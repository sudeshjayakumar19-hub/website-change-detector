"""Background scheduler service using APScheduler for periodic website checks."""

from datetime import datetime, timedelta, timezone
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.database import SessionLocal
from app.models import MonitoredSite
from app.services.monitor import MonitoringError, run_site_check

logger = logging.getLogger(__name__)


class MonitoringScheduler:
    """Manages APScheduler jobs for periodic non-overlapping website checks."""

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self._started = False

    @property
    def is_running(self) -> bool:
        return bool(self._started and self.scheduler.running)

    def start(self) -> None:
        """Start the background scheduler and register all existing sites."""
        settings = get_settings()
        if not settings.scheduler_enabled or self._started:
            return

        self.scheduler.add_job(
            self.check_due_sites,
            trigger=IntervalTrigger(seconds=max(10, settings.scheduler_tick_seconds)),
            id="due_sites_sweep",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        self._started = True
        self.sync_all_sites()
        logger.info("MonitoringScheduler started.")

    def shutdown(self) -> None:
        """Gracefully stop the background scheduler."""
        if self._started:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._started = False
            logger.info("MonitoringScheduler shut down.")

    def sync_all_sites(self) -> None:
        """Ensure every site in the database has a registered interval job."""
        if not self._started:
            return
        db = SessionLocal()
        try:
            sites = db.query(MonitoredSite).all()
            for site in sites:
                self.sync_site_job(site.id, site.interval_minutes)
        except Exception as exc:
            logger.warning("Failed to sync site jobs: %s", exc)
        finally:
            db.close()

    def sync_site_job(self, site_id: int, interval_minutes: int) -> None:
        """Register or update the periodic check job for a specific site."""
        if not self._started:
            return
        job_id = f"site_check_{site_id}"
        safe_minutes = max(15, min(1440, int(interval_minutes)))
        self.scheduler.add_job(
            self.run_scheduled_site_check,
            trigger=IntervalTrigger(minutes=safe_minutes),
            args=[site_id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

    def remove_site_job(self, site_id: int) -> None:
        """Remove a scheduled job when a monitored website is deleted."""
        if not self._started:
            return
        job_id = f"site_check_{site_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    async def run_scheduled_site_check(self, site_id: int) -> None:
        """Execute a scheduled check for `site_id` inside an isolated DB session."""
        db = SessionLocal()
        try:
            site = db.query(MonitoredSite).filter(MonitoredSite.id == site_id).first()
            if not site:
                self.remove_site_job(site_id)
                return
            await run_site_check(db, site)
        except MonitoringError as mon_exc:
            logger.debug("Scheduled check skipped/errored for site %s: %s", site_id, mon_exc)
        except Exception as exc:
            logger.exception("Unexpected error in scheduled check for site %s: %s", site_id, exc)
        finally:
            db.close()

    async def check_due_sites(self) -> None:
        """Sweep for any sites whose check interval has elapsed."""
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            sites = db.query(MonitoredSite).all()
            due_ids: list[int] = []
            for site in sites:
                if site.last_checked_at is None:
                    due_ids.append(site.id)
                    continue
                last_checked = site.last_checked_at
                if last_checked.tzinfo is None:
                    last_checked = last_checked.replace(tzinfo=timezone.utc)
                next_due = last_checked + timedelta(minutes=site.interval_minutes)
                if now >= next_due:
                    due_ids.append(site.id)
        except Exception as exc:
            logger.warning("Error querying due sites: %s", exc)
            due_ids = []
        finally:
            db.close()

        for site_id in due_ids:
            await self.run_scheduled_site_check(site_id)


monitoring_scheduler = MonitoringScheduler()
