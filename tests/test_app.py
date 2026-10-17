"""Comprehensive automated tests for Website Change Detector."""

import asyncio
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base, get_db
from app.main import app
from app.models import CheckRun, MonitoredSite, Notification, PageSnapshot
from app.services.differ import compute_diff
from app.services.extractor import extract_meaningful_content
from app.services.monitor import MonitoringError, reset_monitor_state, run_site_check
from app.services.notifier import dispatch_change_notifications
from app.services.security import SSRFValidationError, validate_public_url


# ---------------------------------------------------------------------------
# Test Database & Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session() -> Session:
    """Provide an isolated in-memory SQLite database session with foreign keys enabled."""
    reset_monitor_state()
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(test_engine, "connect")
    def _enable_fk(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    """Provide a FastAPI TestClient wired to the isolated in-memory database."""
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def test_settings() -> Settings:
    """Fast, deterministic settings for unit tests."""
    return Settings(
        scheduler_enabled=False,
        request_timeout_seconds=2.0,
        max_retries=1,
        retry_backoff_factor=0.01,
        host_min_interval_seconds=0.0,
        respect_robots_txt=True,
        smtp_enabled=False,
        webhook_enabled=False,
    )


def make_mock_transport(handler) -> httpx.MockTransport:
    """Wrap a request handler function into an httpx.MockTransport."""
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 1. API Health Check & Frontend Views
# ---------------------------------------------------------------------------

def test_api_health_check(client: TestClient):
    """Verify GET /api/health returns service status and database health."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "healthy"
    assert body["monitored_sites_count"] == 0
    assert "timestamp" in body


def test_dashboard_and_stats_endpoints(client: TestClient):
    """Verify HTML dashboard and /api/stats endpoint load cleanly."""
    html_resp = client.get("/")
    assert html_resp.status_code == 200
    assert "Website Change Detector" in html_resp.text

    stats_resp = client.get("/api/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["total_sites"] == 0
    assert len(stats["timeline"]) == 7


# ---------------------------------------------------------------------------
# 2. Website Creation, Validation, Invalid URL & SSRF Rejection, Intervals
# ---------------------------------------------------------------------------

def test_create_list_get_and_delete_website(client: TestClient, db_session: Session):
    """Verify creating, fetching, listing, and cascade-deleting a monitored website."""
    payload = {
        "name": "Example Releases",
        "url": "https://example.com/releases#section-1",
        "interval_minutes": 30,
        "keywords": "security, patch, v2.0",
        "keyword_only_alerts": False,
        "css_selector": "main article",
        "ignore_selector": ".advertisement",
    }
    create_resp = client.post("/api/sites", json=payload)
    assert create_resp.status_code == 201
    created = create_resp.json()
    site_id = created["id"]
    assert created["name"] == "Example Releases"
    # URL fragment (#section-1) is normalized away
    assert created["url"] == "https://example.com/releases"
    assert created["interval_minutes"] == 30
    assert created["keywords"] == "security, patch, v2.0"
    assert created["status"] == "pending"

    # List sites
    list_resp = client.get("/api/sites")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    # Get site details
    detail_resp = client.get(f"/api/sites/{site_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == site_id

    # History HTML page renders
    hist_page = client.get(f"/sites/{site_id}/history")
    assert hist_page.status_code == 200
    assert "Example Releases" in hist_page.text

    # Delete site
    del_resp = client.delete(f"/api/sites/{site_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Confirm 404 after deletion
    assert client.get(f"/api/sites/{site_id}").status_code == 404


@pytest.mark.parametrize(
    "bad_url",
    [
        "not-a-url",
        "ftp://example.com/file.txt",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://localhost:8000/admin",
        "http://127.0.0.1/secret",
        "http://127.0.1.1:8080/",
        "http://0.0.0.0/",
        "http://10.0.0.1/internal",
        "http://172.16.0.10/router",
        "http://192.168.1.1/setup",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/",
        "http://intranet.local/dashboard",
        "http://service.internal/health",
        "https://user:password@example.com/private",
    ],
)
def test_invalid_and_ssrf_urls_rejected(client: TestClient, bad_url: str):
    """Verify malformed, non-HTTP, credentialed, localhost, and private IP URLs are rejected."""
    resp = client.post(
        "/api/sites",
        json={
            "name": "Blocked Target",
            "url": bad_url,
            "interval_minutes": 60,
        },
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("invalid_interval", [0, 5, 14, -10, 1441, 10000])
def test_check_interval_validation(client: TestClient, invalid_interval: int):
    """Verify check intervals outside 15..1440 minutes are rejected."""
    resp = client.post(
        "/api/sites",
        json={
            "name": "Interval Test",
            "url": "https://example.com/status",
            "interval_minutes": invalid_interval,
        },
    )
    assert resp.status_code == 422

    # Valid boundary values (15 and 1440) succeed
    for valid_interval in (15, 1440):
        ok_resp = client.post(
            "/api/sites",
            json={
                "name": f"Valid {valid_interval}",
                "url": "https://example.com/status",
                "interval_minutes": valid_interval,
            },
        )
        assert ok_resp.status_code == 201


# ---------------------------------------------------------------------------
# 3. Initial Snapshot, Genuine Changes, Duplicate Prevention & Noise Filtering
# ---------------------------------------------------------------------------

def test_initial_snapshot_and_duplicate_snapshot_prevention(
    db_session: Session, test_settings: Settings
):
    """Verify initial check creates v1 without alerts, and unchanged check does not create v2."""
    site = MonitoredSite(
        name="Product Docs",
        url="https://example.com/docs",
        interval_minutes=60,
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)

    html_v1 = """
    <html>
      <head><title>Docs v1</title></head>
      <body>
        <main>
          <h1>Documentation Portal</h1>
          <p>Welcome to version 1.0 of our public API documentation.</p>
        </main>
      </body>
    </html>
    """

    transport = make_mock_transport(
        lambda request: httpx.Response(200, text=html_v1, headers={"content-type": "text/html"})
    )

    # 1. Initial check
    outcome1 = asyncio.run(
        run_site_check(db_session, site, settings=test_settings, transport=transport)
    )
    assert outcome1.snapshot_created is True
    assert outcome1.snapshot is not None
    assert outcome1.snapshot.version_number == 1
    assert outcome1.check_run.status == "initial"
    assert outcome1.site.status == "unchanged"
    # Initial snapshot must NOT generate false change alerts
    assert len(outcome1.notifications) == 0

    # 2. Second check with identical meaningful content
    outcome2 = asyncio.run(
        run_site_check(db_session, site, settings=test_settings, transport=transport)
    )
    assert outcome2.snapshot_created is False
    assert outcome2.snapshot is None
    assert outcome2.check_run.status == "unchanged"

    # Only 1 snapshot exists in DB
    total_snaps = db_session.query(PageSnapshot).filter(PageSnapshot.site_id == site.id).count()
    assert total_snaps == 1


def test_detect_genuine_content_change_and_ignore_noise(
    db_session: Session, test_settings: Settings
):
    """Verify noisy changes are ignored while genuine content changes create v2 and alerts."""
    site = MonitoredSite(
        name="Pricing Page",
        url="https://example.com/pricing",
        interval_minutes=30,
        css_selector="#pricing-container",
        ignore_selector=".promo-banner",
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)

    base_html = """
    <html>
      <head>
        <title>Pricing</title>
        <script>console.log("random_nonce_11111");</script>
        <style>.plan { color: navy; }</style>
      </head>
      <body>
        <nav>Navigation Menu link 1</nav>
        <div id="pricing-container">
          <!-- Render timestamp: 2026-10-17T00:01:02Z -->
          <h2>Pro Plan</h2>
          <p>Price: $29 per month</p>
          <span class="timestamp">Updated 2 minutes ago</span>
          <div class="view-count">1,024 views</div>
          <div class="promo-banner">Flash sale ends in 12s!</div>
        </div>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """

    # Noisy variation: different script nonce, comment, nav, timestamp, view count, and ignored promo-banner
    noisy_html = """
    <html>
      <head>
        <title>Pricing</title>
        <script>console.log("random_nonce_99999");</script>
      </head>
      <body>
        <nav>Navigation Menu link CHANGED</nav>
        <div id="pricing-container">
          <!-- Render timestamp: 2026-10-17T04:55:19Z -->
          <h2>Pro Plan</h2>
          <p>   Price:   $29 per month   </p>
          <span class="timestamp">Updated 45 seconds ago</span>
          <div class="view-count">9,876 views</div>
          <div class="promo-banner">COMPLETELY DIFFERENT IGNORED PROMO</div>
        </div>
        <footer>Updated Footer 2026</footer>
      </body>
    </html>
    """

    # Genuine change inside #pricing-container
    genuine_change_html = """
    <html>
      <body>
        <div id="pricing-container">
          <h2>Pro Plan</h2>
          <p>Price: $39 per month</p>
          <p>Includes unlimited team seats.</p>
        </div>
      </body>
    </html>
    """

    current_html = {"body": base_html}
    transport = make_mock_transport(
        lambda req: httpx.Response(200, text=current_html["body"], headers={"content-type": "text/html"})
    )

    # Check 1: Initial baseline
    out1 = asyncio.run(run_site_check(db_session, site, settings=test_settings, transport=transport))
    assert out1.snapshot_created is True
    assert out1.snapshot.version_number == 1

    # Check 2: Noisy HTML should produce the exact same content hash and NO new snapshot
    current_html["body"] = noisy_html
    out2 = asyncio.run(run_site_check(db_session, site, settings=test_settings, transport=transport))
    assert out2.snapshot_created is False
    assert out2.check_run.status == "unchanged"
    assert out2.check_run.content_hash == out1.snapshot.content_hash

    # Check 3: Genuine change inside #pricing-container creates v2 and triggers alert
    current_html["body"] = genuine_change_html
    out3 = asyncio.run(run_site_check(db_session, site, settings=test_settings, transport=transport))
    assert out3.snapshot_created is True
    assert out3.snapshot.version_number == 2
    assert out3.check_run.status == "changed"
    assert out3.site.status == "changed"
    assert out3.site.last_changed_at is not None
    assert "$39 per month" in out3.snapshot.change_summary
    assert len(out3.notifications) == 1
    assert out3.notifications[0].channel == "in_app"
    assert out3.notifications[0].delivery_status == "delivered"


# ---------------------------------------------------------------------------
# 4. Failed Requests, Timeouts, Redirect SSRF Revalidation, and Robots.txt
# ---------------------------------------------------------------------------

def test_failed_requests_and_timeout_handling(db_session: Session, test_settings: Settings):
    """Verify HTTP 500 errors and timeouts are recorded gracefully without crashing."""
    site = MonitoredSite(
        name="Unreliable Endpoint",
        url="https://example.com/flaky",
        interval_minutes=60,
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)

    # 1. Simulate HTTP 503 Server Error
    transport_503 = make_mock_transport(
        lambda req: httpx.Response(503, text="Service Unavailable")
    )
    out_503 = asyncio.run(
        run_site_check(db_session, site, settings=test_settings, transport=transport_503)
    )
    assert out_503.snapshot_created is False
    assert out_503.check_run.status == "failed"
    assert out_503.check_run.http_status_code == 503
    assert out_503.check_run.error_type == "http_5xx"
    assert out_503.site.status == "error"
    assert out_503.site.consecutive_failures == 1

    # 2. Simulate Network Timeout
    def _timeout_handler(request: httpx.Request):
        raise httpx.ReadTimeout("Read timed out", request=request)

    transport_timeout = make_mock_transport(_timeout_handler)
    out_timeout = asyncio.run(
        run_site_check(db_session, site, settings=test_settings, transport=transport_timeout)
    )
    assert out_timeout.snapshot_created is False
    assert out_timeout.check_run.status == "failed"
    assert out_timeout.check_run.error_type == "timeout"
    assert out_timeout.site.status == "error"
    assert out_timeout.site.consecutive_failures == 2


def test_redirect_to_internal_ip_is_blocked_by_ssrf_guard(
    db_session: Session, test_settings: Settings
):
    """Verify a public URL redirecting to 127.0.0.1 or 169.254.169.254 is blocked mid-flight."""
    site = MonitoredSite(
        name="Redirect Trap",
        url="https://example.com/redirect-me",
        interval_minutes=60,
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)

    def _redirect_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data/"},
        )

    outcome = asyncio.run(
        run_site_check(
            db_session,
            site,
            settings=test_settings,
            transport=make_mock_transport(_redirect_handler),
        )
    )
    assert outcome.check_run.status == "failed"
    assert outcome.check_run.error_type == "ssrf_redirect_blocked"
    assert "SSRF" in (outcome.check_run.error_message or "")


def test_robots_txt_disallow_respected(db_session: Session, test_settings: Settings):
    """Verify robots.txt Disallow rules prevent scraping disallowed paths."""
    site = MonitoredSite(
        name="Robots Protected",
        url="https://example.com/private-section/page",
        interval_minutes=60,
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)

    def _robots_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /private-section/\n",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(200, text="<html><body>Should not be fetched</body></html>")

    outcome = asyncio.run(
        run_site_check(
            db_session,
            site,
            settings=test_settings,
            transport=make_mock_transport(_robots_handler),
        )
    )
    assert outcome.check_run.status == "failed"
    assert outcome.check_run.error_type == "robots_disallowed"


# ---------------------------------------------------------------------------
# 5. Diff Generation & API Diff Endpoint
# ---------------------------------------------------------------------------

def test_diff_generation_and_api_endpoint(
    client: TestClient, db_session: Session, test_settings: Settings
):
    """Verify unified diff, side-by-side comparison, and no-change messages."""
    site = MonitoredSite(
        name="Changelog Page",
        url="https://example.com/changelog",
        interval_minutes=60,
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)

    pages = [
        "<html><body><h1>Changelog</h1><p>v1.0 Initial Release</p><p>Bug fixes</p></body></html>",
        "<html><body><h1>Changelog</h1><p>v1.1 Security Update</p><p>Bug fixes</p><p>Added RSS support</p></body></html>",
    ]
    idx = {"i": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = pages[min(idx["i"], len(pages) - 1)]
        idx["i"] += 1
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    transport = make_mock_transport(_handler)
    out1 = asyncio.run(run_site_check(db_session, site, settings=test_settings, transport=transport))
    out2 = asyncio.run(run_site_check(db_session, site, settings=test_settings, transport=transport))

    # Query /api/sites/{id}/diff between v1 and v2
    diff_resp = client.get(
        f"/api/sites/{site.id}/diff?from_id={out1.snapshot.id}&to_id={out2.snapshot.id}"
    )
    assert diff_resp.status_code == 200
    diff_data = diff_resp.json()
    assert diff_data["stats"]["has_meaningful_changes"] is True
    assert diff_data["stats"]["added_lines"] >= 2
    assert diff_data["stats"]["removed_lines"] >= 1
    assert any(line["line_type"] == "added" for line in diff_data["unified_diff"])
    assert any(line["line_type"] == "removed" for line in diff_data["unified_diff"])
    assert any(row["right_type"] == "insert" for row in diff_data["side_by_side"])
    assert any(row["left_type"] == "delete" for row in diff_data["side_by_side"])

    # Compare v1 against v1 -> clear no-changes message
    same_resp = client.get(
        f"/api/sites/{site.id}/diff?from_id={out1.snapshot.id}&to_id={out1.snapshot.id}"
    )
    assert same_resp.status_code == 200
    same_data = same_resp.json()
    assert same_data["stats"]["has_meaningful_changes"] is False
    assert "No meaningful changes" in same_data["stats"]["summary"]

    # Verify RSS feed endpoint works for this site
    rss_resp = client.get(f"/api/sites/{site.id}/rss")
    assert rss_resp.status_code == 200
    assert "<rss version=\"2.0\">" in rss_resp.text
    assert "Changelog Page" in rss_resp.text


# ---------------------------------------------------------------------------
# 6. Keyword Alerts, Keyword-Only Filtering & Duplicate Alert Prevention
# ---------------------------------------------------------------------------

def test_keyword_alerts_and_duplicate_notification_prevention(
    db_session: Session,
):
    """Verify keyword alerts, keyword-only mode, webhook dispatch, and deduplication."""
    webhook_calls: list[dict] = []

    def _webhook_handler(request: httpx.Request) -> httpx.Response:
        webhook_calls.append({"url": str(request.url), "body": request.content.decode("utf-8")})
        return httpx.Response(200, json={"ok": True})

    webhook_transport = make_mock_transport(_webhook_handler)

    cfg = Settings(
        scheduler_enabled=False,
        host_min_interval_seconds=0.0,
        respect_robots_txt=False,
        webhook_enabled=True,
        webhook_url="https://hooks.example.com/notify",
        webhook_secret="test-secret-key",
    )

    site = MonitoredSite(
        name="Security Advisories",
        url="https://example.com/advisories",
        interval_minutes=15,
        keywords="CVE, critical, patch",
        keyword_only_alerts=True,
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)

    html_seq = [
        "<html><body><h1>Advisories</h1><p>All systems normal.</p></body></html>",
        "<html><body><h1>Advisories</h1><p>Minor documentation typo fixed.</p></body></html>",
        "<html><body><h1>Advisories</h1><p>Urgent: CVE-2026-1234 critical patch released.</p></body></html>",
    ]
    step = {"n": 0}

    page_transport = make_mock_transport(
        lambda req: httpx.Response(
            200,
            text=html_seq[min(step["n"], len(html_seq) - 1)],
            headers={"content-type": "text/html"},
        )
    )

    # Step 0: Initial baseline -> no alert
    step["n"] = 0
    out0 = asyncio.run(
        run_site_check(
            db_session,
            site,
            settings=cfg,
            transport=page_transport,
            webhook_transport=webhook_transport,
        )
    )
    assert out0.snapshot_created is True
    assert len(out0.notifications) == 0

    # Step 1: Change without matching keywords while keyword_only_alerts=True -> snapshot v2 saved, 0 alerts
    step["n"] = 1
    out1 = asyncio.run(
        run_site_check(
            db_session,
            site,
            settings=cfg,
            transport=page_transport,
            webhook_transport=webhook_transport,
        )
    )
    assert out1.snapshot_created is True
    assert out1.snapshot.version_number == 2
    assert len(out1.notifications) == 0
    assert len(webhook_calls) == 0

    # Step 2: Change WITH matching keywords ("CVE", "critical", "patch") -> triggers in_app + webhook alert
    step["n"] = 2
    out2 = asyncio.run(
        run_site_check(
            db_session,
            site,
            settings=cfg,
            transport=page_transport,
            webhook_transport=webhook_transport,
        )
    )
    assert out2.snapshot_created is True
    assert out2.snapshot.version_number == 3
    assert "CVE" in (out2.snapshot.matched_keywords or "")
    assert len(out2.notifications) == 2  # in_app + webhook
    assert len(webhook_calls) == 1

    # Verify duplicate alert prevention: calling dispatch_change_notifications again for the same snapshot
    # must NOT create duplicate notifications or send duplicate webhooks
    dup_notifs = asyncio.run(
        dispatch_change_notifications(
            db_session,
            site,
            out2.snapshot,
            ["CVE", "critical", "patch"],
            settings=cfg,
            webhook_transport=webhook_transport,
        )
    )
    assert len(dup_notifs) == 0
    assert len(webhook_calls) == 1

    # Verify cascade delete removes snapshots, check runs, and notifications for this site
    db_session.delete(site)
    db_session.commit()
    assert db_session.query(PageSnapshot).count() == 0
    assert db_session.query(CheckRun).count() == 0
    assert db_session.query(Notification).count() == 0
