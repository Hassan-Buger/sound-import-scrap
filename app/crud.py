from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy import select, func, text, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Product, Image, Attribute, ScrapeJob, ScrapeProgress


async def get_categories(
    db: AsyncSession,
    parent_id: Optional[int] = None,
) -> List[Category]:
    stmt = select(Category).where(
        Category.parent_id == parent_id
    ).order_by(Category.name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_all_categories(db: AsyncSession) -> List[Category]:
    stmt = select(Category).options(selectinload(Category.children)).order_by(Category.level, Category.name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_category_by_slug(db: AsyncSession, slug: str) -> Optional[Category]:
    stmt = select(Category).where(Category.slug == slug)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_category(
    db: AsyncSession,
    name: str,
    slug: str,
    url: str,
    level: int = 0,
    parent_id: Optional[int] = None,
) -> Category:
    stmt = select(Category).where(Category.slug == slug)
    result = await db.execute(stmt)
    category = result.scalar_one_or_none()

    if category:
        category.name = name
        category.url = url
        category.level = level
        category.parent_id = parent_id
        category.updated_at = datetime.utcnow()
    else:
        category = Category(
            name=name,
            slug=slug,
            url=url,
            level=level,
            parent_id=parent_id,
        )
        db.add(category)

    await db.commit()
    await db.refresh(category)
    return category


SORTABLE_COLUMNS = {
    "sku": "sku",
    "title": "title",
    "brand": "brand",
    "price": "regular_price",
    "stock": "stock",
    "updated_at": "updated_at",
    "created_at": "created_at",
}


def _apply_filters(
    query, count_query,
    brand: Optional[str],
    category_slug: Optional[str],
    search: Optional[str],
    stock_status: Optional[str] = None,
):
    if brand:
        query = query.where(Product.brand == brand)
        count_query = count_query.where(Product.brand == brand)
    if category_slug:
        like_pattern = f"%{category_slug}%"
        query = query.where(Product.category_ids.like(like_pattern))
        count_query = count_query.where(Product.category_ids.like(like_pattern))
    if search:
        pattern = f"%{search}%"
        filter_cond = Product.title.ilike(pattern) | Product.sku.ilike(pattern) | Product.brand.ilike(pattern)
        query = query.where(filter_cond)
        count_query = count_query.where(filter_cond)
    if stock_status:
        query = query.where(Product.stock_status == stock_status)
        count_query = count_query.where(Product.stock_status == stock_status)
    return query, count_query


def _apply_sorting(query, sort_by: Optional[str], sort_order: Optional[str]):
    if sort_by and sort_by in SORTABLE_COLUMNS:
        col = getattr(Product, SORTABLE_COLUMNS[sort_by])
        if sort_order and sort_order.lower() == "asc":
            query = query.order_by(col.asc())
        else:
            query = query.order_by(col.desc())
    else:
        query = query.order_by(Product.updated_at.desc())
    return query


async def get_products_paginated(
    db: AsyncSession,
    page: int = 1,
    per_page: int = 50,
    brand: Optional[str] = None,
    category_slug: Optional[str] = None,
    search: Optional[str] = None,
    stock_status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> Tuple[List[Product], int]:
    query = select(Product)
    count_query = select(func.count(Product.id))

    query, count_query = _apply_filters(query, count_query, brand, category_slug, search, stock_status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = _apply_sorting(query, sort_by, sort_order)
    query = query.offset((page - 1) * per_page).limit(per_page).options(selectinload(Product.images))

    result = await db.execute(query)
    products = list(result.scalars().all())
    return products, total


async def export_products(
    db: AsyncSession,
    brand: Optional[str] = None,
    category_slug: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> List[Product]:
    query = select(Product)
    count_query = select(func.count(Product.id))

    query, count_query = _apply_filters(query, count_query, brand, category_slug, search)
    query = _apply_sorting(query, sort_by, sort_order)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_product_by_id(db: AsyncSession, product_id: int) -> Optional[Product]:
    stmt = (
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.images), selectinload(Product.attributes_rel))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_product_by_sku(db: AsyncSession, sku: str) -> Optional[Product]:
    stmt = (
        select(Product)
        .where(Product.sku == sku)
        .options(selectinload(Product.images), selectinload(Product.attributes_rel))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_changed_products(
    db: AsyncSession,
    hours: int = 24,
    page: int = 1,
    per_page: int = 50,
) -> Tuple[List[Product], int]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    count_query = select(func.count(Product.id)).where(Product.updated_at >= cutoff)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        select(Product)
        .where(Product.updated_at >= cutoff)
        .order_by(Product.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(query)
    products = list(result.scalars().all())
    return products, total


async def get_changed_products_since(
    db: AsyncSession,
    since: datetime,
) -> List[int]:
    stmt = (
        select(Product.id)
        .where(Product.updated_at >= since)
        .order_by(Product.updated_at.desc())
    )
    result = await db.execute(stmt)
    return [row[0] for row in result]


async def get_brands(db: AsyncSession) -> List[dict]:
    stmt = select(
        Product.brand,
        func.count(Product.id).label("product_count"),
    ).where(
        Product.brand.isnot(None),
        Product.brand != "",
    ).group_by(Product.brand).order_by(Product.brand)
    result = await db.execute(stmt)
    return [{"brand": row[0], "product_count": row[1]} for row in result]


async def upsert_product(
    db: AsyncSession,
    product_id: str,
    sku: str,
    ean: Optional[str],
    title: Optional[str],
    description: Optional[str],
    long_description: Optional[str],
    price: Optional[float],
    stock: Optional[int],
    stock_status: Optional[str],
    brand: Optional[str],
    currency: str,
    url: Optional[str],
    category_ids: Optional[str],
    raw_json: Optional[str],
    images: List[dict],
    attributes: List[dict],
    regular_price: Optional[float] = None,
    short_description: Optional[str] = None,
) -> Product:
    stmt = select(Product).where(Product.sku == sku)
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()

    if product:
        product.product_id = product_id or product.product_id
        product.ean = ean or product.ean
        product.title = title or product.title
        if description is not None:
            product.description = description
        if short_description is not None:
            product.short_description = short_description
        if long_description is not None:
            product.long_description = long_description
        if regular_price is not None:
            product.regular_price = regular_price
        product.price = price if price is not None else product.price
        if regular_price is None and price is not None:
            product.regular_price = price
        product.stock = stock
        product.stock_status = stock_status or product.stock_status
        product.brand = brand or product.brand
        product.currency = currency
        product.url = url or product.url
        product.category_ids = category_ids or product.category_ids
        if raw_json is not None:
            product.raw_json = raw_json
        product.is_active = True
        product.updated_at = datetime.utcnow()
    else:
        product = Product(
            product_id=product_id,
            sku=sku,
            ean=ean,
            title=title,
            description=description,
            short_description=short_description or description,
            long_description=long_description,
            regular_price=regular_price if regular_price is not None else price,
            price=price,
            stock=stock,
            stock_status=stock_status,
            brand=brand,
            currency=currency,
            url=url,
            category_ids=category_ids,
            raw_json=raw_json,
            is_active=True,
        )
        db.add(product)

    await db.flush()

    if images is not None:
        old_images_stmt = select(Image).where(Image.product_id == product.id)
        old_images = (await db.execute(old_images_stmt)).scalars().all()
        for old_img in old_images:
            await db.delete(old_img)

        for img_data in images:
            img = Image(
                product_id=product.id,
                image_url=img_data["image_url"],
                sort_order=img_data.get("sort_order", 0),
                is_cover=img_data.get("is_cover", False),
            )
            db.add(img)

    if attributes is not None:
        old_attrs_stmt = select(Attribute).where(Attribute.product_id == product.id)
        old_attrs = (await db.execute(old_attrs_stmt)).scalars().all()
        for old_attr in old_attrs:
            await db.delete(old_attr)

        for attr_data in attributes:
            attr = Attribute(
                product_id=product.id,
                attribute_name=attr_data["attribute_name"],
                attribute_value=attr_data.get("attribute_value"),
                normalized_name=attr_data.get("normalized_name"),
                sort_order=attr_data.get("sort_order", 0),
            )
            db.add(attr)

    await db.commit()
    await db.refresh(product)
    return product


async def create_scrape_job(db: AsyncSession, job_type: str) -> int:
    job = ScrapeJob(job_type=job_type, status="running", started_at=datetime.utcnow())
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job.id


async def update_scrape_job(db: AsyncSession, job_id: int, **kwargs):
    stmt = select(ScrapeJob).where(ScrapeJob.id == job_id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job:
        for key, value in kwargs.items():
            setattr(job, key, value)
        await db.commit()


async def get_scrape_progress(
    db: AsyncSession, job_id: int, category_url: str
) -> Optional:
    stmt = select(ScrapeProgress).where(
        and_(
            ScrapeProgress.job_id == job_id,
            ScrapeProgress.category_url == category_url,
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_scrape_progress(
    db: AsyncSession,
    job_id: int,
    category_url: str,
    page: int = 1,
    completed: bool = False,
    total_pages: int = 0,
    total_products: int = 0,
    errors: Optional[str] = None,
):
    stmt = select(ScrapeProgress).where(
        and_(
            ScrapeProgress.job_id == job_id,
            ScrapeProgress.category_url == category_url,
        )
    )
    result = await db.execute(stmt)
    progress = result.scalar_one_or_none()

    if progress:
        progress.page = page
        progress.completed = completed
        progress.total_pages = total_pages
        progress.total_products = total_products
        if errors:
            progress.errors = errors
        progress.updated_at = datetime.utcnow()
    else:
        progress = ScrapeProgress(
            job_id=job_id,
            category_url=category_url,
            page=page,
            completed=completed,
            total_pages=total_pages,
            total_products=total_products,
            errors=errors,
        )
        db.add(progress)

    await db.commit()
