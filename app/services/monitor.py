"""Core Website Monitoring Engine with SSRF redirect protection, rate limiting, and robots.txt compliance."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import CheckRun, MonitoredSite, Notification, PageSnapshot
from app.services.differ import compute_diff
from app.services.extractor import (
    ExtractionError,
    ExtractedContent,
    extract_meaningful_content,
    match_keywords_in_change,
)
from app.services.notifier import dispatch_change_notifications
from app.services.security import SSRFValidationError, validate_public_url

logger = logging.getLogger(__name__)


class MonitoringError(Exception):
    """Structured error raised during webpage fetching or validation."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "fetch_error",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code


@dataclass
class FetchResult:
    """Result of fetching a public webpage."""

    final_url: str
    status_code: int
    html: str
    duration_ms: int


@dataclass
class CheckOutcome:
    """Complete result of executing a monitoring check on a MonitoredSite."""

    site: MonitoredSite
    check_run: CheckRun
    snapshot_created: bool
    snapshot: PageSnapshot | None
    notifications: list[Notification]
    message: str


# In-memory synchronization primitives to prevent duplicate concurrent checks and enforce rate limits
_site_locks: dict[int, asyncio.Lock] = {}
_url_locks: dict[str, asyncio.Lock] = {}
_host_locks: dict[str, asyncio.Lock] = {}
_host_last_request_ts: dict[str, float] = {}
_robots_cache: dict[str, tuple[float, RobotFileParser | None]] = {}

ROBOTS_CACHE_TTL_SECONDS = 3600.0

CAPTCHA_INDICATORS = (
    "cf-chl-bypass",
    "cf-turnstile",
    "g-recaptcha",
    "hcaptcha-box",
    "please verify you are a human",
    "attention required! | cloudflare",
)


def reset_monitor_state() -> None:
    """Clear in-memory rate limiters and caches (used by automated tests)."""
    _site_locks.clear()
    _url_locks.clear()
    _host_locks.clear()
    _host_last_request_ts.clear()
    _robots_cache.clear()


def _get_site_lock(site_id: int) -> asyncio.Lock:
    if site_id not in _site_locks:
        _site_locks[site_id] = asyncio.Lock()
    return _site_locks[site_id]


def _get_url_lock(url: str) -> asyncio.Lock:
    if url not in _url_locks:
        _url_locks[url] = asyncio.Lock()
    return _url_locks[url]


def _get_host_lock(hostname: str) -> asyncio.Lock:
    if hostname not in _host_locks:
        _host_locks[hostname] = asyncio.Lock()
    return _host_locks[hostname]


async def _respect_host_rate_limit(hostname: str, min_interval: float) -> None:
    """Ensure consecutive requests to the same hostname respect `min_interval` seconds."""
    if min_interval <= 0:
        return
    host_lock = _get_host_lock(hostname)
    async with host_lock:
        now = time.monotonic()
        last_ts = _host_last_request_ts.get(hostname)
        if last_ts is not None:
            elapsed = now - last_ts
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
        _host_last_request_ts[hostname] = time.monotonic()


async def _check_robots_txt(
    url: str,
    client: httpx.AsyncClient,
    user_agent: str,
) -> bool:
    """Check whether `url` is allowed by the origin's robots.txt policy."""
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    now = time.monotonic()

    cached = _robots_cache.get(origin)
    if cached is not None and (now - cached[0]) < ROBOTS_CACHE_TTL_SECONDS:
        parser = cached[1]
        return True if parser is None else parser.can_fetch(user_agent, url)

    robots_url = f"{origin}/robots.txt"
    try:
        resp = await client.get(
            robots_url,
            headers={"User-Agent": user_agent},
            follow_redirects=False,
            timeout=5.0,
        )
        if resp.status_code == 200 and "text" in (resp.headers.get("content-type", "").lower()):
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
            _robots_cache[origin] = (now, parser)
            return parser.can_fetch(user_agent, url)
    except Exception:
        # If robots.txt is unreachable or absent, default to permissive standard behavior
        pass

    _robots_cache[origin] = (now, None)
    return True


def _detect_access_gate_or_captcha(status_code: int, html: str) -> None:
    """Refuse to bypass authentication walls, 401/403 restrictions, or CAPTCHAs."""
    if status_code in (401, 403, 407):
        raise MonitoringError(
            f"Access denied by target server (HTTP {status_code}). "
            "Authenticated or restricted pages are not monitored.",
            error_type="access_restricted",
            status_code=status_code,
        )

    lower_html = (html or "")[:15000].lower()
    if status_code in (403, 429, 503):
        for indicator in CAPTCHA_INDICATORS:
            if indicator in lower_html:
                raise MonitoringError(
                    "Target webpage presented a CAPTCHA or anti-bot challenge. "
                    "Bypassing access protections is not permitted.",
                    error_type="captcha_detected",
                    status_code=status_code,
                )


async def _fetch_with_playwright_fallback(url: str, settings: Settings) -> str:
    """Optional Playwright headless rendering fallback for JS-heavy public pages."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise MonitoringError(
            "Playwright is not available in the current environment.",
            error_type="playwright_unavailable",
        ) from exc

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(user_agent=settings.user_agent)
                page = await context.new_page()

                # Intercept and block non-public requests inside Playwright
                async def _route_guard(route):
                    try:
                        validate_public_url(route.request.url, require_dns_resolution=False)
                        await route.continue_()
                    except SSRFValidationError:
                        await route.abort()

                await page.route("**/*", _route_guard)
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=settings.playwright_timeout_ms,
                )
                return await page.content()
            finally:
                await browser.close()
    except Exception as exc:
        raise MonitoringError(
            f"Playwright rendering fallback failed: {exc}",
            error_type="playwright_error",
        ) from exc


async def fetch_public_webpage(
    url: str,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    use_playwright: bool = False,
) -> FetchResult:
    """Safely fetch a public HTTP/HTTPS webpage with SSRF revalidation, redirect limits, and retries."""
    cfg = settings or get_settings()
    start_perf = time.perf_counter()

    # Validate initial destination (require DNS resolution only on live network calls)
    require_dns = transport is None
    try:
        current_url = validate_public_url(url, require_dns_resolution=require_dns)
    except SSRFValidationError as exc:
        raise MonitoringError(str(exc), error_type="ssrf_blocked") from exc

    headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }

    timeout = httpx.Timeout(cfg.request_timeout_seconds)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        transport=transport,
        headers=headers,
    ) as client:
        # Check robots.txt on initial origin if enabled
        if cfg.respect_robots_txt:
            allowed = await _check_robots_txt(current_url, client, cfg.user_agent)
            if not allowed:
                raise MonitoringError(
                    f"Monitoring disallowed by target site's robots.txt policy ({current_url}).",
                    error_type="robots_disallowed",
                )

        last_exc: MonitoringError | None = None
        max_attempts = max(1, cfg.max_retries + 1)

        for attempt in range(max_attempts):
            redirect_url = current_url
            try:
                for redirect_hop in range(cfg.max_redirects + 1):
                    parsed_hop = urlsplit(redirect_url)
                    hostname = (parsed_hop.hostname or "").lower()

                    if transport is None:
                        await _respect_host_rate_limit(hostname, cfg.host_min_interval_seconds)

                    response = await client.get(redirect_url)

                    # Handle redirects manually to revalidate every hop against SSRF
                    if response.status_code in (301, 302, 303, 307, 308):
                        if redirect_hop >= cfg.max_redirects:
                            raise MonitoringError(
                                f"Exceeded maximum redirect limit ({cfg.max_redirects}).",
                                error_type="too_many_redirects",
                                status_code=response.status_code,
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise MonitoringError(
                                "Redirect response missing Location header.",
                                error_type="invalid_redirect",
                                status_code=response.status_code,
                            )
                        next_target = urljoin(redirect_url, location.strip())
                        try:
                            redirect_url = validate_public_url(
                                next_target, require_dns_resolution=require_dns
                            )
                        except SSRFValidationError as ssrf_exc:
                            raise MonitoringError(
                                f"Redirect blocked by SSRF policy: {ssrf_exc}",
                                error_type="ssrf_redirect_blocked",
                                status_code=response.status_code,
                            ) from ssrf_exc
                        continue

                    # Enforce response size limit
                    raw_bytes = response.content
                    if len(raw_bytes) > cfg.max_response_bytes:
                        raise MonitoringError(
                            f"Response size ({len(raw_bytes)} bytes) exceeds maximum allowed limit "
                            f"({cfg.max_response_bytes} bytes).",
                            error_type="response_too_large",
                            status_code=response.status_code,
                        )

                    html_text = response.text
                    _detect_access_gate_or_captcha(response.status_code, html_text)

                    if response.status_code >= 500:
                        raise MonitoringError(
                            f"Remote server returned HTTP {response.status_code}.",
                            error_type="http_5xx",
                            status_code=response.status_code,
                        )

                    if response.status_code >= 400:
                        raise MonitoringError(
                            f"HTTP error {response.status_code} when fetching webpage.",
                            error_type="http_4xx",
                            status_code=response.status_code,
                        )

                    # Optional Playwright fallback for JS-heavy pages if requested
                    if (use_playwright or cfg.enable_playwright_fallback) and transport is None:
                        if len(html_text.strip()) < 250 or "enable javascript" in html_text.lower():
                            try:
                                html_text = await _fetch_with_playwright_fallback(redirect_url, cfg)
                            except MonitoringError:
                                # Keep static HTML if headless browser is not installed
                                pass

                    duration_ms = max(1, int((time.perf_counter() - start_perf) * 1000))
                    return FetchResult(
                        final_url=redirect_url,
                        status_code=response.status_code,
                        html=html_text,
                        duration_ms=duration_ms,
                    )

            except MonitoringError as mon_err:
                last_exc = mon_err
                # Retry only on transient 5xx server errors
                if mon_err.error_type == "http_5xx" and attempt < max_attempts - 1:
                    backoff = (cfg.retry_backoff_factor ** attempt) * 0.25
                    await asyncio.sleep(backoff)
                    continue
                raise
            except httpx.TimeoutException as timeout_exc:
                last_exc = MonitoringError(
                    f"Request timed out after {cfg.request_timeout_seconds}s: {timeout_exc}",
                    error_type="timeout",
                )
                if attempt < max_attempts - 1:
                    backoff = (cfg.retry_backoff_factor ** attempt) * 0.25
                    await asyncio.sleep(backoff)
                    continue
                raise last_exc from timeout_exc
            except httpx.RequestError as req_exc:
                last_exc = MonitoringError(
                    f"Network request failed: {req_exc}",
                    error_type="network_error",
                )
                if attempt < max_attempts - 1:
                    backoff = (cfg.retry_backoff_factor ** attempt) * 0.25
                    await asyncio.sleep(backoff)
                    continue
                raise last_exc from req_exc

        if last_exc:
            raise last_exc
        raise MonitoringError("Failed to fetch webpage after retries.", error_type="fetch_error")


async def run_site_check(
    db: Session,
    site: MonitoredSite,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    webhook_transport: httpx.AsyncBaseTransport | None = None,
) -> CheckOutcome:
    """Execute a complete monitoring check for a single `MonitoredSite`.

    - Prevents duplicate concurrent checks on the same site or URL.
    - Fetches the webpage safely and extracts meaningful content.
    - Saves initial snapshot without firing false alerts.
    - Saves subsequent snapshots only when the SHA-256 content hash changes.
    - Triggers notifications and records structured check run telemetry.
    """
    cfg = settings or get_settings()
    site_lock = _get_site_lock(site.id)
    url_lock = _get_url_lock(site.url)

    if site_lock.locked() or url_lock.locked():
        raise MonitoringError(
            "A check is already in progress for this website.",
            error_type="concurrent_check_in_progress",
            status_code=409,
        )

    async with site_lock, url_lock:
        start_perf = time.perf_counter()
        now = datetime.now(timezone.utc)

        try:
            fetch_result = await fetch_public_webpage(
                site.url,
                settings=cfg,
                transport=transport,
                use_playwright=site.use_playwright,
            )
            extracted: ExtractedContent = extract_meaningful_content(
                fetch_result.html,
                css_selector=site.css_selector,
                ignore_selector=site.ignore_selector,
            )
        except (MonitoringError, ExtractionError) as exc:
            duration_ms = max(1, int((time.perf_counter() - start_perf) * 1000))
            if isinstance(exc, MonitoringError):
                err_type = exc.error_type
                err_msg = exc.message
                http_code = exc.status_code
            else:
                err_type = "extraction_error"
                err_msg = str(exc)
                http_code = None

            check_run = CheckRun(
                site_id=site.id,
                status="failed",
                http_status_code=http_code,
                duration_ms=duration_ms,
                error_type=err_type,
                error_message=err_msg,
                checked_at=now,
            )
            site.status = "error"
            site.last_checked_at = now
            site.last_status_code = http_code
            site.last_check_duration_ms = duration_ms
            site.last_error = err_msg
            site.consecutive_failures = (site.consecutive_failures or 0) + 1
            site.updated_at = now

            db.add(check_run)
            db.commit()
            db.refresh(site)
            db.refresh(check_run)

            return CheckOutcome(
                site=site,
                check_run=check_run,
                snapshot_created=False,
                snapshot=None,
                notifications=[],
                message=f"Check failed: {err_msg}",
            )

        # Fetch latest snapshot for this site
        latest_snapshot: PageSnapshot | None = (
            db.query(PageSnapshot)
            .filter(PageSnapshot.site_id == site.id)
            .order_by(PageSnapshot.version_number.desc())
            .first()
        )

        # Case 1: Initial snapshot (first successful check)
        if latest_snapshot is None:
            initial_snapshot = PageSnapshot(
                site_id=site.id,
                version_number=1,
                content_hash=extracted.content_hash,
                extracted_text=extracted.normalized_text,
                title_text=extracted.title or site.name,
                change_summary="Initial baseline snapshot captured.",
                matched_keywords=None,
                char_count=extracted.char_count,
                word_count=extracted.word_count,
                created_at=now,
            )
            db.add(initial_snapshot)
            db.flush()

            check_run = CheckRun(
                site_id=site.id,
                status="initial",
                http_status_code=fetch_result.status_code,
                duration_ms=fetch_result.duration_ms,
                content_hash=extracted.content_hash,
                snapshot_id=initial_snapshot.id,
                checked_at=now,
            )
            db.add(check_run)

            site.status = "unchanged"
            site.last_checked_at = now
            site.last_status_code = fetch_result.status_code
            site.last_check_duration_ms = fetch_result.duration_ms
            site.last_error = None
            site.last_change_summary = initial_snapshot.change_summary
            site.consecutive_failures = 0
            site.updated_at = now

            db.commit()
            db.refresh(site)
            db.refresh(initial_snapshot)
            db.refresh(check_run)

            return CheckOutcome(
                site=site,
                check_run=check_run,
                snapshot_created=True,
                snapshot=initial_snapshot,
                notifications=[],
                message="Initial baseline snapshot captured without triggering alerts.",
            )

        # Case 2: Subsequent check — no meaningful content change
        if latest_snapshot.content_hash == extracted.content_hash:
            check_run = CheckRun(
                site_id=site.id,
                status="unchanged",
                http_status_code=fetch_result.status_code,
                duration_ms=fetch_result.duration_ms,
                content_hash=extracted.content_hash,
                snapshot_id=latest_snapshot.id,
                checked_at=now,
            )
            db.add(check_run)

            # Preserve 'changed' status if site previously detected changes, otherwise 'unchanged'
            site.status = "changed" if site.last_changed_at is not None else "unchanged"
            site.last_checked_at = now
            site.last_status_code = fetch_result.status_code
            site.last_check_duration_ms = fetch_result.duration_ms
            site.last_error = None
            site.consecutive_failures = 0
            site.updated_at = now

            db.commit()
            db.refresh(site)
            db.refresh(check_run)

            return CheckOutcome(
                site=site,
                check_run=check_run,
                snapshot_created=False,
                snapshot=None,
                notifications=[],
                message="No meaningful content changes detected.",
            )

        # Case 3: Meaningful content change detected!
        diff_result = compute_diff(latest_snapshot.extracted_text, extracted.normalized_text)
        matched_kw = match_keywords_in_change(
            site.keywords,
            extracted.normalized_text,
            added_text_only=diff_result.added_text_blob,
        )
        matched_kw_str = ", ".join(matched_kw) if matched_kw else None

        next_version = latest_snapshot.version_number + 1
        new_snapshot = PageSnapshot(
            site_id=site.id,
            version_number=next_version,
            content_hash=extracted.content_hash,
            extracted_text=extracted.normalized_text,
            title_text=extracted.title or site.name,
            change_summary=diff_result.stats.summary,
            matched_keywords=matched_kw_str,
            char_count=extracted.char_count,
            word_count=extracted.word_count,
            created_at=now,
        )
        db.add(new_snapshot)
        db.flush()

        check_run = CheckRun(
            site_id=site.id,
            status="changed",
            http_status_code=fetch_result.status_code,
            duration_ms=fetch_result.duration_ms,
            content_hash=extracted.content_hash,
            snapshot_id=new_snapshot.id,
            checked_at=now,
        )
        db.add(check_run)

        site.status = "changed"
        site.last_checked_at = now
        site.last_changed_at = now
        site.last_status_code = fetch_result.status_code
        site.last_check_duration_ms = fetch_result.duration_ms
        site.last_error = None
        site.last_change_summary = diff_result.stats.summary
        site.consecutive_failures = 0
        site.updated_at = now

        db.commit()
        db.refresh(site)
        db.refresh(new_snapshot)
        db.refresh(check_run)

        notifications = await dispatch_change_notifications(
            db,
            site,
            new_snapshot,
            matched_kw,
            settings=cfg,
            webhook_transport=webhook_transport,
        )

        return CheckOutcome(
            site=site,
            check_run=check_run,
            snapshot_created=True,
            snapshot=new_snapshot,
            notifications=notifications,
            message=f"Change detected (v{next_version}): {diff_result.stats.summary}",
        )
