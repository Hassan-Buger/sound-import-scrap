"""Async CRUD tests for canonical-path category identity and the
product/category many-to-many association."""

import pytest
from datetime import timedelta
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.database import Base
from app.models import Category, Product
from app.timeutils import utc_now
from app import crud


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


async def _upsert_cat(db, name, slug, path, parent=None, count=0):
    return await crud.upsert_category_with_parent(
        db,
        name=name,
        slug=slug,
        canonical_path=path,
        url=f"https://www.soundimports.eu{path}",
        level=len(path.strip("/").split("/")),
        parent_path_ref=parent,
        source_count=count,
    )


@pytest.mark.asyncio
async def test_upsert_category_with_parent_hierarchy(session_factory):
    async with session_factory() as db:
        parent = await _upsert_cat(db, "Home audio", "home-audio", "/en/home-audio/")
        child = await _upsert_cat(
            db,
            "Speakers",
            "speakers",
            "/en/home-audio/speakers/",
            parent="/en/home-audio/",
        )
        grandchild = await _upsert_cat(
            db,
            "Bookshelf",
            "bookshelf-speakers",
            "/en/home-audio/speakers/bookshelf-speakers/",
            parent="/en/home-audio/speakers/",
        )
        await db.commit()

        assert child.parent_id == parent.id
        assert grandchild.parent_id == child.id


@pytest.mark.asyncio
async def test_duplicate_slugs_survive_in_different_branches(session_factory):
    async with session_factory() as db:
        c1 = await _upsert_cat(
            db, "Switches", "switches", "/en/home-audio/amplifiers/switches/"
        )
        c2 = await _upsert_cat(
            db, "Switches", "switches", "/en/accessories/electromechanics/switches/"
        )
        await db.commit()

    assert c1.id != c2.id
    async with session_factory() as db:
        result = await db.execute(select(Category).where(Category.slug == "switches"))
        assert len(result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_upsert_is_idempotent(session_factory):
    async with session_factory() as db:
        await _upsert_cat(db, "Home audio", "home-audio", "/en/home-audio/")
        await db.commit()
        again = await _upsert_cat(
            db, "Home audio v2", "home-audio", "/en/home-audio/", count=5
        )
        await db.commit()

        all_cats = (await db.execute(select(Category))).scalars().all()
        assert len(all_cats) == 1
        assert again.name == "Home audio v2"
        assert again.source_product_count == 5
        assert again.is_active is True


@pytest.mark.asyncio
async def test_reactivated_when_absent_reappears(session_factory):
    async with session_factory() as db:
        cat = await _upsert_cat(db, "Stale", "stale", "/en/stale/")
        await db.commit()
        cat.is_active = False
        await db.commit()
        # upserting the same path reactivates it
        reactivated = await _upsert_cat(db, "Stale", "stale", "/en/stale/")
        await db.commit()
        assert reactivated.is_active is True


@pytest.mark.asyncio
async def test_deactivate_categories_not_in(session_factory):
    async with session_factory() as db:
        await _upsert_cat(db, "Keep", "keep", "/en/keep/")
        await _upsert_cat(db, "Gone", "gone", "/en/gone/")
        await db.commit()
    async with session_factory() as db:
        deactivated = await crud.deactivate_categories_not_in(db, {"/en/keep/"})
        assert deactivated == 0
        deactivated = await crud.deactivate_categories_not_in(db, {"/en/keep/"})
        assert deactivated == 1
        gone = await crud.get_category_by_path(db, "/en/gone/")
        assert gone.is_active is False


@pytest.mark.asyncio
async def test_set_product_categories_idempotent(session_factory):
    async with session_factory() as db:
        await _upsert_cat(db, "Home audio", "home-audio", "/en/home-audio/")
        await _upsert_cat(db, "Bookshelf", "bookshelf", "/en/home-audio/bookshelf/")
        await db.commit()

        product = Product(sku="PROD-1", title="One")
        db.add(product)
        await db.commit()
        prod_id = product.id

        created, existing = await crud.set_product_categories(
            db, prod_id, ["/en/home-audio/", "/en/home-audio/bookshelf/"]
        )
        await db.commit()
        assert created == 2
        assert existing == 0

        created2, existing2 = await crud.set_product_categories(
            db, prod_id, ["/en/home-audio/", "/en/home-audio/bookshelf/"]
        )
        await db.commit()
        assert created2 == 0
        assert existing2 == 2

        # unknown paths are ignored
        created3, existing3 = await crud.set_product_categories(
            db, prod_id, ["/en/does-not-exist/"]
        )
        await db.commit()
        assert created3 == 0
        assert existing3 == 0


@pytest.mark.asyncio
async def test_recompute_category_counts_family(session_factory):
    async with session_factory() as db:
        parent = await _upsert_cat(db, "Home audio", "home-audio", "/en/home-audio/")
        child = await _upsert_cat(
            db,
            "Bookshelf",
            "bookshelf",
            "/en/home-audio/bookshelf/",
            parent="/en/home-audio/",
        )
        await db.commit()

        p1 = Product(sku="SP-1", title="One")
        p2 = Product(sku="SP-2", title="Two")
        db.add_all([p1, p2])
        await db.commit()
        await crud.set_product_categories(db, p1.id, ["/en/home-audio/"])
        await crud.set_product_categories(db, p2.id, ["/en/home-audio/bookshelf/"])
        await db.commit()

        await crud.recompute_category_counts(db, family=True)

        parent = await crud.get_category_by_id(db, parent.id)
        child = await crud.get_category_by_id(db, child.id)
        # family(parent) = {parent, child} -> two distinct products
        assert parent.product_count == 2
        # family(child) = {child} -> one
        assert child.product_count == 1


@pytest.mark.asyncio
async def test_family_count_deduplicates_product_in_parent_and_child(session_factory):
    async with session_factory() as db:
        parent = await _upsert_cat(db, "Home audio", "home-audio", "/en/home-audio/")
        child = await _upsert_cat(
            db,
            "Bookshelf",
            "bookshelf",
            "/en/home-audio/bookshelf/",
            parent="/en/home-audio/",
        )
        product = Product(sku="BOTH-1", title="Both")
        db.add(product)
        await db.commit()
        await crud.set_product_categories(
            db,
            product.id,
            ["/en/home-audio/", "/en/home-audio/bookshelf/"],
        )
        await db.commit()
        await crud.recompute_category_counts(db, family=True)
        parent = await crud.get_category_by_id(db, parent.id)
        child = await crud.get_category_by_id(db, child.id)
        assert parent.product_count == 1
        assert child.product_count == 1


@pytest.mark.asyncio
async def test_category_relationship_direction(session_factory):
    async with session_factory() as db:
        parent = await _upsert_cat(db, "Root", "root", "/en/root/")
        child = await _upsert_cat(
            db, "Child", "child", "/en/root/child/", parent="/en/root/"
        )
        await db.commit()

    async with session_factory() as db:
        loaded = await crud.get_category_by_id(db, parent.id)
        assert [row.id for row in loaded.children] == [child.id]
        assert child.parent_id == parent.id


@pytest.mark.asyncio
async def test_job_lease_blocks_active_and_reclaims_stale(session_factory):
    async with session_factory() as db:
        job_id = await crud.create_scrape_job(db, "full")
        same_id, state = await crud.create_or_resume_scrape_job(
            db, "full", stale_after_seconds=120
        )
        assert (same_id, state) == (job_id, "already_running")
        cross_type_id, cross_type_state = await crud.create_or_resume_scrape_job(
            db, "incremental", stale_after_seconds=120
        )
        assert (cross_type_id, cross_type_state) == (job_id, "already_running")

        job = await crud.get_scrape_job(db, job_id)
        job.heartbeat_at = utc_now() - timedelta(minutes=5)
        await db.commit()

        reclaimed_id, state = await crud.create_or_resume_scrape_job(
            db, "full", stale_after_seconds=120
        )
        assert (reclaimed_id, state) == (job_id, "resumed")
        reclaimed = await crud.get_scrape_job(db, job_id)
        assert reclaimed.status == "running"


@pytest.mark.asyncio
async def test_get_products_paginated_by_category_id(session_factory):
    async with session_factory() as db:
        await _upsert_cat(db, "Speakers", "speakers", "/en/speakers/")
        await db.commit()

        p1 = Product(sku="SPK-1", title="Speaker One", category_ids="speakers")
        p2 = Product(sku="AMP-1", title="Amp One", category_ids="amplifiers")
        db.add_all([p1, p2])
        await db.commit()
        await crud.set_product_categories(db, p1.id, ["/en/speakers/"])
        await db.commit()

        speakers = await crud.get_category_by_path(db, "/en/speakers/")

    # products for a real category are found via the M2M table
    async with session_factory() as db:
        products, total = await crud.get_products_paginated(
            db, page=1, per_page=50, category_id=speakers.id
        )
        assert total == 1
        assert products[0].sku == "SPK-1"

        # a non-existent category returns empty (no LIKE fallback)
        products, total = await crud.get_products_paginated(
            db, page=1, per_page=50, category_id=999999
        )
        assert (products, total) == ([], 0)


@pytest.mark.asyncio
async def test_get_category_by_id_or_slug_resolution(session_factory):
    async with session_factory() as db:
        await _upsert_cat(db, "Speakers", "speakers", "/en/speakers/")
        await _upsert_cat(
            db, "Switches", "switches", "/en/home-audio/amplifiers/switches/"
        )
        await db.commit()

    async with session_factory() as db:
        cat = await crud.get_category_by_id_or_slug(db, "/en/speakers/")
        assert cat is not None and cat.canonical_path == "/en/speakers/"

        by_id = await crud.get_category_by_id_or_slug(db, str(cat.id))
        assert by_id.id == cat.id

        # slug resolution is ambiguous but still resolves to the lowest id
        by_slug = await crud.get_category_by_id_or_slug(db, "switches")
        assert by_slug.slug == "switches"
