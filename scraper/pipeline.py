import asyncio
import json
import logging
import random
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from app.config import settings
from app.database import async_session_factory
from app import crud
from app.models import Category
from scraper.client import (
    HttpClient,
    HttpClientError,
    RetryableHttpError,
    NonRetryableHttpError,
)
from scraper.category import CategoryScraper
from scraper.base import BaseSupplierScraper
from app.schemas import ProductDetail, CategoryOut
from app.timeutils import utc_now

logger = logging.getLogger("scraper.pipeline")

# Maximum number of product-detail coroutines constructed per listing page.
# HTTP concurrency is already bounded by the shared client semaphore, but a
# page of ``limit=100`` products across several concurrent categories produces
# hundreds of pending asyncio tasks.  Batching the *construction* of those tasks
# keeps peak memory bounded without changing request concurrency.
PRODUCT_BATCH_SIZE = 10


class CategoryIncompleteError(RetryableHttpError):
    """A category response was reachable but incomplete or inconsistent."""

    def __init__(self, message: str, *, product_failures: int = 0):
        super().__init__(message)
        self.product_failures = product_failures


def decide_job_status(
    failed: int,
    succeeded: int,
    skipped: int = 0,
    products_failed: int = 0,
    discrepancies: int = 0,
) -> str:
    """Derive a truthful terminal state from all completeness signals."""
    incomplete = failed + skipped + products_failed + discrepancies
    if incomplete == 0:
        return "SUCCESS"
    if succeeded == 0 and (failed or skipped):
        return "FAILED"
    return "PARTIAL_SUCCESS"


class ScrapePipeline:
    """Orchestrates the full scraping workflow with resume, retry and
    per-category job state persisted in the database so that Railway
    restarts never lose progress."""

    def __init__(
        self,
        supplier: BaseSupplierScraper,
        client: Optional[HttpClient] = None,
        full: bool = True,
        category_filter: Optional[Set[str]] = None,
    ):
        self.supplier = supplier
        self.client = client or HttpClient(
            concurrency=settings.product_concurrency,
            request_delay=settings.request_delay,
            max_retries=settings.max_retries,
            timeout=settings.request_timeout,
        )
        self.full = full
        self.category_filter = {x.strip() for x in (category_filter or set()) if x.strip()}
        self.category_scraper = CategoryScraper(self.client)
        self._category_sem = asyncio.Semaphore(max(1, settings.category_concurrency))
        self._db_sem = asyncio.Semaphore(8)
        self._product_locks: Dict[str, asyncio.Lock] = {}
        self._product_cache: Dict[str, int] = {}
        self._seen_product_ids: Set[int] = set()
        self._stats: Dict[str, Any] = {
            "job_id": None,
            "categories_discovered": 0,
            "categories_completed": 0,
            "categories_succeeded": 0,
            "categories_failed": 0,
            "categories_skipped": 0,
            "products_total": 0,
            "products_new": 0,
            "products_updated": 0,
            "products_failed": 0,
            "pages_fetched": 0,
            "relationships_created": 0,
            "relationships_existing": 0,
            "relationships_removed": 0,
            "category_discrepancies": 0,
            "http_requests": 0,
            "http_retries": 0,
            "http_failures": 0,
        }

    # ------------------------------------------------------------------ run

    async def run(
        self,
        too_many_failures_ratio: float = 0.05,
        job_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        start_time = utc_now()
        logger.info(
            "Starting %s scrape for %s",
            "full" if self.full else "incremental",
            self.supplier.name,
        )

        if job_id is not None:
            self._stats["job_id"] = job_id
            async with async_session_factory() as db:
                await crud.update_scrape_job(
                    db,
                    job_id,
                    status="running",
                    job_status="RUNNING",
                    finished_at=None,
                    heartbeat_at=utc_now(),
                )
        else:
            job_id = await self._create_or_resume_job()

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_job(job_id, heartbeat_stop)
        )
        try:
            source_categories = await self.supplier.discover_categories()
            if not source_categories:
                raise CategoryIncompleteError(
                    "Sitemap discovery returned zero categories; refusing false success"
                )

            categories = source_categories
            if self.category_filter:
                categories = [
                    category
                    for category in source_categories
                    if any(
                        token in category["canonical_path"]
                        or token == category["slug"]
                        for token in self.category_filter
                    )
                ]
                if not categories:
                    raise ValueError(
                        "No categories matched filter: "
                        + ", ".join(sorted(self.category_filter))
                    )

            self._stats["categories_discovered"] = len(categories)
            logger.info(
                "Discovered %d source categories; %d selected for this job",
                len(source_categories),
                len(categories),
            )

            # Persist canonical-path-derived DB tree.
            path_to_cat = await self._sync_categories(source_categories)

            # Safe reconciliation: a category is only deactivated when the
            # source sitemap was loaded successfully and it is genuinely absent.
            active_paths = {c["canonical_path"] for c in source_categories}
            async with async_session_factory() as db:
                deactivated = await crud.deactivate_categories_not_in(
                    db,
                    active_paths,
                    confirmation_threshold=settings.category_deactivation_threshold,
                )
                if deactivated:
                    logger.info(
                        "Deactivated %d categories absent from the source tree",
                        deactivated,
                    )

            pending_categories = await self._prepare_job_progress(
                job_id, categories, path_to_cat
            )

            tasks = [
                self._run_category_limited(job_id, category)
                for category in pending_categories
            ]
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for category, result in zip(pending_categories, task_results):
                if isinstance(result, BaseException):
                    self._stats["categories_failed"] += 1
                    logger.exception(
                        "Unhandled category task failure for %s",
                        category["canonical_path"],
                        exc_info=(type(result), result, result.__traceback__),
                    )

            async with async_session_factory() as db:
                await crud.recompute_category_counts(db, family=True)
            await self._detect_count_discrepancies(
                {category["canonical_path"] for category in categories}
            )

            elapsed = (utc_now() - start_time).total_seconds()
            self._stats["elapsed_seconds"] = elapsed
            await self._finalize_job(job_id)

            await self._export_json()

            logger.info(
                "Scrape finished in %.1fs: %d categories (%d succeeded, %d failed, %d skipped), "
                "%d products (%d new, %d updated, %d failed). Job status: %s",
                elapsed,
                self._stats["categories_discovered"],
                self._stats["categories_succeeded"],
                self._stats["categories_failed"],
                self._stats["categories_skipped"],
                self._stats["products_total"],
                self._stats["products_new"],
                self._stats["products_updated"],
                self._stats["products_failed"],
                self._stats.get("job_status"),
            )

        except Exception as e:
            logger.exception("Scrape failed: %s", e)
            self._stats["job_status"] = "FAILED"
            async with async_session_factory() as db:
                await crud.update_scrape_job(
                    db,
                    job_id,
                    status="failed",
                    job_status="FAILED",
                    finished_at=utc_now(),
                    errors=f"{type(e).__name__}: {e}",
                )
            raise
        finally:
            heartbeat_stop.set()
            await heartbeat_task

        return self._stats

    async def _heartbeat_job(self, job_id: int, stop: asyncio.Event) -> None:
        """Renew the database lease while a scrape process is alive."""
        interval = max(5.0, min(30.0, settings.job_stale_after / 3))
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                try:
                    async with async_session_factory() as db:
                        await crud.update_scrape_job(
                            db, job_id, heartbeat_at=utc_now()
                        )
                except Exception as exc:
                    # The scrape itself may recover even if one heartbeat write
                    # fails; the next renewal will retry.
                    logger.warning("Job #%d heartbeat failed: %s", job_id, exc)

    async def _create_or_resume_job(self) -> int:
        """Resume an interrupted same-type job, including full jobs."""
        job_type = "full" if self.full else "incremental"
        async with async_session_factory() as db:
            job_id, claim_state = await crud.create_or_resume_scrape_job(
                db, job_type, settings.job_stale_after
            )
            if claim_state == "already_running":
                raise RuntimeError(
                    f"A {job_type} scrape is already running as job #{job_id}"
                )
            if claim_state == "resumed":
                logger.info("Resuming interrupted job #%d", job_id)
            self._stats["job_id"] = job_id
            return job_id

    async def _prepare_job_progress(
        self,
        job_id: int,
        categories: List[Dict[str, Any]],
        path_to_cat: Dict[str, Category],
    ) -> List[Dict[str, Any]]:
        """Persist discovery and return only categories not already completed."""
        async with async_session_factory() as db:
            for category in categories:
                await crud.mark_category_discovered(
                    db, job_id, path_to_cat[category["canonical_path"]]
                )
            await db.commit()
            progress_rows = await crud.get_job_progress(db, job_id)

        completed_paths = {
            row.canonical_path for row in progress_rows if row.completed
        }
        if completed_paths:
            self._stats["categories_completed"] += len(completed_paths)
            self._stats["categories_succeeded"] += len(completed_paths)
            logger.info(
                "Resume checkpoint: %d categories already completed",
                len(completed_paths),
            )
        return [
            category
            for category in categories
            if category["canonical_path"] not in completed_paths
        ]

    async def _run_category_limited(
        self, job_id: int, category: Dict[str, Any]
    ):
        async with self._category_sem:
            return await self._scrape_category_with_retries(job_id, category)

    # ------------------------------------------------------------- categories

    async def _sync_categories(
        self, categories: List[Dict[str, Any]]
    ) -> Dict[str, Category]:
        """Upsert all discovered categories into the DB keyed by canonical path,
        resolving parents via canonical parent paths."""
        async with async_session_factory() as db:
            path_to_cat: Dict[str, Category] = {}
            for cat in categories:
                db_cat = await crud.upsert_category_with_parent(
                    db,
                    name=cat["name"],
                    slug=cat["slug"],
                    canonical_path=cat["canonical_path"],
                    url=cat["url"],
                    level=cat.get("level", 0),
                    parent_path_ref=cat.get("parent_path"),
                    source_count=cat.get("source_count", 0),
                )
                path_to_cat[cat["canonical_path"]] = db_cat
            await db.commit()
        return path_to_cat

    async def _scrape_category_with_retries(
        self, job_id: int, category: Dict[str, Any]
    ):
        """Scrape a single category retrying on transient failures."""
        max_attempts = max(1, settings.category_max_retries)
        attempt = 0
        delay = 1.0

        while True:
            attempt += 1
            try:
                return await self._scrape_category(job_id, category, attempt=attempt)
            except NonRetryableHttpError as exc:
                self._stats["categories_failed"] += 1
                logger.error(
                    "Category aborted on permanent HTTP error: %s - %s",
                    category["name"],
                    exc,
                )
                async with async_session_factory() as db:
                    await crud.update_category_progress(
                        db,
                        job_id,
                        category["canonical_path"],
                        status="failed",
                        attempt_count=attempt,
                        last_error=str(exc),
                        last_error_class=type(exc).__name__,
                        completed_at=utc_now(),
                        completed=False,
                    )
                    await crud.update_category_scrape_state(
                        db,
                        category["canonical_path"],
                        scrape_status="failed",
                        attempt_count=attempt,
                        last_error=str(exc),
                    )
                return
            except (
                HttpClientError,
                asyncio.TimeoutError,
                ConnectionError,
                OSError,
            ) as exc:
                if attempt >= max_attempts:
                    self._stats["categories_failed"] += 1
                    self._stats["products_failed"] += getattr(
                        exc, "product_failures", 0
                    )
                    logger.error(
                        "Category failed after %d attempts: %s - %s",
                        attempt,
                        category["name"],
                        exc,
                    )
                    async with async_session_factory() as db:
                        await crud.update_category_progress(
                            db,
                            job_id,
                            category["canonical_path"],
                            status="failed",
                            attempt_count=attempt,
                            last_error=str(exc),
                            last_error_class=type(exc).__name__,
                            completed_at=utc_now(),
                            completed=False,
                        )
                        await crud.update_category_scrape_state(
                            db,
                            category["canonical_path"],
                            scrape_status="failed",
                            attempt_count=attempt,
                            last_error=str(exc),
                        )
                    return
                # exponential backoff + jitter
                wait = delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    "Category %s attempt %d/%d failed (%s); retrying in %.1fs",
                    category["name"],
                    attempt,
                    max_attempts,
                    exc,
                    wait,
                )
                async with async_session_factory() as db:
                    await crud.update_category_progress(
                        db,
                        job_id,
                        category["canonical_path"],
                        status="retrying",
                        attempt_count=attempt,
                        last_error=str(exc),
                        last_error_class=type(exc).__name__,
                    )
                    await crud.update_category_scrape_state(
                        db,
                        category["canonical_path"],
                        scrape_status="retrying",
                        attempt_count=attempt,
                        last_error=str(exc),
                    )
                await asyncio.sleep(wait)
            except Exception as exc:  # non-retryable
                self._stats["categories_failed"] += 1
                logger.exception(
                    "Category aborted (non-retryable): %s", category["name"]
                )
                async with async_session_factory() as db:
                    await crud.update_category_progress(
                        db,
                        job_id,
                        category["canonical_path"],
                        status="failed",
                        attempt_count=attempt,
                        last_error=f"{type(exc).__name__}: {exc}",
                        last_error_class=type(exc).__name__,
                        completed_at=utc_now(),
                        completed=False,
                    )
                    await crud.update_category_scrape_state(
                        db,
                        category["canonical_path"],
                        scrape_status="failed",
                        attempt_count=attempt,
                        last_error=f"{type(exc).__name__}: {exc}",
                    )
                return

    async def _scrape_category(
        self, job_id: int, category: Dict[str, Any], attempt: int = 1
    ):
        """Scrape all products from a single category."""
        cat_url = category["url"]
        cat_path = category["canonical_path"]
        cat_name = category["name"]
        logger.info("Category started: %s (%s) attempt=%d", cat_name, cat_url, attempt)

        async with async_session_factory() as db:
            await crud.update_category_progress(
                db,
                job_id,
                cat_path,
                status="running",
                attempt_count=attempt,
                started_at=utc_now(),
            )

        page = 1
        limit = 100
        products_scraped = 0
        products_failed = 0
        pages_processed = 0
        # ``source_count`` is the raw counter SoundImports prints next to the
        # category in its HTML sitemap. It is a family/aggregate metric and is
        # NOT a reliable source-of-truth for how many products are actually
        # enumerable. ``catalog_total`` is the JSON catalog's own declared
        # count, which the scraper then verifies by enumerating pages.
        source_count = 0
        catalog_total = None
        list_total = 0
        total_pages = 0
        listed_keys: Set[str] = set()
        previous_page_keys: Set[str] = set()
        successful_product_ids: Set[int] = set()
        product_failure_messages: List[str] = []

        try:
            while True:
                data = await self.supplier.get_product_list(
                    cat_url, page=page, limit=limit
                )
                self._stats["pages_fetched"] += 1

                meta = self.category_scraper.get_metadata(data)
                if page == 1:
                    source_count = category.get("source_count", 0)
                    list_total = meta["total"]
                    catalog_total = list_total
                    total_pages = self.category_scraper.get_total_pages(data)

                product_list = self._extract_products_from_list(data)
                page_keys = {self._product_list_key(item) for item in product_list}
                page_keys.discard("")
                if page > 1 and page_keys and page_keys == previous_page_keys:
                    raise CategoryIncompleteError(
                        f"Pagination repeated page {page} for {cat_path}"
                    )
                if not product_list and total_pages and page < total_pages:
                    raise CategoryIncompleteError(
                        f"Pagination returned an empty page {page}/{total_pages} for {cat_path}"
                    )

                new_products = []
                for item in product_list:
                    key = self._product_list_key(item)
                    if key and key not in listed_keys:
                        new_products.append(item)
                        listed_keys.add(key)
                previous_page_keys = page_keys

                if new_products:
                    found, failed, product_ids, failures = await self._process_product_list(
                        new_products,
                        category_path=cat_path,
                    )
                    products_scraped += found
                    products_failed += failed
                    successful_product_ids.update(product_ids)
                    product_failure_messages.extend(failures)
                pages_processed += 1

                total_pages = max(
                    total_pages,
                    self.category_scraper.get_total_pages(data),
                )
                logger.debug(
                    "Category %s page %d/%d: %d products (source total %d)",
                    cat_name,
                    page,
                    total_pages,
                    len(product_list),
                    source_count,
                )

                async with async_session_factory() as db:
                    await crud.update_category_progress(
                        db,
                        job_id,
                        cat_path,
                        page=page,
                        total_pages=total_pages,
                        total_products=list_total,
                        source_count=source_count,
                        products_scraped=products_scraped,
                        pages_processed=pages_processed,
                    )

                if not product_list or page >= total_pages or total_pages == 0:
                    break
                page += 1

            if list_total and len(listed_keys) != list_total:
                raise CategoryIncompleteError(
                    f"Product-list count mismatch for {cat_path}: "
                    f"api_total={list_total}, unique_listed={len(listed_keys)}"
                )
            if products_failed:
                samples = "; ".join(product_failure_messages[:5])
                raise CategoryIncompleteError(
                    f"{products_failed} product detail(s) failed for {cat_path}: {samples}",
                    product_failures=products_failed,
                )

            async with async_session_factory() as db:
                removed = await crud.replace_category_products(
                    db, cat_path, successful_product_ids
                )
                self._stats["relationships_removed"] += removed

            self._stats["categories_completed"] += 1
            self._stats["categories_succeeded"] += 1

            async with async_session_factory() as db:
                await crud.update_category_progress(
                    db,
                    job_id,
                    cat_path,
                    status="completed",
                    completed=True,
                    completed_at=utc_now(),
                    products_scraped=products_scraped,
                    pages_processed=pages_processed,
                    total_products=list_total,
                    source_count=source_count,
                    last_error=None,
                )
                await crud.update_category_scrape_state(
                    db,
                    cat_path,
                    scrape_status="completed",
                    attempt_count=attempt,
                    last_error=None,
                    last_scraped_at=utc_now(),
                    # Persist the authoritative enumerable catalog count (JSON
                    # collection.count) instead of the inflated sitemap counter
                    # so later audits compare against the right source number.
                    source_product_count=catalog_total or 0,
                )

            logger.info(
                "Category finished: %s (catalog=%d, scraped=%d, failed=%d, sitemap=%d)",
                cat_name,
                catalog_total or 0,
                products_scraped,
                products_failed,
                source_count,
            )

        except Exception as exc:
            async with async_session_factory() as db:
                await crud.update_category_progress(
                    db,
                    job_id,
                    cat_path,
                    status="retrying"
                    if attempt < max(1, settings.category_max_retries)
                    else "failed",
                    last_error=str(exc),
                    last_error_class=type(exc).__name__,
                )
            raise

    # ------------------------------------------------------------- products

    async def _process_product_list(
        self,
        product_list: List[Dict[str, Any]],
        category_path: Optional[str] = None,
    ) -> tuple[int, int, Set[int], List[str]]:
        """Fetch details for all products in a list concurrently."""
        found = 0
        failed = 0
        product_ids: Set[int] = set()
        failures: List[str] = []
        lock = asyncio.Lock()

        async def process_one(raw_product: Dict[str, Any]):
            nonlocal found, failed
            try:
                product_summary = self.supplier.extract_product_summary(raw_product)
                product_url = product_summary.get("url", "")
                if not product_url:
                    raise ValueError("Product list entry has no detail URL")
                lock_key = str(
                    product_summary.get("sku")
                    or product_summary.get("product_id")
                    or product_url
                )
                product_lock = self._product_locks.setdefault(
                    lock_key, asyncio.Lock()
                )

                async with product_lock:
                    cached_product_id = self._product_cache.get(lock_key)
                    if cached_product_id is not None:
                        async with self._db_sem:
                            async with async_session_factory() as db:
                                rel_created, rel_existing = (
                                    await crud.set_product_categories(
                                        db,
                                        cached_product_id,
                                        [category_path] if category_path else [],
                                    )
                                )
                                await db.commit()
                        self._stats["relationships_created"] += rel_created
                        self._stats["relationships_existing"] += rel_existing
                        async with lock:
                            found += 1
                            product_ids.add(cached_product_id)
                        return

                    try:
                        detail_data = await self.supplier.get_product_detail(
                            product_url
                        )
                        product_data = self.supplier.extract_product_detail(
                            detail_data, category_slug=category_path
                        )
                    except RetryableHttpError as exc:
                        # A small number of live product URLs return HTTP 200
                        # with an empty/non-JSON body.  The category listing
                        # still supplies a stable ID, SKU, title and price, so
                        # retain that product and its category membership
                        # rather than failing every category that contains it.
                        if "Invalid JSON" not in str(exc):
                            raise
                        product_data = self._listing_fallback_product_data(
                            raw_product, product_summary, category_path
                        )
                        logger.warning(
                            "Using category-list fallback for malformed product detail %s",
                            product_url,
                        )

                    existing_ids = product_data.get("category_ids") or ""

                    async with self._db_sem:
                        async with async_session_factory() as db:
                            (
                                db_product,
                                is_new,
                                (rel_created, rel_existing),
                            ) = await crud.upsert_product(
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
                                category_ids=existing_ids,
                                raw_json=product_data.get("raw_json"),
                                images=product_data.get("images", []),
                                attributes=product_data.get("attributes", []),
                                # Source-of-truth for product/category membership
                                # is the listing where the product was actually
                                # discovered. The detail JSON's ``categories``
                                # nodes repeat every ancestor (Crossover
                                # components, Coils, Air core coils) and would
                                # otherwise create relationships to categories
                                # whose own pages never list the product.
                                product_categories_paths=[category_path]
                                if category_path
                                else [],
                            )
                            self._stats["relationships_created"] += rel_created
                            self._stats["relationships_existing"] += rel_existing
                            self._product_cache[lock_key] = db_product.id

                    async with lock:
                        found += 1
                        product_ids.add(db_product.id)
                        if db_product.id not in self._seen_product_ids:
                            self._seen_product_ids.add(db_product.id)
                            self._stats["products_total"] += 1
                            if is_new:
                                self._stats["products_new"] += 1
                            else:
                                self._stats["products_updated"] += 1

            except Exception as exc:
                sku = raw_product.get(
                    "code", raw_product.get("sku", raw_product.get("id", "unknown"))
                )
                message = f"{sku}: {type(exc).__name__}: {exc}"
                async with lock:
                    failed += 1
                    failures.append(message)
                logger.error("Product failed %s", message)

        # Build the detail-fetch coroutines in bounded batches.  The shared
        # HTTP semaphore already caps real request concurrency; this caps the
        # number of live task objects and ORM session closures in memory so a
        # 100-product page across a few concurrent categories does not create
        # hundreds of pending coroutines at once.
        for start in range(0, len(product_list), PRODUCT_BATCH_SIZE):
            chunk = product_list[start : start + PRODUCT_BATCH_SIZE]
            await asyncio.gather(
                *(process_one(p) for p in chunk),
                return_exceptions=True,
            )
        return found, failed, product_ids, failures

    @staticmethod
    def _listing_fallback_product_data(
        raw_product: Dict[str, Any],
        summary: Dict[str, Any],
        category_path: Optional[str],
    ) -> Dict[str, Any]:
        """Build a minimal, non-destructive product record from a listing.

        ``images`` and ``attributes`` are deliberately ``None``: passing empty
        lists to the upsert would erase richer data obtained in a prior run.
        """
        sku = str(summary.get("sku") or summary.get("product_id") or "").strip()
        if not sku:
            raise ValueError("Product list entry has no SKU or product ID")

        price = summary.get("price")
        try:
            price = float(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            price = None

        return {
            "product_id": str(summary.get("product_id") or sku),
            "sku": sku,
            "ean": None,
            "title": summary.get("name"),
            "description": None,
            "short_description": None,
            "long_description": None,
            "regular_price": price,
            "price": price,
            "stock": None,
            "stock_status": None,
            "brand": None,
            "currency": "EUR",
            "url": summary.get("url"),
            "category_ids": "",
            "raw_json": json.dumps(raw_product, ensure_ascii=False),
            "images": None,
            "attributes": None,
            "product_categories": (
                [{"canonical_path": category_path}] if category_path else []
            ),
        }

    @staticmethod
    def _product_list_key(product: Dict[str, Any]) -> str:
        for key in ("id", "code", "sku", "url", "link"):
            value = product.get(key)
            if value not in (None, ""):
                return f"{key}:{value}"
        return ""

    def _extract_products_from_list(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        return self.category_scraper.extract_products(data)

    # ------------------------------------------------------------- counts

    async def _detect_count_discrepancies(
        self, selected_paths: Optional[Set[str]] = None
    ):
        """Compare DB-derived counts against the source's own counts.

        Reads fresh ``product_count`` from the DB (values were just recomputed
        by ``recompute_category_counts``) rather than the in-memory rows from
        ``_sync_categories``, which are stale at this point.
        """
        discrepancies = 0
        async with async_session_factory() as db:
            cats = await crud.get_all_categories(db)
            for cat in cats:
                if selected_paths is not None and cat.canonical_path not in selected_paths:
                    continue
                db_count = cat.product_count
                source_count = cat.source_product_count
                if db_count != source_count and source_count:
                    discrepancies += 1
                    logger.warning(
                        "Count discrepancy: %s source=%d db=%d",
                        cat.canonical_path,
                        source_count,
                        db_count,
                    )
        self._stats["category_discrepancies"] = discrepancies

    async def _finalize_job(self, job_id: int):
        failed = self._stats["categories_failed"]
        succeeded = self._stats["categories_succeeded"]
        skipped = self._stats["categories_skipped"]
        client = getattr(self.supplier, "_client", self.client)
        http_stats = getattr(client, "stats", {}) or {}
        self._stats["http_requests"] = http_stats.get("requests", 0)
        self._stats["http_retries"] = http_stats.get("retries", 0)
        self._stats["http_failures"] = http_stats.get("failures", 0)
        job_status = decide_job_status(
            failed,
            succeeded,
            skipped,
            products_failed=self._stats["products_failed"],
            discrepancies=self._stats["category_discrepancies"],
        )

        summary = {
            "categories_discovered": self._stats["categories_discovered"],
            "categories_succeeded": succeeded,
            "categories_failed": failed,
            "categories_skipped": self._stats["categories_skipped"],
            "products_total": self._stats["products_total"],
            "products_new": self._stats["products_new"],
            "products_updated": self._stats["products_updated"],
            "products_failed": self._stats["products_failed"],
            "relationships_created": self._stats["relationships_created"],
            "relationships_existing": self._stats["relationships_existing"],
            "relationships_removed": self._stats["relationships_removed"],
            "category_discrepancies": self._stats["category_discrepancies"],
            "http_requests": self._stats["http_requests"],
            "http_retries": self._stats["http_retries"],
            "http_failures": self._stats["http_failures"],
            "elapsed_seconds": self._stats.get("elapsed_seconds", 0),
        }
        self._stats["job_status"] = job_status

        async with async_session_factory() as db:
            await crud.update_scrape_job(
                db,
                job_id,
                status="completed",
                job_status=job_status,
                finished_at=utc_now(),
                total_categories=self._stats["categories_discovered"],
                completed_categories=succeeded,
                categories_succeeded=succeeded,
                categories_failed=failed,
                categories_skipped=self._stats["categories_skipped"],
                total_products=self._stats["products_total"],
                new_products=self._stats["products_new"],
                updated_products=self._stats["products_updated"],
                failed_products=self._stats["products_failed"],
                relationships_created=self._stats["relationships_created"],
                relationships_existing=self._stats["relationships_existing"],
                category_discrepancies=self._stats["category_discrepancies"],
                summary=json.dumps(summary, default=str),
            )

    # ------------------------------------------------------------- export

    async def _export_json(self):
        """Export scraped data to JSON files for debugging."""
        export_dir = Path(settings.json_export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)

        async with async_session_factory() as db:
            from app.router import _build_category_tree

            categories = await crud.get_all_categories(db)
            cat_tree = _build_category_tree(categories)
            with open(export_dir / "categories.json", "w", encoding="utf-8") as f:
                json.dump(cat_tree, f, ensure_ascii=False, indent=2)
            logger.info(
                "Exported %d root categories (hierarchical) to %s",
                len(cat_tree),
                export_dir / "categories.json",
            )

            brands_raw = await crud.get_brands(db)
            brand_data = [
                {"name": r["brand"], "product_count": r["product_count"]}
                for r in brands_raw
            ]
            with open(export_dir / "brands.json", "w", encoding="utf-8") as f:
                json.dump(brand_data, f, ensure_ascii=False, indent=2)
            logger.info(
                "Exported %d brands to %s", len(brand_data), export_dir / "brands.json"
            )

            products_dir = export_dir / "products"
            products_dir.mkdir(parents=True, exist_ok=True)
            # Stream products in id-keyed batches instead of loading the full
            # catalog (products + selectin images/attributes/categories) into
            # one ORM identity map, which is a memory spike after a full scrape.
            exported = 0
            after_id = 0
            while True:
                batch = await crud.get_products_batch(db, after_id=after_id, batch=200)
                if not batch:
                    break
                for p in batch:
                    detail = ProductDetail.model_validate(p)
                    sku = p.sku.replace("/", "_").replace("\\", "_")
                    with open(
                        products_dir / f"{sku}.json", "w", encoding="utf-8"
                    ) as f:
                        json.dump(detail.model_dump(), f, ensure_ascii=False, indent=2)
                    exported += 1
                after_id = batch[-1].id
            logger.info(
                "Exported %d products to %s", exported, products_dir
            )
