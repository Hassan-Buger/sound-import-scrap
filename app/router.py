import asyncio
import json
import logging
from datetime import datetime, date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Product, Category, ScrapeJob
from app.schemas import (
    CategoryOut, BrandOut, ProductDetail, ProductListItem,
    ProductsResponse, ChangedProductsResponse, StatsOut, SyncResponse,
)
from app import crud

logger = logging.getLogger("app.router")
router = APIRouter()


@router.get("/categories", response_model=List[CategoryOut])
async def list_categories(
    db: AsyncSession = Depends(get_db),
):
    cats = await crud.get_all_categories(db)
    return cats


@router.get("/brands", response_model=List[BrandOut])
async def list_brands(
    db: AsyncSession = Depends(get_db),
):
    raw = await crud.get_brands(db)
    return [BrandOut(name=r["brand"], product_count=r["product_count"]) for r in raw]


@router.get("/products", response_model=ProductsResponse)
async def list_products(
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    brand: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: Optional[str] = Query(None, description="Field to sort by"),
    sort_order: Optional[str] = Query("desc", description="asc or desc"),
    db: AsyncSession = Depends(get_db),
):
    products, total = await crud.get_products_paginated(
        db, page=page, per_page=limit, brand=brand,
        category_slug=category, search=search,
        sort_by=sort_by, sort_order=sort_order,
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
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductDetail.model_validate(product)


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
    return ProductDetail.model_validate(product)


@router.get("/products/changed", response_model=ChangedProductsResponse)
async def changed_products(
    since: str = Query(..., description="Date in YYYY-MM-DD format"),
    db: AsyncSession = Depends(get_db),
):
    try:
        since_date = datetime.strptime(since, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

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
            select(func.count(func.distinct(Product.brand)))
            .where(Product.brand.isnot(None), Product.brand != "")
        )
    ).scalar() or 0

    last_job = await db.execute(
        select(ScrapeJob).where(ScrapeJob.status == "completed").order_by(ScrapeJob.finished_at.desc()).limit(1)
    )
    last_sync_job = last_job.scalar_one_or_none()
    last_sync = last_sync_job.finished_at.isoformat() if last_sync_job and last_sync_job.finished_at else None

    return StatsOut(
        total_products=total_products,
        total_categories=total_categories,
        total_brands=total_brands,
        last_sync=last_sync,
    )


@router.post("/sync", response_model=SyncResponse)
async def trigger_sync(
    background_tasks: BackgroundTasks,
    incremental: bool = Query(False, description="Run incremental scrape"),
    db: AsyncSession = Depends(get_db),
):
    job_id = await crud.create_scrape_job(db, "incremental" if incremental else "full")

    async def run_scrape(job_id: int):
        try:
            from app.database import async_session_factory
            from scraper.soundimports import SoundImportsScraper
            from scraper.pipeline import ScrapePipeline

            supplier = SoundImportsScraper()
            pipeline = ScrapePipeline(supplier, full=not incremental)
            stats = await pipeline.run()

            async with async_session_factory() as session:
                await crud.update_scrape_job(
                    session, job_id,
                    status="completed",
                    finished_at=datetime.utcnow(),
                    total_categories=stats.get("categories_completed", 0),
                    total_products=stats.get("products_total", 0),
                    new_products=stats.get("products_new", 0),
                    updated_products=stats.get("products_updated", 0),
                    failed_products=stats.get("products_failed", 0),
                )
        except Exception as e:
            logger.exception("Background scrape failed: %s", e)
            async with async_session_factory() as session:
                await crud.update_scrape_job(
                    session, job_id,
                    status="failed",
                    finished_at=datetime.utcnow(),
                    errors=str(e),
                )

    background_tasks.add_task(run_scrape, job_id)

    return SyncResponse(
        job_id=job_id,
        status="running",
        message=f"{'Incremental' if incremental else 'Full'} scrape started (job #{job_id})",
    )
