"""Notification dispatcher supporting In-App, SMTP Email, and Webhook (Discord/Telegram/Generic) alerts."""

from datetime import timezone
from email.message import EmailMessage
import hmac
import hashlib
import json
import logging
import smtplib
import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import MonitoredSite, Notification, PageSnapshot

logger = logging.getLogger(__name__)


def _build_dedup_key(channel: str, site_id: int, content_hash: str) -> str:
    """Create a deterministic deduplication key for a site + content hash + channel."""
    return f"{channel}:{site_id}:{content_hash}"


def _notification_exists(db: Session, dedup_key: str) -> bool:
    """Check whether a notification with the same dedup_key was already recorded."""
    existing = (
        db.query(Notification.id)
        .filter(
            Notification.dedup_key == dedup_key,
            Notification.delivery_status == "delivered",
        )
        .first()
    )
    return existing is not None


def _send_smtp_email(settings: Settings, subject: str, body: str) -> None:
    """Send an email notification using configured SMTP settings."""
    if not settings.smtp_host or not settings.smtp_from_email or not settings.smtp_to_email:
        raise ValueError("Incomplete SMTP configuration (SMTP_HOST, SMTP_FROM_EMAIL, SMTP_TO_EMAIL required).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_email
    msg["To"] = settings.smtp_to_email
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


async def _send_webhook_payload(
    settings: Settings,
    site: MonitoredSite,
    snapshot: PageSnapshot,
    title: str,
    message: str,
    matched_keywords: list[str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Send a JSON payload compatible with Discord, Telegram, Slack, and custom webhooks."""
    webhook_url = (settings.webhook_url or "").strip()
    if not webhook_url:
        raise ValueError("WEBHOOK_URL is not configured.")

    payload: dict = {
        "event": "website_change_detected",
        # Discord & Slack compatibility fields
        "content": f"**{title}**\n{message}\nURL: {site.url}",
        "text": f"{title}\n{message}\nURL: {site.url}",
        # Structured fields for generic webhooks
        "site": {
            "id": site.id,
            "name": site.name,
            "url": site.url,
        },
        "snapshot": {
            "id": snapshot.id,
            "version_number": snapshot.version_number,
            "content_hash": snapshot.content_hash,
            "change_summary": snapshot.change_summary,
            "created_at": (
                snapshot.created_at.astimezone(timezone.utc).isoformat()
                if snapshot.created_at
                else None
            ),
        },
        "matched_keywords": matched_keywords,
    }

    if settings.telegram_chat_id:
        payload["chat_id"] = settings.telegram_chat_id

    body_bytes = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": settings.user_agent}

    if settings.webhook_secret:
        signature = hmac.new(
            settings.webhook_secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        headers["X-Webhook-Signature-256"] = f"sha256={signature}"

    async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
        response = await client.post(webhook_url, content=body_bytes, headers=headers)
        response.raise_for_status()


async def dispatch_change_notifications(
    db: Session,
    site: MonitoredSite,
    snapshot: PageSnapshot,
    matched_keywords: list[str],
    *,
    settings: Settings | None = None,
    webhook_transport: httpx.AsyncBaseTransport | None = None,
) -> list[Notification]:
    """Evaluate alert conditions, prevent duplicate alerts, and dispatch notifications.

    Returns the list of Notification database records created during this invocation.
    """
    cfg = settings or get_settings()
    created_records: list[Notification] = []

    configured_keywords = [kw.strip() for kw in (site.keywords or "").split(",") if kw.strip()]

    # Bonus & core feature: if keyword_only_alerts is enabled and site has keywords,
    # skip alerting when no configured keyword matched the change.
    if site.keyword_only_alerts and configured_keywords and not matched_keywords:
        return created_records

    trigger_type = "keyword_match" if matched_keywords else "content_change"
    keywords_str = ", ".join(matched_keywords) if matched_keywords else None

    if matched_keywords:
        title = f"Keyword Alert on {site.name}: {', '.join(matched_keywords)}"
    else:
        title = f"Change Detected on {site.name} (v{snapshot.version_number})"

    summary_text = snapshot.change_summary or "Meaningful page content changed."
    message = (
        f"Website '{site.name}' ({site.url}) updated to version #{snapshot.version_number}. "
        f"Summary: {summary_text}"
    )
    if matched_keywords:
        message += f" | Matched keywords: {', '.join(matched_keywords)}."

    # 1. Always record In-App notification (with deduplication)
    in_app_key = _build_dedup_key("in_app", site.id, snapshot.content_hash)
    if not _notification_exists(db, in_app_key):
        in_app_notif = Notification(
            site_id=site.id,
            snapshot_id=snapshot.id,
            channel="in_app",
            trigger_type=trigger_type,
            matched_keywords=keywords_str,
            title=title,
            message=message,
            delivery_status="delivered",
            dedup_key=in_app_key,
        )
        db.add(in_app_notif)
        created_records.append(in_app_notif)

    # 2. Optional Email Notification
    if cfg.smtp_enabled:
        email_key = _build_dedup_key("email", site.id, snapshot.content_hash)
        if not _notification_exists(db, email_key):
            try:
                _send_smtp_email(cfg, title, message)
                email_notif = Notification(
                    site_id=site.id,
                    snapshot_id=snapshot.id,
                    channel="email",
                    trigger_type=trigger_type,
                    matched_keywords=keywords_str,
                    title=title,
                    message=message,
                    delivery_status="delivered",
                    dedup_key=email_key,
                )
            except Exception as exc:
                logger.warning("Email notification delivery failed for site %s: %s", site.id, exc)
                email_notif = Notification(
                    site_id=site.id,
                    snapshot_id=snapshot.id,
                    channel="email",
                    trigger_type=trigger_type,
                    matched_keywords=keywords_str,
                    title=title,
                    message=message,
                    delivery_status="failed",
                    error_detail=str(exc)[:500],
                    dedup_key=email_key,
                )
            db.add(email_notif)
            created_records.append(email_notif)

    # 3. Optional Webhook Notification (Discord / Telegram / Generic)
    if cfg.webhook_enabled:
        webhook_key = _build_dedup_key("webhook", site.id, snapshot.content_hash)
        if not _notification_exists(db, webhook_key):
            try:
                await _send_webhook_payload(
                    cfg,
                    site,
                    snapshot,
                    title,
                    message,
                    matched_keywords,
                    transport=webhook_transport,
                )
                webhook_notif = Notification(
                    site_id=site.id,
                    snapshot_id=snapshot.id,
                    channel="webhook",
                    trigger_type=trigger_type,
                    matched_keywords=keywords_str,
                    title=title,
                    message=message,
                    delivery_status="delivered",
                    dedup_key=webhook_key,
                )
            except Exception as exc:
                logger.warning("Webhook notification delivery failed for site %s: %s", site.id, exc)
                webhook_notif = Notification(
                    site_id=site.id,
                    snapshot_id=snapshot.id,
                    channel="webhook",
                    trigger_type=trigger_type,
                    matched_keywords=keywords_str,
                    title=title,
                    message=message,
                    delivery_status="failed",
                    error_detail=str(exc)[:500],
                    dedup_key=webhook_key,
                )
            db.add(webhook_notif)
            created_records.append(webhook_notif)

    db.commit()
    for record in created_records:
        db.refresh(record)

    return created_records
