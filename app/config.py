"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the Website Change Detector application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = Field(default="Website Change Detector")
    app_env: str = Field(default="development")
    debug: bool = Field(default=False)
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)

    # Database
    database_url: str = Field(default="sqlite:///./change_detector.db")

    # Monitoring Engine
    user_agent: str = Field(
        default="WebsiteChangeDetector/1.0 (+https://github.com/vinnovateit/website-change-detector; responsible-monitor)"
    )
    request_timeout_seconds: float = Field(default=15.0)
    max_redirects: int = Field(default=5)
    max_response_bytes: int = Field(default=5 * 1024 * 1024)  # 5 MB
    max_retries: int = Field(default=2)
    retry_backoff_factor: float = Field(default=1.5)
    host_min_interval_seconds: float = Field(default=2.0)
    respect_robots_txt: bool = Field(default=True)
    enable_playwright_fallback: bool = Field(default=False)
    playwright_timeout_ms: int = Field(default=15000)

    # Scheduler
    scheduler_enabled: bool = Field(default=True)
    scheduler_tick_seconds: int = Field(default=30)

    # Optional Email Notifications (SMTP)
    smtp_enabled: bool = Field(default=False)
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_username: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_from_email: str = Field(default="")
    smtp_to_email: str = Field(default="")
    smtp_use_tls: bool = Field(default=True)

    # Optional Webhook Notifications (Discord / Telegram / Generic)
    webhook_enabled: bool = Field(default=False)
    webhook_url: str = Field(default="")
    webhook_secret: str = Field(default="")
    telegram_chat_id: str = Field(default="")


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
