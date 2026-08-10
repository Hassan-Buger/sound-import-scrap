"""Pipeline job-state tests: retry handling, partial success and no-false-
success behaviour, using a fake supplier over an in-memory async database."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import ScrapeJob, ScrapeProgress
from app import crud
from scraper.client import HttpClientError
import scraper.pipeline as pipeline_mod
from scraper.pipeline import ScrapePipeline, decide_job_status


class FakeSupplier:
    name = "FakeSupplier"
    concurrency = 1

    def __init__(self, categories, fail_paths=(), fail_first=0):
        self.categories = categories
        self.fail_paths = set(fail_paths)
        self.fail_first = fail_first
        self.fail_discover = False
        self.list_calls = 0

    async def discover_categories(self):
        if self.fail_discover:
            raise HttpClientError("sitemap unavailable")
        return self.categories

    async def get_product_list(self, category_url, page=1, limit=100):
        self.list_calls += 1
        if self.list_calls <= self.fail_first:
            raise HttpClientError("transient failure")
        if any(p in category_url for p in self.fail_paths):
            raise HttpClientError("persistent failure")
        return {
            "collection": {
                "count": 0,
                "pages": 0,
                "page": 1,
                "limit": 100,
                "products": {},
            }
        }

    def extract_product_summary(self, raw):
        raise AssertionError("should not be called")

    async def get_product_detail(self, product_url):
        raise AssertionError("should not be called")

    def extract_product_detail(self, raw, category_slug=None):
        raise AssertionError("should not be called")


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def fast_settings(monkeypatch):
    monkeypatch.setattr(settings, "category_max_retries", 2)

    async def fake_sleep(_duration):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


def _category(path):
    return {
        "name": path.strip("/").rsplit("/", 1)[-1].replace("-", " ").capitalize(),
        "slug": path.strip("/").rsplit("/", 1)[-1],
        "url": f"https://www.soundimports.eu{path}",
        "canonical_path": path,
        "parent_path": None,
        "level": len(path.strip("/").split("/")),
        "source_count": 0,
    }


async def _latest_job_status(session_factory):
    async with session_factory() as db:
        result = await db.execute(
            select(ScrapeJob).order_by(ScrapeJob.id.desc()).limit(1)
        )
        job = result.scalar_one()
        return job.job_status, job.categories_succeeded, job.categories_failed


def test_decide_job_status():
    assert decide_job_status(0, 0) == "SUCCESS"
    assert decide_job_status(0, 5) == "SUCCESS"
    assert decide_job_status(1, 0) == "FAILED"
    assert decide_job_status(2, 3) == "PARTIAL_SUCCESS"
    # skipped categories must never yield a false SUCCESS
    assert decide_job_status(0, 0, 1) == "FAILED"


@pytest.mark.asyncio
async def test_transient_failure_retried_then_success(
    session_factory, fast_settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(pipeline_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "json_export_dir", str(tmp_path))

    supplier = FakeSupplier(categories=[_category("/en/cat-one/")], fail_first=1)
    pipeline = ScrapePipeline(supplier)
    pipeline.supplier = supplier

    stats = await pipeline.run()

    assert stats["categories_discovered"] == 1
    assert stats["categories_succeeded"] == 1
    assert stats["categories_failed"] == 0
    assert stats["job_status"] == "SUCCESS"

    status, succeeded, failed = await _latest_job_status(session_factory)
    assert status == "SUCCESS"
    assert succeeded == 1
    assert failed == 0


@pytest.mark.asyncio
async def test_partial_success_when_category_fails(
    session_factory, fast_settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(pipeline_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "json_export_dir", str(tmp_path))

    supplier = FakeSupplier(
        categories=[_category("/en/good/"), _category("/en/bad/")],
        fail_paths=["/en/bad/"],
    )
    pipeline = ScrapePipeline(supplier)

    stats = await pipeline.run()

    assert stats["categories_succeeded"] == 1
    assert stats["categories_failed"] == 1
    assert stats["job_status"] == "PARTIAL_SUCCESS"

    status, succeeded, failed = await _latest_job_status(session_factory)
    assert status == "PARTIAL_SUCCESS"
    assert succeeded == 1
    assert failed == 1


@pytest.mark.asyncio
async def test_full_failure_is_not_success(
    session_factory, fast_settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(pipeline_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "json_export_dir", str(tmp_path))

    supplier = FakeSupplier(categories=[_category("/en/bad/")], fail_paths=["/en/bad/"])
    pipeline = ScrapePipeline(supplier)

    stats = await pipeline.run()

    assert stats["categories_succeeded"] == 0
    assert stats["categories_failed"] == 1
    assert stats["job_status"] == "FAILED"

    status, succeeded, failed = await _latest_job_status(session_factory)
    assert status == "FAILED"
    assert succeeded == 0
    assert failed == 1


@pytest.mark.asyncio
async def test_discover_categories_failure_marks_job_failed(
    session_factory, fast_settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(pipeline_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "json_export_dir", str(tmp_path))

    supplier = FakeSupplier(categories=[_category("/en/cat-one/")])
    supplier.fail_discover = True
    pipeline = ScrapePipeline(supplier)

    with pytest.raises(HttpClientError):
        await pipeline.run()

    async with session_factory() as db:
        job = (
            await db.execute(select(ScrapeJob).order_by(ScrapeJob.id.desc()).limit(1))
        ).scalar_one()
        assert job.job_status == "FAILED"
        assert job.status == "failed"
        assert "HttpClientError" in (job.errors or "")


@pytest.mark.asyncio
async def test_completed_category_checkpoint_is_preserved_on_resume(
    session_factory, fast_settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(pipeline_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "json_export_dir", str(tmp_path))
    category = _category("/en/resumable/")

    first_supplier = FakeSupplier(categories=[category])
    first = ScrapePipeline(first_supplier)
    first_stats = await first.run()
    assert first_supplier.list_calls == 1

    second_supplier = FakeSupplier(
        categories=[category], fail_paths=["/en/resumable/"]
    )
    resumed = ScrapePipeline(second_supplier)
    resumed_stats = await resumed.run(job_id=first_stats["job_id"])

    assert second_supplier.list_calls == 0
    assert resumed_stats["categories_succeeded"] == 1
    assert resumed_stats["categories_failed"] == 0
    async with session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(ScrapeProgress).where(
                        ScrapeProgress.job_id == first_stats["job_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].completed is True
