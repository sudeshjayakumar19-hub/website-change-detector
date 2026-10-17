"""REST API endpoints for Website Change Detector."""

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app import __version__
from app.database import get_db
from app.models import CheckRun, MonitoredSite, Notification, PageSnapshot
from app.schemas import (
    CheckRunResponse,
    DashboardStatsResponse,
    DiffResponse,
    HealthResponse,
    ManualCheckResponse,
    NotificationResponse,
    SiteCreate,
    SiteDetailResponse,
    SiteHistoryResponse,
    SiteResponse,
    SnapshotDetail,
    SnapshotSummary,
    TimelinePoint,
)
from app.services.differ import compute_diff
from app.services.monitor import MonitoringError, run_site_check
from app.services.rss import build_site_rss_xml
from app.services.scheduler import monitoring_scheduler

router = APIRouter(prefix="/api", tags=["Website Change Detector API"])


def _serialize_site(db: Session, site: MonitoredSite) -> SiteResponse:
    """Convert a MonitoredSite ORM instance into a SiteResponse with snapshot count."""
    snap_count = (
        db.query(func.count(PageSnapshot.id))
        .filter(PageSnapshot.site_id == site.id)
        .scalar()
        or 0
    )
    return SiteResponse(
        id=site.id,
        name=site.name,
        url=site.url,
        interval_minutes=site.interval_minutes,
        keywords=site.keywords,
        keyword_only_alerts=site.keyword_only_alerts,
        css_selector=site.css_selector,
        ignore_selector=site.ignore_selector,
        use_playwright=site.use_playwright,
        status=site.status,
        last_checked_at=site.last_checked_at,
        last_changed_at=site.last_changed_at,
        last_status_code=site.last_status_code,
        last_check_duration_ms=site.last_check_duration_ms,
        last_error=site.last_error,
        last_change_summary=site.last_change_summary,
        consecutive_failures=site.consecutive_failures,
        snapshot_count=snap_count,
        created_at=site.created_at,
        updated_at=site.updated_at,
    )


def _get_site_or_404(db: Session, site_id: int) -> MonitoredSite:
    site = db.query(MonitoredSite).filter(MonitoredSite.id == site_id).first()
    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monitored website with ID {site_id} not found.",
        )
    return site


@router.get("/health", response_model=HealthResponse, summary="Application health check")
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    """Return service health, database connectivity, and scheduler status."""
    db_status = "healthy"
    sites_count = 0
    try:
        db.execute(text("SELECT 1"))
        sites_count = db.query(func.count(MonitoredSite.id)).scalar() or 0
    except Exception as exc:
        db_status = f"error: {exc}"

    return HealthResponse(
        status="ok" if db_status == "healthy" else "degraded",
        version=__version__,
        database=db_status,
        scheduler_running=monitoring_scheduler.is_running,
        monitored_sites_count=sites_count,
        timestamp=datetime.now(timezone.utc),
    )


@router.get(
    "/stats",
    response_model=DashboardStatsResponse,
    summary="Get aggregated dashboard statistics and timeline chart data",
)
def get_dashboard_stats(db: Session = Depends(get_db)) -> DashboardStatsResponse:
    """Aggregate statistics across monitored websites, checks, snapshots, and alerts."""
    total_sites = db.query(func.count(MonitoredSite.id)).scalar() or 0
    sites_with_changes = (
        db.query(func.count(MonitoredSite.id))
        .filter(
            (MonitoredSite.status == "changed") | (MonitoredSite.last_changed_at.isnot(None))
        )
        .scalar()
        or 0
    )
    sites_in_error = (
        db.query(func.count(MonitoredSite.id))
        .filter(MonitoredSite.status == "error")
        .scalar()
        or 0
    )
    successful_checks = (
        db.query(func.count(CheckRun.id))
        .filter(CheckRun.status.in_(["initial", "unchanged", "changed"]))
        .scalar()
        or 0
    )
    failed_checks = (
        db.query(func.count(CheckRun.id))
        .filter(CheckRun.status == "failed")
        .scalar()
        or 0
    )
    total_snapshots = db.query(func.count(PageSnapshot.id)).scalar() or 0
    total_notifications = db.query(func.count(Notification.id)).scalar() or 0

    # Build 7-day timeline of meaningful changes and check outcomes
    now = datetime.now(timezone.utc)
    start_day = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    recent_runs = (
        db.query(CheckRun)
        .filter(CheckRun.checked_at >= start_day)
        .all()
    )

    buckets: dict[str, dict[str, int]] = {}
    for offset in range(7):
        day_str = (start_day + timedelta(days=offset)).strftime("%Y-%m-%d")
        buckets[day_str] = {"changes": 0, "successful_checks": 0, "failed_checks": 0}

    for run in recent_runs:
        checked_dt = run.checked_at
        if checked_dt.tzinfo is None:
            checked_dt = checked_dt.replace(tzinfo=timezone.utc)
        day_key = checked_dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if day_key not in buckets:
            continue
        if run.status == "changed":
            buckets[day_key]["changes"] += 1
            buckets[day_key]["successful_checks"] += 1
        elif run.status in ("initial", "unchanged"):
            buckets[day_key]["successful_checks"] += 1
        elif run.status == "failed":
            buckets[day_key]["failed_checks"] += 1

    timeline = [
        TimelinePoint(
            date=day_key,
            changes=vals["changes"],
            successful_checks=vals["successful_checks"],
            failed_checks=vals["failed_checks"],
        )
        for day_key, vals in buckets.items()
    ]

    return DashboardStatsResponse(
        total_sites=total_sites,
        sites_with_changes=sites_with_changes,
        sites_in_error=sites_in_error,
        successful_checks=successful_checks,
        failed_checks=failed_checks,
        total_snapshots=total_snapshots,
        total_notifications=total_notifications,
        timeline=timeline,
    )


@router.get("/sites", response_model=list[SiteResponse], summary="List all monitored websites")
def list_sites(db: Session = Depends(get_db)) -> list[SiteResponse]:
    """Return all monitored websites ordered by creation time descending."""
    sites = db.query(MonitoredSite).order_by(MonitoredSite.created_at.desc()).all()
    return [_serialize_site(db, site) for site in sites]


@router.post(
    "/sites",
    response_model=SiteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new monitored website",
)
def create_site(payload: SiteCreate, db: Session = Depends(get_db)) -> SiteResponse:
    """Validate and register a public webpage for periodic change detection."""
    site = MonitoredSite(
        name=payload.name,
        url=payload.url,
        interval_minutes=payload.interval_minutes,
        keywords=payload.keywords,
        keyword_only_alerts=payload.keyword_only_alerts,
        css_selector=payload.css_selector,
        ignore_selector=payload.ignore_selector,
        use_playwright=payload.use_playwright,
        status="pending",
    )
    db.add(site)
    db.commit()
    db.refresh(site)

    monitoring_scheduler.sync_site_job(site.id, site.interval_minutes)
    return _serialize_site(db, site)


@router.get(
    "/sites/{site_id}",
    response_model=SiteDetailResponse,
    summary="Get website details",
)
def get_site_detail(site_id: int, db: Session = Depends(get_db)) -> SiteDetailResponse:
    """Return full details for a monitored website including recent snapshots and check runs."""
    site = _get_site_or_404(db, site_id)
    base_resp = _serialize_site(db, site)

    recent_snapshots = (
        db.query(PageSnapshot)
        .filter(PageSnapshot.site_id == site.id)
        .order_by(PageSnapshot.version_number.desc())
        .limit(20)
        .all()
    )
    recent_checks = (
        db.query(CheckRun)
        .filter(CheckRun.site_id == site.id)
        .order_by(CheckRun.checked_at.desc())
        .limit(20)
        .all()
    )

    return SiteDetailResponse(
        **base_resp.model_dump(),
        recent_snapshots=[SnapshotSummary.model_validate(s) for s in recent_snapshots],
        recent_checks=[CheckRunResponse.model_validate(c) for c in recent_checks],
    )


@router.delete(
    "/sites/{site_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a monitored website and its history",
)
def delete_site(site_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete a monitored website and cascade-clean its snapshots, check runs, and alerts."""
    site = _get_site_or_404(db, site_id)
    site_name = site.name
    monitoring_scheduler.remove_site_job(site.id)

    db.delete(site)
    db.commit()
    return {"status": "deleted", "id": site_id, "name": site_name}


@router.post(
    "/sites/{site_id}/check",
    response_model=ManualCheckResponse,
    summary="Trigger an immediate manual check for a website",
)
async def trigger_manual_check(
    site_id: int,
    db: Session = Depends(get_db),
) -> ManualCheckResponse:
    """Fetch the webpage immediately, extract content, compare hashes, and record results."""
    site = _get_site_or_404(db, site_id)
    try:
        outcome = await run_site_check(db, site)
    except MonitoringError as exc:
        if exc.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.message,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.message,
        ) from exc

    return ManualCheckResponse(
        site=_serialize_site(db, outcome.site),
        check_run=CheckRunResponse.model_validate(outcome.check_run),
        snapshot_created=outcome.snapshot_created,
        snapshot=(
            SnapshotSummary.model_validate(outcome.snapshot)
            if outcome.snapshot is not None
            else None
        ),
        notifications_triggered=len(outcome.notifications),
        message=outcome.message,
    )


@router.get(
    "/sites/{site_id}/history",
    response_model=SiteHistoryResponse,
    summary="Retrieve version history and check runs for a website",
)
def get_site_history(site_id: int, db: Session = Depends(get_db)) -> SiteHistoryResponse:
    """Return chronological snapshots and recent check runs for a monitored website."""
    site = _get_site_or_404(db, site_id)
    snapshots = (
        db.query(PageSnapshot)
        .filter(PageSnapshot.site_id == site.id)
        .order_by(PageSnapshot.version_number.desc())
        .all()
    )
    recent_checks = (
        db.query(CheckRun)
        .filter(CheckRun.site_id == site.id)
        .order_by(CheckRun.checked_at.desc())
        .limit(50)
        .all()
    )
    return SiteHistoryResponse(
        site=_serialize_site(db, site),
        snapshots=[SnapshotSummary.model_validate(s) for s in snapshots],
        recent_checks=[CheckRunResponse.model_validate(c) for c in recent_checks],
    )


@router.get(
    "/sites/{site_id}/snapshots/{snapshot_id}",
    response_model=SnapshotDetail,
    summary="Retrieve a specific snapshot with full extracted text",
)
def get_snapshot_detail(
    site_id: int,
    snapshot_id: int,
    db: Session = Depends(get_db),
) -> SnapshotDetail:
    """Return full extracted content and metadata for a single snapshot."""
    _get_site_or_404(db, site_id)
    snap = (
        db.query(PageSnapshot)
        .filter(PageSnapshot.site_id == site_id, PageSnapshot.id == snapshot_id)
        .first()
    )
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Snapshot {snapshot_id} not found for website {site_id}.",
        )
    return SnapshotDetail.model_validate(snap)


@router.get(
    "/sites/{site_id}/diff",
    response_model=DiffResponse,
    summary="Compare two versions of a monitored website",
)
def get_site_diff(
    site_id: int,
    from_id: int | None = Query(default=None, description="Older snapshot ID"),
    to_id: int | None = Query(default=None, description="Newer snapshot ID"),
    from_version: int | None = Query(default=None, description="Older version number"),
    to_version: int | None = Query(default=None, description="Newer version number"),
    db: Session = Depends(get_db),
) -> DiffResponse:
    """Generate a unified and side-by-side diff between any two snapshots of a website."""
    site = _get_site_or_404(db, site_id)
    all_snapshots = (
        db.query(PageSnapshot)
        .filter(PageSnapshot.site_id == site.id)
        .order_by(PageSnapshot.version_number.asc())
        .all()
    )
    if not all_snapshots:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No snapshots exist yet for this website. Run a check first.",
        )

    from_snap: PageSnapshot | None = None
    to_snap: PageSnapshot | None = None

    if from_id is not None:
        from_snap = next((s for s in all_snapshots if s.id == from_id), None)
        if not from_snap:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Snapshot ID {from_id} not found for website {site_id}.",
            )
    elif from_version is not None:
        from_snap = next((s for s in all_snapshots if s.version_number == from_version), None)
        if not from_snap:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version {from_version} not found for website {site_id}.",
            )

    if to_id is not None:
        to_snap = next((s for s in all_snapshots if s.id == to_id), None)
        if not to_snap:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Snapshot ID {to_id} not found for website {site_id}.",
            )
    elif to_version is not None:
        to_snap = next((s for s in all_snapshots if s.version_number == to_version), None)
        if not to_snap:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version {to_version} not found for website {site_id}.",
            )

    # Default selection if not explicitly specified
    if to_snap is None:
        to_snap = all_snapshots[-1]
    if from_snap is None:
        if len(all_snapshots) >= 2:
            # Find the snapshot immediately preceding `to_snap`, or the first snapshot
            idx = next(
                (i for i, s in enumerate(all_snapshots) if s.id == to_snap.id),
                len(all_snapshots) - 1,
            )
            from_snap = all_snapshots[max(0, idx - 1)]
        else:
            from_snap = all_snapshots[0]

    computed = compute_diff(from_snap.extracted_text, to_snap.extracted_text)

    return DiffResponse(
        site_id=site.id,
        site_name=site.name,
        from_snapshot=SnapshotDetail.model_validate(from_snap),
        to_snapshot=SnapshotDetail.model_validate(to_snap),
        stats=computed.stats,
        unified_diff=computed.unified_lines,
        side_by_side=computed.side_by_side_rows,
    )


@router.get(
    "/sites/{site_id}/rss",
    summary="Page-specific RSS 2.0 feed for detected changes",
)
def get_site_rss_feed(
    site_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Return an RSS 2.0 XML feed of version snapshots for the specified website."""
    site = _get_site_or_404(db, site_id)
    snapshots = (
        db.query(PageSnapshot)
        .filter(PageSnapshot.site_id == site.id)
        .order_by(PageSnapshot.version_number.desc())
        .limit(30)
        .all()
    )
    base_url = str(request.base_url).rstrip("/")
    xml_content = build_site_rss_xml(site, snapshots, base_url=base_url)
    return Response(content=xml_content, media_type="application/rss+xml; charset=utf-8")


@router.get(
    "/notifications",
    response_model=list[NotificationResponse],
    summary="List change notifications and delivery status",
)
def list_notifications(
    site_id: int | None = Query(default=None, description="Optional filter by website ID"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[NotificationResponse]:
    """Return recent notifications with their site context and delivery status."""
    query = db.query(Notification)
    if site_id is not None:
        query = query.filter(Notification.site_id == site_id)

    records = query.order_by(Notification.created_at.desc()).limit(limit).all()
    results: list[NotificationResponse] = []
    for notif in records:
        site = notif.site
        results.append(
            NotificationResponse(
                id=notif.id,
                site_id=notif.site_id,
                site_name=site.name if site else None,
                site_url=site.url if site else None,
                snapshot_id=notif.snapshot_id,
                channel=notif.channel,
                trigger_type=notif.trigger_type,
                matched_keywords=notif.matched_keywords,
                title=notif.title,
                message=notif.message,
                delivery_status=notif.delivery_status,
                error_detail=notif.error_detail,
                dedup_key=notif.dedup_key,
                created_at=notif.created_at,
            )
        )
    return results
