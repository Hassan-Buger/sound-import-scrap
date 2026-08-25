import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Product, Category, ScrapeJob
from app.schemas import (
    CategoryOut,
    BrandOut,
    ProductDetail,
    ProductListItem,
    ProductDescriptionOut,
    SpecificationOut,
    ProductsResponse,
    ChangedProductsResponse,
    StatsOut,
    SyncResponse,
)
from app import crud
from app.config import settings
from app.timeutils import utc_now

logger = logging.getLogger("app.router")
router = APIRouter()


def _build_category_tree(categories: List[Category]) -> List[dict]:
    """Return a stable, parent-to-child category tree without ORM recursion.

    Serializing a self-referential ORM relationship directly is fragile: it can
    trigger lazy loads after the async session context and, on historical
    deployments, has exposed the parent as ``children``.  Build plain response
    objects from ``parent_id`` instead so the public API is deterministic.
    """
    nodes = {
        category.id: {
            "id": category.id,
            "parent_id": category.parent_id,
            "name": category.name,
            "slug": category.slug,
            "canonical_path": category.canonical_path,
            "level": category.level,
            "product_count": category.product_count,
            "source_product_count": category.source_product_count,
            "is_active": category.is_active,
            "children": [],
        }
        for category in categories
    }

    roots = []
    for category in categories:
        node = nodes[category.id]
        parent = nodes.get(category.parent_id)
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)

    return roots


@router.get("/categories", response_model=List[CategoryOut])
async def list_categories(
    db: AsyncSession = Depends(get_db),
):
    # Return one hierarchy rooted at top-level categories.  Building this from
    # parent_id avoids recursive ORM serialization and guarantees that children
    # are nested beneath their actual parent, never the other way round.
    return _build_category_tree(await crud.get_all_categories(db))


@router.get("/category/{category_id_or_slug}", response_model=CategoryOut)
async def get_category(
    category_id_or_slug: str,
    db: AsyncSession = Depends(get_db),
):
    cat = await crud.get_category_by_id_or_slug(db, category_id_or_slug)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


async def _enrich_product_if_needed(
    db: AsyncSession, product: Product, force: bool = False
) -> Product:
    """If a product has stock=None, stock_status=None, or force=True, fetch live details and update DB."""
    if not force and product.stock is not None and product.stock_status is not None:
        return product

    if not product.url:
        return product

    from scraper.soundimports import SoundImportsScraper

    supplier = SoundImportsScraper()
    try:
        detail_data = await supplier.get_product_detail(product.url)
        html_doc = None
        try:
            html_doc = await supplier._client.fetch_html(product.url)
        except Exception:
            pass

        cat_path = (
            product.categories[0].canonical_path
            if product.categories
            else None
        )
        prod_data = supplier.extract_product_detail(
            detail_data,
            category_slug=cat_path,
            html_doc=html_doc,
        )
        db_prod, _, _ = await crud.upsert_product(
            db,
            product_id=prod_data["product_id"],
            sku=prod_data["sku"],
            ean=prod_data.get("ean"),
            title=prod_data.get("title"),
            description=prod_data.get("description"),
            short_description=prod_data.get("short_description"),
            long_description=prod_data.get("long_description"),
            regular_price=prod_data.get("regular_price"),
            price=prod_data.get("price"),
            stock=prod_data.get("stock"),
            stock_status=prod_data.get("stock_status"),
            brand=prod_data.get("brand"),
            currency=prod_data.get("currency", "EUR"),
            url=prod_data.get("url"),
            category_ids=product.category_ids or "",
            raw_json=prod_data.get("raw_json"),
            images=prod_data.get("images", []),
            attributes=prod_data.get("attributes", []),
            product_categories_paths=[cat_path] if cat_path else [],
        )
        await db.commit()
        await db.refresh(db_prod)
        return db_prod
    except Exception as exc:
        logger.warning("Could not auto-enrich product %s: %s", product.sku, exc)
        return product
    finally:
        await supplier._client.close()


@router.get("/category/{category_id_or_slug}/products", response_model=ProductsResponse)
async def list_category_products(
    category_id_or_slug: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    brand: Optional[str] = None,
    search: Optional[str] = None,
    stock_status: Optional[str] = None,
    include_children: bool = Query(
        False,
        description="If true, include products in sub-child categories. Default false (selected category only).",
    ),
    sort_by: Optional[str] = Query(None, description="Field to sort by"),
    sort_order: Optional[str] = Query("desc", description="asc or desc"),
    db: AsyncSession = Depends(get_db),
):
    if stock_status and stock_status not in (
        "in_stock",
        "out_of_stock",
        "on_backorder",
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid stock_status. Use: in_stock, out_of_stock, on_backorder",
        )

    cat = await crud.get_category_by_id_or_slug(db, category_id_or_slug)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    products, total = await crud.get_products_paginated(
        db,
        page=page,
        per_page=limit,
        brand=brand,
        category_id=cat.id,
        search=search,
        stock_status=stock_status,
        sort_by=sort_by,
        sort_order=sort_order,
        include_children=include_children,
    )

    # Auto-enrich legacy database products where stock is None
    unpopulated = [p for p in products if p.stock is None and p.url]
    if unpopulated:
        enriched_products = []
        for p in products:
            if p.stock is None and p.url:
                enriched = await _enrich_product_if_needed(db, p)
                enriched_products.append(enriched)
            else:
                enriched_products.append(p)
        products = enriched_products

    return ProductsResponse(
        total=total,
        page=page,
        limit=limit,
        products=[ProductListItem.model_validate(p) for p in products],
    )


@router.get("/brands", response_model=List[BrandOut])
async def list_brands(
    db: AsyncSession = Depends(get_db),
):
    raw = await crud.get_brands(db)
    return [BrandOut(name=r["brand"], product_count=r["product_count"]) for r in raw]


@router.get("/products", response_model=ProductsResponse)
async def list_products(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    brand: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    stock_status: Optional[str] = None,
    sort_by: Optional[str] = Query(None, description="Field to sort by"),
    sort_order: Optional[str] = Query("desc", description="asc or desc"),
    db: AsyncSession = Depends(get_db),
):
    if stock_status and stock_status not in (
        "in_stock",
        "out_of_stock",
        "on_backorder",
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid stock_status. Use: in_stock, out_of_stock, on_backorder",
        )
    products, total = await crud.get_products_paginated(
        db,
        page=page,
        per_page=limit,
        brand=brand,
        category_slug=category,
        search=search,
        stock_status=stock_status,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return ProductsResponse(
        total=total,
        page=page,
        limit=limit,
        products=[ProductListItem.model_validate(p) for p in products],
    )


@router.get("/product/{product_id}", response_model=ProductDetail)
async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    product = await crud.get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(
            status_code=404,
            detail={"message": "Product not found.", "error_code": "PRODUCT_NOT_FOUND"},
        )
    if product.stock is None and product.url:
        product = await _enrich_product_if_needed(db, product)
    return ProductDetail.model_validate(product)


@router.get("/product/{product_id}/description", response_model=ProductDescriptionOut)
async def get_product_description(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    product = await crud.get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(
            status_code=404,
            detail={"message": "Product not found.", "error_code": "PRODUCT_NOT_FOUND"},
        )
    return ProductDescriptionOut.model_validate(product)


@router.get(
    "/product/{product_id}/specifications", response_model=List[SpecificationOut]
)
async def get_product_specifications(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    product = await crud.get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(
            status_code=404,
            detail={"message": "Product not found.", "error_code": "PRODUCT_NOT_FOUND"},
        )
    attrs_sorted = sorted(product.attributes_rel, key=lambda a: a.sort_order or 0)
    return [
        SpecificationOut(
            name=a.attribute_name, value=a.attribute_value, sort_order=a.sort_order or 0
        )
        for a in attrs_sorted
    ]


@router.get("/product/sku/{sku:path}", response_model=ProductDetail)
async def get_product_by_sku(
    sku: str,
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import unquote

    sku = unquote(sku)
    product = await crud.get_product_by_sku(db, sku)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.stock is None and product.url:
        product = await _enrich_product_if_needed(db, product)
    return ProductDetail.model_validate(product)


@router.get("/products/changed", response_model=ChangedProductsResponse)
async def changed_products(
    since: str = Query(..., description="Date in YYYY-MM-DD format"),
    db: AsyncSession = Depends(get_db),
):
    try:
        since_date = datetime.strptime(since, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Use YYYY-MM-DD."
        )

    product_ids = await crud.get_changed_products_since(db, since_date)
    return ChangedProductsResponse(
        since=since,
        total=len(product_ids),
        product_ids=product_ids,
    )


@router.get("/stats", response_model=StatsOut)
async def get_stats(
    db: AsyncSession = Depends(get_db),
):
    total_products = (await db.execute(select(func.count(Product.id)))).scalar() or 0
    total_categories = (await db.execute(select(func.count(Category.id)))).scalar() or 0
    total_brands = (
        await db.execute(
            select(func.count(func.distinct(Product.brand))).where(
                Product.brand.isnot(None), Product.brand != ""
            )
        )
    ).scalar() or 0

    last_job = await db.execute(
        select(ScrapeJob)
        .where(ScrapeJob.status == "completed")
        .order_by(ScrapeJob.finished_at.desc())
        .limit(1)
    )
    last_sync_job = last_job.scalar_one_or_none()
    last_sync = (
        last_sync_job.finished_at.isoformat()
        if last_sync_job and last_sync_job.finished_at
        else None
    )

    return StatsOut(
        total_products=total_products,
        total_categories=total_categories,
        total_brands=total_brands,
        last_sync=last_sync,
    )


@router.get("/telemetry")
async def telemetry(limit: int = Query(60, le=500)):
    from app import telemetry as _telemetry

    return await _telemetry.read_telemetry(limit=limit)


@router.post("/sync", response_model=SyncResponse)
async def trigger_sync(
    background_tasks: BackgroundTasks,
    incremental: bool = Query(False, description="Run incremental scrape"),
    db: AsyncSession = Depends(get_db),
):
    job_type = "incremental" if incremental else "full"
    job_id, claim_state = await crud.create_or_resume_scrape_job(
        db, job_type, settings.job_stale_after
    )
    logger.info(
        "Sync trigger: job_id=%d, type=%s, claim=%s",
        job_id,
        job_type,
        claim_state,
    )

    if claim_state == "already_running":
        return SyncResponse(
            job_id=job_id,
            status="running",
            message=f"{job_type.title()} scrape job #{job_id} is already running",
        )

    async def run_scrape(job_id: int):
        logger.info("Background task started: job_id=%d", job_id)
        supplier = None
        try:
            from app.database import async_session_factory
            from scraper.soundimports import SoundImportsScraper
            from scraper.pipeline import ScrapePipeline

            logger.info("Background task imports OK: job_id=%d", job_id)

            supplier = SoundImportsScraper()
            pipeline = ScrapePipeline(supplier, full=not incremental)

            import asyncio as _asyncio
            from app import telemetry as _telemetry

            stop_event = _asyncio.Event()

            async def _sample_loop():
                try:
                    while not stop_event.is_set():
                        await _telemetry.record(phase="pipeline")
                        await _asyncio.sleep(5)
                except Exception:  # noqa: BLE001
                    logger.exception("telemetry sampler died")

            sampler_task = _asyncio.create_task(_sample_loop())
            try:
                logger.info("Calling pipeline.run() for job_id=%d", job_id)
                stats = await pipeline.run(job_id=job_id)
                logger.info(
                    "pipeline.run() returned for job_id=%d: stats=%s",
                    job_id,
                    stats,
                )
            finally:
                stop_event.set()
                await sampler_task

        except Exception as e:
            logger.exception("Background scrape failed: job_id=%d, error=%s", job_id, e)
            try:
                async with async_session_factory() as session:
                    await crud.update_scrape_job(
                        session,
                        job_id,
                        status="failed",
                        job_status="FAILED",
                        finished_at=utc_now(),
                        errors=f"{type(e).__name__}: {e}",
                    )
            except Exception as db_err:
                logger.error(
                    "Failed to update scrape_job %d with failure status: %s",
                    job_id,
                    db_err,
                )
        finally:
            if supplier is not None:
                await supplier._client.close()

    background_tasks.add_task(run_scrape, job_id)

    return SyncResponse(
        job_id=job_id,
        status="running",
        message=(
            f"{job_type.title()} scrape "
            f"{'resumed' if claim_state == 'resumed' else 'started'} (job #{job_id})"
        ),
    )


@router.post(
    "/category/{category_id_or_slug}/sync",
    response_model=ProductsResponse,
    summary="Rescrape and refresh stock & specs for all products in a single category immediately",
)
async def sync_category(
    category_id_or_slug: str,
    db: AsyncSession = Depends(get_db),
):
    """Fetch live data from SoundImports for a specific category and update the DB."""
    cat = await crud.get_category_by_id_or_slug(db, category_id_or_slug)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    from scraper.soundimports import SoundImportsScraper

    supplier = SoundImportsScraper()
    try:
        raw_url = cat.url or cat.canonical_path
        category_url = (
            raw_url
            if str(raw_url).startswith("http")
            else f"{settings.base_url.rstrip('/')}/en/{str(raw_url).lstrip('/')}"
        )
        page_data = await supplier.get_product_list(category_url, page=1, limit=100)
        raw_products = supplier.category_scraper.extract_products(page_data)

        for raw_p in raw_products:
            p_summary = supplier.extract_product_summary(raw_p)
            p_url = p_summary.get("url")
            if not p_url:
                continue
            try:
                detail_data = await supplier.get_product_detail(p_url)
                html_doc = None
                try:
                    html_doc = await supplier._client.fetch_html(p_url)
                except Exception:
                    pass
                prod_data = supplier.extract_product_detail(
                    detail_data,
                    category_slug=cat.canonical_path,
                    html_doc=html_doc,
                )
                await crud.upsert_product(
                    db,
                    product_id=prod_data["product_id"],
                    sku=prod_data["sku"],
                    ean=prod_data.get("ean"),
                    title=prod_data.get("title"),
                    description=prod_data.get("description"),
                    short_description=prod_data.get("short_description"),
                    long_description=prod_data.get("long_description"),
                    regular_price=prod_data.get("regular_price"),
                    price=prod_data.get("price"),
                    stock=prod_data.get("stock"),
                    stock_status=prod_data.get("stock_status"),
                    brand=prod_data.get("brand"),
                    currency=prod_data.get("currency", "EUR"),
                    url=prod_data.get("url"),
                    category_ids=cat.slug,
                    raw_json=prod_data.get("raw_json"),
                    images=prod_data.get("images", []),
                    attributes=prod_data.get("attributes", []),
                    product_categories_paths=[cat.canonical_path],
                )
            except Exception as exc:
                logger.warning("Failed to sync product %s: %s", p_url, exc)

        await db.commit()
    finally:
        await supplier._client.close()

    products, total = await crud.get_products_paginated(
        db,
        category_id=cat.id,
        page=1,
        per_page=100,
    )
    return ProductsResponse(
        total=total,
        page=1,
        limit=100,
        products=[ProductListItem.model_validate(p) for p in products],
    )


@router.post(
    "/product/sku/{sku:path}/sync",
    response_model=ProductDetail,
    summary="Rescrape and refresh stock & specs for a single product by SKU immediately",
)
async def sync_product_by_sku(
    sku: str,
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import unquote

    sku = unquote(sku)
    product = await crud.get_product_by_sku(db, sku)

    from scraper.soundimports import SoundImportsScraper

    supplier = SoundImportsScraper()
    try:
        p_url = (
            product.url
            if product and product.url
            else f"{settings.base_url.rstrip('/')}/en/{sku.lower()}.html"
        )
        detail_data = await supplier.get_product_detail(p_url)
        html_doc = None
        try:
            html_doc = await supplier._client.fetch_html(p_url)
        except Exception:
            pass
        cat_path = (
            product.categories[0].canonical_path
            if product and product.categories
            else None
        )
        prod_data = supplier.extract_product_detail(
            detail_data,
            category_slug=cat_path,
            html_doc=html_doc,
        )
        db_prod, _, _ = await crud.upsert_product(
            db,
            product_id=prod_data["product_id"],
            sku=prod_data["sku"],
            ean=prod_data.get("ean"),
            title=prod_data.get("title"),
            description=prod_data.get("description"),
            short_description=prod_data.get("short_description"),
            long_description=prod_data.get("long_description"),
            regular_price=prod_data.get("regular_price"),
            price=prod_data.get("price"),
            stock=prod_data.get("stock"),
            stock_status=prod_data.get("stock_status"),
            brand=prod_data.get("brand"),
            currency=prod_data.get("currency", "EUR"),
            url=prod_data.get("url"),
            category_ids=product.category_ids if product else "",
            raw_json=prod_data.get("raw_json"),
            images=prod_data.get("images", []),
            attributes=prod_data.get("attributes", []),
            product_categories_paths=[cat_path] if cat_path else [],
        )
        await db.commit()
        await db.refresh(db_prod)
        return ProductDetail.model_validate(db_prod)
    finally:
        await supplier._client.close()


@router.post(
    "/product/{product_id}/sync",
    response_model=ProductDetail,
    summary="Rescrape and refresh stock & specs for a single product by ID immediately",
)
async def sync_product_by_id(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    product = await crud.get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return await sync_product_by_sku(product.sku, db)

