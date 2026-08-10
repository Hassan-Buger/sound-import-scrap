"""Safe application-driven Alembic startup for local, Docker, and Railway."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.database import engine

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    # Application/CLI logging is configured by the caller. Let standalone
    # `alembic` commands keep their normal alembic.ini logging behavior.
    config.attributes["configure_logger"] = False
    return config


async def _detect_unversioned_revision() -> Optional[str]:
    """Identify databases historically created with metadata.create_all().

    Older releases created tables without an ``alembic_version`` row. We stamp
    only schemas that exactly match a known revision boundary; ambiguous or
    partially migrated schemas are left unstamped so Alembic fails loudly.
    """

    def detect(sync_connection) -> Optional[str]:
        inspector = inspect(sync_connection)
        tables = set(inspector.get_table_names())
        if not tables or "alembic_version" in tables:
            return None
        if "categories" not in tables or "products" not in tables:
            return None

        category_columns = {c["name"] for c in inspector.get_columns("categories")}
        product_columns = {c["name"] for c in inspector.get_columns("products")}
        attribute_columns = (
            {c["name"] for c in inspector.get_columns("attributes")}
            if "attributes" in tables
            else set()
        )
        job_columns = (
            {c["name"] for c in inspector.get_columns("scrape_jobs")}
            if "scrape_jobs" in tables
            else set()
        )
        progress_columns = (
            {c["name"] for c in inspector.get_columns("scrape_progress")}
            if "scrape_progress" in tables
            else set()
        )

        revision_004 = (
            {"canonical_path", "source_product_count", "missing_streak"}
            <= category_columns
            and "product_categories" in tables
            and {"job_status", "categories_failed", "summary"} <= job_columns
            and {"category_id", "canonical_path", "status"} <= progress_columns
        )
        if revision_004:
            return "004"
        if "canonical_path" in category_columns or "product_categories" in tables:
            raise RuntimeError(
                "Database has a partially applied category migration and no "
                "alembic_version; refusing to guess a revision."
            )
        if {"normalized_name", "sort_order"} <= attribute_columns:
            return "003"
        if {"regular_price", "short_description"} <= product_columns:
            return "002"
        return "001"

    async with engine.connect() as connection:
        return await connection.run_sync(detect)


async def upgrade_database() -> None:
    """Stamp a recognized legacy schema if needed, then migrate to head."""
    legacy_revision = await _detect_unversioned_revision()
    config = _alembic_config()
    if legacy_revision:
        logger.warning(
            "Found unversioned schema matching revision %s; stamping before upgrade",
            legacy_revision,
        )
        await asyncio.to_thread(command.stamp, config, legacy_revision)
    await asyncio.to_thread(command.upgrade, config, "head")
