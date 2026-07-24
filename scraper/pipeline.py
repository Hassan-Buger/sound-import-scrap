import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Awaitable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app import crud
from app.models import Product, Category
from scraper.client import HttpClient
from scraper.sitemap import SitemapParser
from scraper.category import CategoryScraper
from scraper.product import ProductScraper
from scraper.base import BaseSupplierScraper
from app.schemas import ProductDetail, CategoryOut

logger = logging.getLogger("scraper.pipeline")


class ScrapePipeline:
    """Orchestrates the full scraping workflow with resume support."""

    def __init__(
        self,
        supplier: BaseSupplierScraper,
        client: Optional[HttpClient] = None,
        full: bool = True,
    ):
        self.supplier = supplier
        self.client = client or HttpClient(concurrency=supplier.concurrency)
        self.full = full
        self._stats = {
            "categories_discovered": 0,
            "categories_completed": 0,
            "products_total": 0,
            "products_new": 0,
            "products_updated": 0,
            "products_failed": 0,
            "pages_fetched": 0,
        }

    async def run(self) -> Dict[str, Any]:
        """Execute the full scrape pipeline."""
        start_time = datetime.utcnow()
        logger.info("Starting %s scrape for %s", "full" if self.full else "incremental", self.supplier.name)

        job_id = None
        async with async_session_factory() as db:
            job_id = await crud.create_scrape_job(db, "full" if self.full else "incremental")

        try:
            categories = await self.supplier.discover_categories()
            self._stats["categories_discovered"] = len(categories)
            logger.info("Discovered %d categories", len(categories))

            await self._sync_categories(categories)

            sem = asyncio.Semaphore(self.supplier.concurrency)

            async def process_category(cat: Dict[str, Any]):
                async with sem:
                    await self._scrape_category(job_id, cat)

            tasks = [process_category(cat) for cat in categories]
            await asyncio.gather(*tasks, return_exceptions=True)

            elapsed = (datetime.utcnow() - start_time).total_seconds()
            self._stats["elapsed_seconds"] = elapsed

            async with async_session_factory() as db:
                await crud.update_scrape_job(
                    db, job_id,
                    status="completed",
                    finished_at=datetime.utcnow(),
                    total_categories=self._stats["categories_discovered"],
                    completed_categories=self._stats["categories_completed"],
                    total_products=self._stats["products_total"],
                    new_products=self._stats["products_new"],
                    updated_products=self._stats["products_updated"],
                    failed_products=self._stats["products_failed"],
                )

            await self._export_json()

            logger.info(
                "Scrape finished in %.1fs: %d categories, %d products "
                "(%d new, %d updated, %d failed)",
                elapsed,
                self._stats["categories_completed"],
                self._stats["products_total"],
                self._stats["products_new"],
                self._stats["products_updated"],
                self._stats["products_failed"],
            )

        except Exception as e:
            logger.exception("Scrape failed: %s", e)
            async with async_session_factory() as db:
                await crud.update_scrape_job(
                    db, job_id,
                    status="failed",
                    finished_at=datetime.utcnow(),
                    errors=str(e),
                )
            raise

        return self._stats

    async def _export_json(self):
        """Export scraped data to JSON files for debugging."""
        export_dir = Path(settings.json_export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)

        async with async_session_factory() as db:
            categories = await crud.get_all_categories(db)
            cat_data = [
                CategoryOut.model_validate(c).model_dump() for c in categories
            ]
            with open(export_dir / "categories.json", "w", encoding="utf-8") as f:
                json.dump(cat_data, f, ensure_ascii=False, indent=2)
            logger.info("Exported %d categories to %s", len(cat_data), export_dir / "categories.json")

            brands_raw = await crud.get_brands(db)
            brand_data = [{"name": r["brand"], "product_count": r["product_count"]} for r in brands_raw]
            with open(export_dir / "brands.json", "w", encoding="utf-8") as f:
                json.dump(brand_data, f, ensure_ascii=False, indent=2)
            logger.info("Exported %d brands to %s", len(brand_data), export_dir / "brands.json")

            products = await crud.export_products(db)
            products_dir = export_dir / "products"
            products_dir.mkdir(parents=True, exist_ok=True)
            for p in products:
                detail = ProductDetail.model_validate(p)
                sku = p.sku.replace("/", "_").replace("\\", "_")
                with open(products_dir / f"{sku}.json", "w", encoding="utf-8") as f:
                    json.dump(detail.model_dump(), f, ensure_ascii=False, indent=2)
            logger.info("Exported %d products to %s", len(products), products_dir)

    async def _sync_categories(self, categories: List[Dict[str, Any]]):
        """Upsert all discovered categories into the database."""
        slug_to_id: Dict[str, int] = {}

        async with async_session_factory() as db:
            for cat in categories:
                parent_id = None
                parent_slug = cat.get("parent_slug")
                if parent_slug and parent_slug in slug_to_id:
                    parent_id = slug_to_id[parent_slug]

                db_cat = await crud.upsert_category(
                    db,
                    name=cat["name"],
                    slug=cat["slug"],
                    url=cat["url"],
                    level=cat.get("level", 0),
                    parent_id=parent_id,
                )
                slug_to_id[cat["slug"]] = db_cat.id

    async def _scrape_category(self, job_id: int, category: Dict[str, Any]):
        """Scrape all products from a single category."""
        cat_url = category["url"]
        cat_name = category["name"]
        logger.info("Category started: %s (%s)", cat_name, cat_url)

        try:
            async with async_session_factory() as db:
                progress = await crud.get_scrape_progress(db, job_id, cat_url)
                start_page = 1
                if progress and not self.full:
                    start_page = progress.page
                    if progress.completed:
                        logger.info("Category %s already completed, skipping", cat_name)
                        return

            # Use category slug from the category dict for product categorization
            category_slug = category.get("slug")

            page = start_page
            limit = 100
            total_products_in_category = 0

            while True:
                data = await self.supplier.get_product_list(cat_url, page=page, limit=limit)
                self._stats["pages_fetched"] += 1

                product_list = self._extract_products_from_list(data)
                if not product_list:
                    logger.debug("No products on page %d for %s", page, cat_name)
                    break

                await self._process_product_list(product_list, category_slug=category_slug)

                total_pages = self._get_total_pages(data)
                logger.debug(
                    "Category %s page %d/%d: %d products",
                    cat_name, page, total_pages, len(product_list),
                )

                async with async_session_factory() as db:
                    await crud.upsert_scrape_progress(
                        db, job_id, cat_url,
                        page=page,
                        total_pages=total_pages,
                        total_products=total_products_in_category + len(product_list),
                    )

                total_products_in_category += len(product_list)

                if page >= total_pages:
                    break
                page += 1

            self._stats["categories_completed"] += 1

            async with async_session_factory() as db:
                await crud.upsert_scrape_progress(
                    db, job_id, cat_url,
                    completed=True,
                    total_products=total_products_in_category,
                )

            logger.info(
                "Category finished: %s (%d products)",
                cat_name, total_products_in_category,
            )

        except Exception as e:
            self._stats["products_failed"] += 1
            logger.error("Category failed: %s - %s", cat_name, e)
            async with async_session_factory() as db:
                await crud.upsert_scrape_progress(
                    db, job_id, cat_url,
                    errors=str(e),
                )

    async def _process_product_list(self, product_list: List[Dict[str, Any]], category_slug: str = None):
        """Fetch details for all products in a list concurrently."""
        http_sem = asyncio.Semaphore(self.supplier.concurrency)
        db_sem = asyncio.Semaphore(20)

        async def process_one(product: Dict[str, Any]):
            async with http_sem:
                try:
                    product_summary = self.supplier.extract_product_summary(product)
                    detail_data = await self.supplier.get_product_detail(
                        product_summary.get("url", "")
                    )
                    product_data = self.supplier.extract_product_detail(detail_data, category_slug=category_slug)

                    async with db_sem:
                        async with async_session_factory() as db:
                            await crud.upsert_product(
                                db,
                                product_id=product_data["product_id"],
                                sku=product_data["sku"],
                                ean=product_data.get("ean"),
                                title=product_data.get("title"),
                                description=product_data.get("description"),
                                short_description=product_data.get("short_description"),
                                long_description=product_data.get("long_description"),
                                regular_price=product_data.get("regular_price"),
                                price=product_data.get("price"),
                                stock=product_data.get("stock"),
                                stock_status=product_data.get("stock_status"),
                                brand=product_data.get("brand"),
                                currency=product_data.get("currency", "EUR"),
                                url=product_data.get("url"),
                                category_ids=product_data.get("category_ids"),
                                raw_json=product_data.get("raw_json"),
                                images=product_data.get("images", []),
                                attributes=product_data.get("attributes", []),
                            )

                    self._stats["products_total"] += 1
                    if product_data.get("is_new", True):
                        self._stats["products_new"] += 1
                    else:
                        self._stats["products_updated"] += 1

                except Exception as e:
                    self._stats["products_failed"] += 1
                    sku = product.get("code", product.get("sku", "unknown"))
                    logger.error("Product failed %s: %s", sku, e)

        tasks = [process_one(p) for p in product_list]
        await asyncio.gather(*tasks, return_exceptions=True)

    def _extract_products_from_list(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract product list from API response.

        Handles both:
          - {collection: {products: {id: {...}, id: {...}}}}
          - {products: [{...}, {...}]}
          - {products: {id: {...}, id: {...}}}
        """
        collection = data.get("collection", data)
        products = collection.get("products", {})

        if isinstance(products, list):
            return products
        if isinstance(products, dict):
            return list(products.values())

        products = data.get("items") or data.get("data", [])
        if isinstance(products, list):
            return products
        if isinstance(products, dict):
            return list(products.values())

        return []

    def _get_total_pages(self, data: Dict[str, Any]) -> int:
        collection = data.get("collection", data)
        pages = collection.get("pages", 0)
        if pages:
            return pages
        total = collection.get("count", 0) or data.get("total", 0) or data.get("count", 0)
        limit = collection.get("limit", 100) or data.get("limit", 100)
        if total == 0:
            return 0
        return max(1, (total + limit - 1) // limit)
