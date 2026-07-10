from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import ClassVar


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://scraper:scraper_pass@localhost:5432/soundimports"

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
