"""SQLAlchemy ORM models for Website Change Detector."""

from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


class MonitoredSite(Base):
    """Represents a public webpage configured for periodic change monitoring."""

    __tablename__ = "monitored_sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    # Filtering & extraction rules
    keywords: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    keyword_only_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    css_selector: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ignore_selector: Mapped[str | None] = mapped_column(String(500), nullable=True)
    use_playwright: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Current monitoring status
    # Values: 'pending', 'unchanged', 'changed', 'error'
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_check_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_change_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    snapshots: Mapped[list["PageSnapshot"]] = relationship(
        "PageSnapshot",
        back_populates="site",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="desc(PageSnapshot.version_number)",
    )
    check_runs: Mapped[list["CheckRun"]] = relationship(
        "CheckRun",
        back_populates="site",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="desc(CheckRun.checked_at)",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="site",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="desc(Notification.created_at)",
    )


class PageSnapshot(Base):
    """Stores a normalized content version of a monitored site when content changes."""

    __tablename__ = "page_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("monitored_sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    title_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    change_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    matched_keywords: Mapped[str | None] = mapped_column(String(500), nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    site: Mapped["MonitoredSite"] = relationship("MonitoredSite", back_populates="snapshots")

    __table_args__ = (
        Index("ix_snapshots_site_version", "site_id", "version_number", unique=True),
    )


class CheckRun(Base):
    """Records each individual check attempt, its duration, HTTP code, and any errors."""

    __tablename__ = "check_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("monitored_sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Values: 'initial', 'unchanged', 'changed', 'failed'
    status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("page_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    site: Mapped["MonitoredSite"] = relationship("MonitoredSite", back_populates="check_runs")


class Notification(Base):
    """Stores generated alerts and delivery status across in-app, email, and webhook channels."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("monitored_sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("page_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Channel: 'in_app', 'email', 'webhook'
    channel: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Trigger: 'content_change', 'keyword_match'
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    matched_keywords: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Status: 'delivered', 'skipped', 'failed'
    delivery_status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    site: Mapped["MonitoredSite"] = relationship("MonitoredSite", back_populates="notifications")
