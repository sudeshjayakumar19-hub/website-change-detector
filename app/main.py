"""FastAPI application entry point for Website Change Detector."""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import __version__
from app.api.routes import router as api_router
from app.config import get_settings
from app.database import get_db, init_db
from app.models import MonitoredSite
from app.services.scheduler import monitoring_scheduler

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database schema and background scheduler on startup."""
    init_db()
    monitoring_scheduler.start()
    try:
        yield
    finally:
        monitoring_scheduler.shutdown()


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=(
        "Production-minded service that monitors public webpages, detects meaningful content changes, "
        "ignores dynamic noise, stores version history, generates diffs, and dispatches alerts."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(api_router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def render_dashboard(request: Request) -> HTMLResponse:
    """Serve the main Website Change Detector dashboard."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.app_name,
            "version": __version__,
        },
    )


@app.get("/sites/{site_id}/history", response_class=HTMLResponse, include_in_schema=False)
def render_site_history(
    site_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Serve the version history and diff viewer page for a monitored website."""
    site = db.query(MonitoredSite).filter(MonitoredSite.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Monitored website not found.")

    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={
            "app_name": settings.app_name,
            "version": __version__,
            "site_id": site.id,
            "site_name": site.name,
            "site_url": site.url,
        },
    )
