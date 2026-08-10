"""UTC clock helpers shared by ORM and scraper state code."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return naive UTC for existing ``DateTime(timezone=False)`` columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
