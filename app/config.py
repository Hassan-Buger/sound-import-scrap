from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator
from typing import ClassVar, Dict, Any

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Default to SQLite for zero-setup local dev / unconfigured deploys.
    # In production (Railway, Docker Compose), DATABASE_URL will override this.
    database_url: str = "sqlite+aiosqlite:///./soundimports.db"

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        """
        Auto-detect database URLs injected by platforms (Railway, Heroku, Render).
        Supports DATABASE_URL, POSTGRES_URL, DATABASE_PRIVATE_URL, DATABASE_PUBLIC_URL.
        Converts plain 'postgres://' or 'postgresql://' to 'postgresql+asyncpg://'.
        Safely falls back to SQLite if URL is empty or an un-expanded Railway variable.
        """
        import os

        env_url = (
            os.getenv("DATABASE_URL")
            or os.getenv("POSTGRES_URL")
            or os.getenv("DATABASE_PRIVATE_URL")
            or os.getenv("DATABASE_PUBLIC_URL")
        )
        if env_url:
            v = env_url

        if not v or not isinstance(v, str) or v.startswith("${{"):
            return "sqlite+aiosqlite:///./soundimports.db"

        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    base_url: str = "https://www.soundimports.eu"
    sitemap_url: str = "https://www.soundimports.eu/en/sitemap/"

    # Scraper concurrency & reliability controls. Env aliases accepted:
    #   CONCURRENCY / SCRAPER_CONCURRENCY        - overall HTTP concurrency
    #   SCRAPER_CATEGORY_CONCURRENCY              - category page workers
    #   SCRAPER_PRODUCT_CONCURRENCY               - product detail workers
    #   REQUEST_DELAY / SCRAPER_REQUEST_DELAY      - min gap between requests (s)
    #   MAX_RETRIES / SCRAPER_MAX_RETRIES          - retries per HTTP request
    #   REQUEST_TIMEOUT / SCRAPER_TIMEOUT          - per-request timeout (s)
    #   SCRAPER_CATEGORY_RETRIES                   - retries per failed category
    concurrency: int = 20
    request_delay: float = 0.1
    max_retries: int = 5
    request_timeout: int = 30
    user_agent: str = "Mozilla/5.0 (compatible; SoundImportsScraper/1.0)"
    category_concurrency: int = 5
    product_concurrency: int = 20
    category_max_retries: int = 3
    rate_limit: float = 0.0
    category_deactivation_threshold: int = 2
    job_stale_after: int = 120

    @field_validator(
        "concurrency",
        "category_concurrency",
        "product_concurrency",
        "max_retries",
        "request_timeout",
        "category_max_retries",
        "category_deactivation_threshold",
        "job_stale_after",
        mode="after",
    )
    @classmethod
    def _positive(cls, v: int) -> int:
        # A zero-sized semaphore never releases and silently deadlocks a run.
        return max(1, int(v))

    @field_validator("request_delay", "rate_limit", mode="after")
    @classmethod
    def _non_negative_delay(cls, v: float) -> float:
        return max(0.0, float(v))

    @model_validator(mode="before")
    @classmethod
    def apply_scraper_env(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """Fold documented SCRAPER_* / short env aliases into the base fields."""
        import os

        def pick(*names, coerce=type(None)):
            for n in names:
                v = os.getenv(n)
                if v is not None and v != "":
                    try:
                        return coerce(v)
                    except (TypeError, ValueError):
                        return v
            return None

        int_pick = lambda *ns: pick(*ns, coerce=int)  # noqa: E731
        float_pick = lambda *ns: pick(*ns, coerce=float)  # noqa: E731

        overrides = {
            "concurrency": int_pick("SCRAPER_CONCURRENCY", "CONCURRENCY"),
            "request_delay": float_pick("SCRAPER_REQUEST_DELAY", "REQUEST_DELAY"),
            "max_retries": int_pick("SCRAPER_MAX_RETRIES", "MAX_RETRIES"),
            "request_timeout": int_pick("SCRAPER_TIMEOUT", "REQUEST_TIMEOUT"),
            "category_concurrency": int_pick("SCRAPER_CATEGORY_CONCURRENCY"),
            "product_concurrency": int_pick("SCRAPER_PRODUCT_CONCURRENCY"),
            "category_max_retries": int_pick("SCRAPER_CATEGORY_RETRIES"),
            "rate_limit": float_pick("SCRAPER_RATE_LIMIT"),
            "category_deactivation_threshold": int_pick(
                "SCRAPER_CATEGORY_DEACTIVATION_THRESHOLD"
            ),
            "job_stale_after": int_pick("SCRAPER_JOB_STALE_AFTER"),
        }
        for key, value in overrides.items():
            if value is not None:
                values[key] = value
        return values

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    log_level: str = "INFO"

    json_export_dir: str = "export"

    DB_ECHO: ClassVar[bool] = False


settings = Settings()
