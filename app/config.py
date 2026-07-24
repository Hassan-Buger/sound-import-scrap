import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import ClassVar

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # NOTE: this default is only meant for local dev via docker-compose.
    # If you see "ConnectionRefusedError" to localhost:5432 in a deployed
    # environment (Railway, etc.), it means DATABASE_URL was never actually
    # injected into this service and it silently fell back to this default.
    database_url: str = "postgresql+asyncpg://scraper:scraper_pass@localhost:5432/soundimports"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        """
        Many hosting providers (Railway, Render, Heroku, etc.) inject
        DATABASE_URL as plain 'postgres://' or 'postgresql://', which is
        the sync psycopg2-style scheme. SQLAlchemy's async engine needs the
        '+asyncpg' driver explicitly, or it will fail to connect / pick the
        wrong dialect. Normalize automatically so we don't depend on the
        platform (or the human setting env vars) getting the scheme right.
        """
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    base_url: str = "https://www.soundimports.eu"
    sitemap_url: str = "https://www.soundimports.eu/en/sitemap/"
    concurrency: int = 50
    request_delay: float = 0.1
    max_retries: int = 5
    request_timeout: int = 30
    user_agent: str = "Mozilla/5.0 (compatible; SoundImportsScraper/1.0)"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    log_level: str = "INFO"

    json_export_dir: str = "export"

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "")

    DB_ECHO: ClassVar[bool] = False


settings = Settings()
