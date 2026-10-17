"""Pydantic request and response schemas for Website Change Detector."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
import soupsieve

from app.services.security import SSRFValidationError, validate_public_url


def _validate_css_selector(selector: str | None, field_label: str) -> str | None:
    """Validate optional CSS selector using soupsieve syntax parser."""
    if selector is None:
        return None
    cleaned = selector.strip()
    if not cleaned:
        return None
    if len(cleaned) > 500:
        raise ValueError(f"{field_label} cannot exceed 500 characters.")
    try:
        soupsieve.compile(cleaned)
    except Exception as exc:
        raise ValueError(f"Invalid {field_label} '{cleaned}': {exc}") from exc
    return cleaned


class SiteCreate(BaseModel):
    """Payload to create a new monitored website."""

    name: str = Field(..., min_length=1, max_length=200, description="Display name for the website")
    url: str = Field(..., min_length=4, max_length=2048, description="Public HTTP or HTTPS URL")
    interval_minutes: int = Field(
        default=60,
        ge=15,
        le=1440,
        description="Check interval in minutes (between 15 minutes and 24 hours / 1440 minutes)",
    )
    keywords: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional comma-separated keywords to watch for in page changes",
    )
    keyword_only_alerts: bool = Field(
        default=False,
        description="If true, only send alerts when a detected change matches configured keywords",
    )
    css_selector: str | None = Field(
        default=None,
        max_length=500,
        description="Optional CSS selector to monitor a specific region of the page",
    )
    ignore_selector: str | None = Field(
        default=None,
        max_length=500,
        description="Optional CSS selector for noisy elements to strip before comparison",
    )
    use_playwright: bool = Field(
        default=False,
        description="Optional fallback to headless Playwright browser for JS-rendered pages",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Website name cannot be blank.")
        return cleaned

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        try:
            return validate_public_url(value, require_dns_resolution=False)
        except SSRFValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = [part.strip() for part in value.split(",") if part.strip()]
        return ", ".join(parts) if parts else None

    @field_validator("css_selector")
    @classmethod
    def validate_target_selector(cls, value: str | None) -> str | None:
        return _validate_css_selector(value, "CSS selector")

    @field_validator("ignore_selector")
    @classmethod
    def validate_ignore_css_selector(cls, value: str | None) -> str | None:
        return _validate_css_selector(value, "ignore CSS selector")


class SnapshotSummary(BaseModel):
    """Lightweight representation of a saved page snapshot for history lists."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    site_id: int
    version_number: int
    content_hash: str
    title_text: str | None = None
    change_summary: str | None = None
    matched_keywords: str | None = None
    char_count: int
    word_count: int
    created_at: datetime


class SnapshotDetail(SnapshotSummary):
    """Full representation of a page snapshot including extracted text."""

    extracted_text: str


class CheckRunResponse(BaseModel):
    """Record of a single monitoring check execution."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    site_id: int
    status: str
    http_status_code: int | None = None
    duration_ms: int
    content_hash: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    snapshot_id: int | None = None
    checked_at: datetime


class SiteResponse(BaseModel):
    """Summary representation of a monitored website."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    interval_minutes: int
    keywords: str | None = None
    keyword_only_alerts: bool = False
    css_selector: str | None = None
    ignore_selector: str | None = None
    use_playwright: bool = False
    status: str
    last_checked_at: datetime | None = None
    last_changed_at: datetime | None = None
    last_status_code: int | None = None
    last_check_duration_ms: int | None = None
    last_error: str | None = None
    last_change_summary: str | None = None
    consecutive_failures: int = 0
    snapshot_count: int = 0
    created_at: datetime
    updated_at: datetime


class SiteDetailResponse(SiteResponse):
    """Detailed representation of a monitored website including recent snapshots and check runs."""

    recent_snapshots: list[SnapshotSummary] = Field(default_factory=list)
    recent_checks: list[CheckRunResponse] = Field(default_factory=list)


class SiteHistoryResponse(BaseModel):
    """Version history response for a monitored website."""

    site: SiteResponse
    snapshots: list[SnapshotSummary]
    recent_checks: list[CheckRunResponse]


class DiffSideRow(BaseModel):
    """Single row in a side-by-side comparison table."""

    left_line_no: int | None = None
    left_text: str = ""
    left_type: str = "equal"  # 'equal', 'delete', 'replace', 'empty'
    right_line_no: int | None = None
    right_text: str = ""
    right_type: str = "equal"  # 'equal', 'insert', 'replace', 'empty'


class UnifiedDiffLine(BaseModel):
    """Single line in a unified diff output."""

    line_type: str  # 'added', 'removed', 'context', 'header'
    content: str
    old_line_no: int | None = None
    new_line_no: int | None = None


class DiffStats(BaseModel):
    """Summary metrics for a comparison between two snapshots."""

    added_lines: int = 0
    removed_lines: int = 0
    unchanged_lines: int = 0
    similarity_ratio: float = 1.0
    has_meaningful_changes: bool = False
    summary: str = "No meaningful changes detected between these versions."


class DiffResponse(BaseModel):
    """Complete diff response between two snapshots of a website."""

    site_id: int
    site_name: str
    from_snapshot: SnapshotDetail
    to_snapshot: SnapshotDetail
    stats: DiffStats
    unified_diff: list[UnifiedDiffLine]
    side_by_side: list[DiffSideRow]


class NotificationResponse(BaseModel):
    """Notification record with delivery status."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    site_id: int
    site_name: str | None = None
    site_url: str | None = None
    snapshot_id: int | None = None
    channel: str
    trigger_type: str
    matched_keywords: str | None = None
    title: str
    message: str
    delivery_status: str
    error_detail: str | None = None
    dedup_key: str
    created_at: datetime


class ManualCheckResponse(BaseModel):
    """Response returned when triggering an immediate check on a site."""

    site: SiteResponse
    check_run: CheckRunResponse
    snapshot_created: bool
    snapshot: SnapshotSummary | None = None
    notifications_triggered: int = 0
    message: str


class TimelinePoint(BaseModel):
    """Daily aggregation point for dashboard charts."""

    date: str
    changes: int
    successful_checks: int
    failed_checks: int


class DashboardStatsResponse(BaseModel):
    """Aggregated statistics for the dashboard."""

    total_sites: int
    sites_with_changes: int
    sites_in_error: int
    successful_checks: int
    failed_checks: int
    total_snapshots: int
    total_notifications: int
    timeline: list[TimelinePoint] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """Application health check payload."""

    status: str
    version: str
    database: str
    scheduler_running: bool
    monitored_sites_count: int
    timestamp: datetime
