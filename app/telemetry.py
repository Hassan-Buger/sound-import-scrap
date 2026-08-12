from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import text

from app.database import async_session_factory

_TABLE_DDL = """\
CREATE TABLE IF NOT EXISTS telemetry (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source VARCHAR(32),
    phase VARCHAR(64),
    rss_kb BIGINT,
    cgroup_bytes BIGINT,
    swap_kb BIGINT,
    threads INT,
    fds INT
)
"""


async def create_telemetry_table() -> None:
    async with async_session_factory() as db:
        await db.execute(text(_TABLE_DDL))
        await db.commit()


def _read_proc_status() -> dict:
    """Parse VM/RSS/swap/threads from /proc/self/status in one pass."""
    fields = {"rss_kb": None, "swap_kb": None, "threads": None}
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                try:
                    if key == "VmRSS":
                        fields["rss_kb"] = int(val.split()[0])
                    elif key == "VmSwap":
                        fields["swap_kb"] = int(val.split()[0])
                    elif key == "Threads":
                        fields["threads"] = int(val)
                except (ValueError, IndexError):
                    continue
    except (OSError, IOError):
        pass
    return fields


def _sample() -> dict:
    proc = _read_proc_status()

    cgroup_bytes = None
    try:
        with open("/sys/fs/cgroup/memory.current", "r", encoding="utf-8") as fh:
            cgroup_bytes = int(fh.read().strip())
    except (OSError, IOError, ValueError):
        try:
            with open(
                "/sys/fs/cgroup/memory/memory.usage_in_bytes", "r", encoding="utf-8"
            ) as fh:
                cgroup_bytes = int(fh.read().strip())
        except (OSError, IOError, ValueError):
            pass

    fds = None
    try:
        fds = len(os.listdir("/proc/self/fd"))
    except (OSError, IOError):
        pass

    return {
        "rss_kb": proc["rss_kb"],
        "swap_kb": proc["swap_kb"],
        "threads": proc["threads"],
        "cgroup_bytes": cgroup_bytes,
        "fds": fds,
    }


async def record(phase: str = "running") -> None:
    data = _sample()
    async with async_session_factory() as db:
        await db.execute(
            text(
                "INSERT INTO telemetry "
                "(source, phase, rss_kb, cgroup_bytes, swap_kb, threads, fds) "
                "VALUES (:source, :phase, :rss_kb, :cgroup_bytes, :swap_kb, :threads, :fds)"
            ),
            {
                "source": "scrape",
                "phase": phase,
                "rss_kb": data["rss_kb"],
                "cgroup_bytes": data["cgroup_bytes"],
                "swap_kb": data["swap_kb"],
                "threads": data["threads"],
                "fds": data["fds"],
            },
        )
        await db.commit()


async def read_telemetry(limit: int = 60) -> List[dict]:
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, created_at, source, phase, rss_kb, cgroup_bytes, "
                    "swap_kb, threads, fds FROM telemetry ORDER BY id DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
        ).all()
    return [
        {
            "id": r[0],
            "created_at": r[1].isoformat() if r[1] else None,
            "source": r[2],
            "phase": r[3],
            "rss_kb": r[4],
            "cgroup_bytes": r[5],
            "swap_kb": r[6],
            "threads": r[7],
            "fds": r[8],
        }
        for r in rows
    ]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()