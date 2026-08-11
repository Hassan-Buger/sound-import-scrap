"""Regression tests for reconciliation semantics discovered live:

1. The authoritative per-category source count is the JSON catalog's
   ``collection.count`` (what is actually enumerable), NOT the sitemap counter.
   A completed category must persist that number as ``source_product_count``.
2. Product/category relationships are driven by where the product was actually
   discovered in a listing, NOT by the detail JSON's ancestor category nodes.
3. Idempotency: rerunning must not duplicate products or relationships.
"""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import Category, Product, ScrapeProgress, product_categories
import scraper.pipeline as pipeline_mod
from scraper.pipeline import ScrapePipeline

LEAF_PATH = "/en/crossover-components/coils/air-core-coils/"
ANCESTOR_PATH = "/en/crossover-components/"


def _category(path, source_count):
    return {
        "name": path.strip("/").rsplit("/", 1)[-1].replace("-", " ").capitalize(),
        "slug": path.strip("/").rsplit("/", 1)[-1],
        "url": f"https://www.soundimports.eu{path}",
        "canonical_path": path,
        "parent_path": None,
        "level": len(path.strip("/").split("/")),
        "source_count": source_count,
    }


class CatalogSupplier:
    """Fake supplier whose catalog agrees with its JSON ``collection.count``.

    ``sitemap_count`` deliberately disagrees with the catalog to reproduce the
    live situation (e.g. sitemap says 1230, catalog lists 5).
    """

    name = "CatalogSupplier"
    concurrency = 1

    def __init__(self, categories, catalog_products):
        self.categories = categories
        # Products are discoverable ONLY under the leaf listing; the ancestor
        # listing is empty even though detail JSON declares the ancestor node.
        self.products_by_url = {
            cat["url"]: list(catalog_products)
            if cat["canonical_path"] == LEAF_PATH
            else []
            for cat in categories
        }
        self.sku_by_url = {p["url"]: p for p in catalog_products}

    async def discover_categories(self):
        return self.categories

    async def get_product_list(self, category_url, page=1, limit=100):
        products = {
            p["id"]: p
            for p in self.products_by_url.get(category_url, [])
        }
        if page > 1:
            products = {}
        return {
            "collection": {
                "count": len(self.products_by_url.get(category_url, [])),
                "pages": 1,
                "page": page,
                "limit": 100,
                "products": products,
            }
        }

    def extract_product_summary(self, raw):
        return {
            "product_id": str(raw["id"]),
            "sku": raw["code"],
            "url": f"https://www.soundimports.eu{raw['url']}",
            "name": raw["name"],
            "price": None,
        }

    async def get_product_detail(self, product_url):
        sku = self.sku_by_url[product_url.replace("https://www.soundimports.eu", "")]
        # Detail JSON declares BOTH the leaf and the ancestor: the bug source.
        return {
            "product": {
                "id": sku["id"],
                "sku": sku["code"],
                "name": sku["name"],
                "price": {"amount": 10.0, "currency": "EUR"},
                "stock": {"quantity": 1, "status": "in_stock"},
                "brand": {"name": "Brand"},
                "url": f"https://www.soundimports.eu{sku['url']}",
                "categories": {
                    "1": {"id": 1, "url": "crossover-components", "title": "Crossover"},
                    "2": {
                        "id": 2,
                        "url": "crossover-components/coils/air-core-coils",
                        "title": "Air core",
                    },
                },
                "images": [],
            }
        }

    def extract_product_detail(self, raw, category_slug=None):
        p = raw.get("product", raw)
        info = self.sku_by_url[p["url"].replace("https://www.soundimports.eu", "")]
        return {
            "product_id": str(p["id"]),
            "sku": p["sku"],
            "ean": None,
            "title": p["name"],
            "description": None,
            "short_description": None,
            "long_description": None,
            "regular_price": 10.0,
            "price": 10.0,
            "stock": 1,
            "stock_status": "in_stock",
            "brand": "Brand",
            "currency": "EUR",
            "url": p["url"],
            "category_ids": info["code"],
            "raw_json": "{}",
            "images": [],
            "attributes": [],
            "product_categories": [
                {"canonical_path": ANCESTOR_PATH},
                {"canonical_path": LEAF_PATH},
            ],
        }


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
    async def fake_sleep(_duration):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


def _catalog_products(n=5):
    return [
        {
            "id": 1000 + i,
            "code": f"SKU-{i}",
            "name": f"Product {i}",
            "url": f"/en/sku-{i}.html",
        }
        for i in range(1, n + 1)
    ]


async def _cat_links(session_factory):
    async with session_factory() as db:
        rows = (await db.execute(select(product_categories))).all()
    return rows


async def _category_row(session_factory, path):
    async with session_factory() as db:
        return (
            await db.execute(select(Category).where(Category.canonical_path == path))
        ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_catalog_count_becomes_authoritative_source(
    session_factory, fast_settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(pipeline_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "json_export_dir", str(tmp_path))
    monkeypatch.setattr(settings, "product_concurrency", 2)
    monkeypatch.setattr(settings, "category_concurrency", 1)

    supplier = CatalogSupplier(
        categories=[
            _category(ANCESTOR_PATH, 5000),  # sitemap says 5000...
            _category(LEAF_PATH, 1230),  # ...and 1230
        ],
        catalog_products=_catalog_products(5),  # ...but catalog says 5
    )
    pipeline = ScrapePipeline(supplier)
    stats = await pipeline.run()

    assert stats["categories_succeeded"] == 2
    assert stats["products_total"] == 5
    assert stats["products_failed"] == 0
    assert stats["job_status"] == "SUCCESS"

    leaf = await _category_row(session_factory, LEAF_PATH)
    assert leaf.source_product_count == 5  # catalog, not 1230
    ancestor = await _category_row(session_factory, ANCESTOR_PATH)
    assert ancestor.source_product_count == 0  # empty listing -> 0


@pytest.mark.asyncio
async def test_relationships_are_listing_driven_not_detail_json(
    session_factory, fast_settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(pipeline_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "json_export_dir", str(tmp_path))
    monkeypatch.setattr(settings, "product_concurrency", 2)
    monkeypatch.setattr(settings, "category_concurrency", 1)

    supplier = CatalogSupplier(
        categories=[
            _category(ANCESTOR_PATH, 5000),
            _category(LEAF_PATH, 1230),
        ],
        catalog_products=_catalog_products(5),
    )
    pipeline = ScrapePipeline(supplier)
    await pipeline.run()

    links = await _cat_links(session_factory)
    assert len(links) == 5  # one per product, leaf only

    async with session_factory() as db:
        leaf = (
            await db.execute(select(Category).where(Category.canonical_path == LEAF_PATH))
        ).scalar_one()
        ancestor = (
            await db.execute(
                select(Category).where(Category.canonical_path == ANCESTOR_PATH)
            )
        ).scalar_one()
        leaf_links = (
            await db.execute(
                select(product_categories).where(
                    product_categories.c.category_id == leaf.id
                )
            )
        ).all()
        ancestor_links = (
            await db.execute(
                select(product_categories).where(
                    product_categories.c.category_id == ancestor.id
                )
            )
        ).all()
        assert len(leaf_links) == 5
        assert len(ancestor_links) == 0


@pytest.mark.asyncio
async def test_rerun_is_idempotent(
    session_factory, fast_settings, monkeypatch, tmp_path
):
    monkeypatch.setattr(pipeline_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(settings, "json_export_dir", str(tmp_path))
    monkeypatch.setattr(settings, "product_concurrency", 2)
    monkeypatch.setattr(settings, "category_concurrency", 1)

    supplier = CatalogSupplier(
        categories=[_category(LEAF_PATH, 1230)],
        catalog_products=_catalog_products(5),
    )
    await ScrapePipeline(supplier).run()
    await ScrapePipeline(supplier).run()

    async with session_factory() as db:
        products = list((await db.execute(select(Product))).scalars().all())
        assert len(products) == 5
    links = await _cat_links(session_factory)
    assert len(links) == 5
    async with session_factory() as db:
        progress = list((await db.execute(select(ScrapeProgress))).scalars().all())
        assert progress