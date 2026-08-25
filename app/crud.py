from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Any, Set
from sqlalchemy import select, func, and_, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models import (
    Category,
    Product,
    Image,
    Attribute,
    ScrapeJob,
    ScrapeProgress,
    product_categories,
)
from app.timeutils import utc_now

ACTIVE_SCRAPE_KEY = "scrape"

# ---------------------------------------------------------------------------
# Category lookup
# ---------------------------------------------------------------------------


async def get_categories(
    db: AsyncSession,
    parent_id: Optional[int] = None,
) -> List[Category]:
    stmt = (
        select(Category).where(Category.parent_id == parent_id).order_by(Category.name)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_all_categories(db: AsyncSession) -> List[Category]:
    stmt = (
        select(Category)
        .options(selectinload(Category.children))
        .order_by(Category.level, Category.name)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_category_by_slug(db: AsyncSession, slug: str) -> Optional[Category]:
    stmt = (
        select(Category)
        .where(Category.slug == slug)
        .order_by(Category.id)
        .options(selectinload(Category.children))
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_category_by_path(
    db: AsyncSession, canonical_path: str
) -> Optional[Category]:
    stmt = (
        select(Category)
        .where(Category.canonical_path == canonical_path)
        .options(selectinload(Category.children))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_category_by_id(db: AsyncSession, category_id: int) -> Optional[Category]:
    stmt = (
        select(Category)
        .where(Category.id == category_id)
        .options(selectinload(Category.children))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_category_by_id_or_slug(
    db: AsyncSession, id_or_slug: str
) -> Optional[Category]:
    """Resolve a category by numeric id, canonical path, or slug (backwards
    compatible). Slug matching is a convenience and may be ambiguous; the
    lowest-id match wins."""
    if isinstance(id_or_slug, int) or (
        isinstance(id_or_slug, str) and id_or_slug.isdigit()
    ):
        cat = await get_category_by_id(db, int(id_or_slug))
        if cat:
            return cat

    from scraper.urlutils import normalize_category_path

    path = normalize_category_path(str(id_or_slug))
    if path and path != "/":
        cat = await get_category_by_path(db, path)
        if cat:
            return cat

    return await get_category_by_slug(db, str(id_or_slug))


def collect_family_ids(category: Category, all_cats: List[Category]) -> Set[int]:
    """Return {category} plus the ids of all descendants (in-memory walk)."""
    family = {category.id}
    child_map: Dict[int, List[Category]] = {}
    for c in all_cats:
        child_map.setdefault(c.parent_id, []).append(c)

    stack = [category.id]
    while stack:
        pid = stack.pop()
        for child in child_map.get(pid, []):
            if child.id not in family:
                family.add(child.id)
                stack.append(child.id)
    return family


async def get_category_family_ids(db: AsyncSession, category: Category) -> List[int]:
    all_cats = await get_all_categories(db)
    return sorted(collect_family_ids(category, all_cats))


# ---------------------------------------------------------------------------
# Category upsert / reconciliation
# ---------------------------------------------------------------------------


async def upsert_category_with_parent(
    db: AsyncSession,
    name: str,
    slug: str,
    canonical_path: str,
    url: str,
    level: int = 0,
    parent_path_ref: Optional[str] = None,
    source_count: int = 0,
) -> Category:
    """Upsert a category keyed by canonical_path, resolving its parent by the
    parent's canonical path rather than by slug.
    """
    parent_id = None
    if parent_path_ref and parent_path_ref != "/":
        parent = await get_category_by_path(db, parent_path_ref)
        if parent:
            parent_id = parent.id

    category = await get_category_by_path(db, canonical_path)
    if category:
        category.name = name
        category.slug = slug
        category.url = url
        category.level = level
        category.parent_id = parent_id
        category.source_product_count = source_count
        if not category.is_active:
            category.is_active = True
        category.missing_streak = 0
        category.source_checked_at = utc_now()
        category.updated_at = utc_now()
    else:
        category = Category(
            name=name,
            slug=slug,
            canonical_path=canonical_path,
            url=url,
            level=level,
            parent_id=parent_id,
            source_product_count=source_count,
            is_active=True,
            missing_streak=0,
            source_checked_at=utc_now(),
            scrape_status="discovered",
        )
        db.add(category)

    await db.flush()
    return category


async def deactivate_categories_not_in(
    db: AsyncSession,
    active_paths: Set[str],
    confirmation_threshold: int = 2,
) -> int:
    """Reconcile source absence without deleting rows.

    A category must be absent from ``confirmation_threshold`` successful
    sitemap discoveries before it is made inactive. A transiently incomplete
    sitemap therefore cannot deactivate a production branch in one run.
    """
    threshold = max(1, confirmation_threshold)
    stmt = select(Category)
    result = await db.execute(stmt)
    deactivated = 0
    for c in result.scalars().all():
        if c.canonical_path in active_paths:
            c.missing_streak = 0
            continue
        c.missing_streak = (c.missing_streak or 0) + 1
        if c.is_active and c.missing_streak >= threshold:
            c.is_active = False
            deactivated += 1
    await db.commit()
    return deactivated


# ---------------------------------------------------------------------------
# Product / category association
# ---------------------------------------------------------------------------


async def set_product_categories(
    db: AsyncSession,
    product_id: int,
    canonical_paths: List[str],
) -> Tuple[int, int]:
    """Idempotently associate a product with the categories identified by
    canonical paths. Returns ``(created, existing)`` counts."""
    created = 0
    existing = 0

    paths = [p for p in {str(x) for x in canonical_paths if x}]
    if not paths:
        return 0, 0

    result = await db.execute(
        select(Category.id).where(Category.canonical_path.in_(paths))
    )
    category_ids = {row[0] for row in result}
    if not category_ids:
        return 0, 0

    existing_rows = await db.execute(
        select(product_categories.c.category_id).where(
            and_(
                product_categories.c.product_id == product_id,
                product_categories.c.category_id.in_(category_ids),
            )
        )
    )
    existing_ids = {row[0] for row in existing_rows}

    missing = [
        {"product_id": product_id, "category_id": cid, "created_at": utc_now()}
        for cid in category_ids
        if cid not in existing_ids
    ]
    if missing:
        dialect = db.bind.dialect.name if db.bind is not None else ""
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert

            stmt = dialect_insert(product_categories).values(missing)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["product_id", "category_id"]
            )
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert

            stmt = dialect_insert(product_categories).values(missing)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["product_id", "category_id"]
            )
        else:
            stmt = product_categories.insert().values(missing)
        await db.execute(stmt)

    after_rows = await db.execute(
        select(product_categories.c.category_id).where(
            and_(
                product_categories.c.product_id == product_id,
                product_categories.c.category_id.in_(category_ids),
            )
        )
    )
    after_ids = {row[0] for row in after_rows}
    created = len(after_ids - existing_ids)
    existing = len(after_ids) - created
    return created, existing


async def replace_category_products(
    db: AsyncSession,
    canonical_path: str,
    product_ids: Set[int],
) -> int:
    """Remove stale direct memberships after a fully successful category run.

    This is deliberately called only when every page and product detail for the
    category completed. On partial failure, existing relationships are retained.
    Returns the number of stale relationships removed.
    """
    category = await get_category_by_path(db, canonical_path)
    if category is None:
        return 0
    existing_result = await db.execute(
        select(product_categories.c.product_id).where(
            product_categories.c.category_id == category.id
        )
    )
    existing_ids = {row[0] for row in existing_result}
    stale_ids = existing_ids - set(product_ids)
    if stale_ids:
        await db.execute(
            delete(product_categories).where(
                and_(
                    product_categories.c.category_id == category.id,
                    product_categories.c.product_id.in_(stale_ids),
                )
            )
        )
    await db.commit()
    return len(stale_ids)


async def recompute_category_counts(
    db: AsyncSession, family: bool = True
) -> Dict[int, int]:
    """Recompute stored category product counts from the association table.

    ``family=True`` counts products in the category OR any descendant, which
    matches the counts SoundImports shows next to each category. ``family=False``
    counts direct memberships only.
    """
    all_cats = await get_all_categories(db)
    if not all_cats:
        return {}

    rows = await db.execute(
        select(product_categories.c.category_id, product_categories.c.product_id)
    )
    direct_products: Dict[int, Set[int]] = {}
    for category_id, product_id in rows.all():
        direct_products.setdefault(category_id, set()).add(product_id)

    final: Dict[int, int] = (
        {cid: len(product_ids) for cid, product_ids in direct_products.items()}
        if not family
        else {}
    )
    if family:
        for cat in all_cats:
            family_ids = collect_family_ids(cat, all_cats)
            family_products: Set[int] = set()
            for category_id in family_ids:
                family_products.update(direct_products.get(category_id, set()))
            final[cat.id] = len(family_products)

    for cat in all_cats:
        new_count = final.get(cat.id, 0)
        if cat.product_count != new_count:
            cat.product_count = new_count
    await db.commit()
    return final


# ---------------------------------------------------------------------------
# Product query helpers
# ---------------------------------------------------------------------------

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
    query,
    count_query,
    brand: Optional[str],
    category_ids: Optional[List[int]],
    search: Optional[str],
    stock_status: Optional[str] = None,
):
    if brand:
        query = query.where(Product.brand == brand)
        count_query = count_query.where(Product.brand == brand)
    if category_ids:
        query = (
            query.join(
                product_categories, Product.id == product_categories.c.product_id
            )
            .where(product_categories.c.category_id.in_(category_ids))
            .distinct()
        )
        count_query = (
            count_query.join(
                product_categories, Product.id == product_categories.c.product_id
            )
            .where(product_categories.c.category_id.in_(category_ids))
            .distinct()
        )
    if search:
        pattern = f"%{search}%"
        filter_cond = (
            Product.title.ilike(pattern)
            | Product.sku.ilike(pattern)
            | Product.brand.ilike(pattern)
        )
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


async def _resolve_category_scope(
    db: AsyncSession,
    category_slug: Optional[str],
    category_id: Optional[int],
    include_children: bool,
) -> Optional[List[int]]:
    if category_id is not None:
        cat = await get_category_by_id(db, category_id)
        if not cat:
            return None
        if include_children:
            return await get_category_family_ids(db, cat)
        return [cat.id]
    if category_slug:
        cat = await get_category_by_id_or_slug(db, category_slug)
        if not cat:
            return None
        if include_children:
            return await get_category_family_ids(db, cat)
        return [cat.id]
    return None


async def get_products_paginated(
    db: AsyncSession,
    page: int = 1,
    per_page: int = 50,
    brand: Optional[str] = None,
    category_slug: Optional[str] = None,
    category_id: Optional[int] = None,
    search: Optional[str] = None,
    stock_status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    include_children: bool = False,
) -> Tuple[List[Product], int]:
    query = select(Product)
    count_query = select(func.count(func.distinct(Product.id)))

    category_ids = await _resolve_category_scope(
        db, category_slug, category_id, include_children
    )
    if category_ids is None and (category_slug or category_id is not None):
        # requested category does not exist -> empty result, not LIKE fallback
        return [], 0

    query, count_query = _apply_filters(
        query,
        count_query,
        brand,
        category_ids,
        search,
        stock_status,
    )

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = _apply_sorting(query, sort_by, sort_order)
    query = (
        query.offset((page - 1) * per_page)
        .limit(per_page)
        .options(
            selectinload(Product.images),
            selectinload(Product.attributes_rel),
            selectinload(Product.categories),
        )
    )

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
    count_query = select(func.count(func.distinct(Product.id)))

    category_ids = await _resolve_category_scope(db, category_slug, None, False)
    query, count_query = _apply_filters(query, count_query, brand, category_ids, search)
    query = _apply_sorting(query, sort_by, sort_order).options(
        selectinload(Product.images),
        selectinload(Product.attributes_rel),
        selectinload(Product.categories),
    )

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_product_by_id(db: AsyncSession, product_id: int) -> Optional[Product]:
    stmt = (
        select(Product)
        .where(Product.id == product_id)
        .options(
            selectinload(Product.images),
            selectinload(Product.attributes_rel),
            selectinload(Product.categories),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_product_by_sku(db: AsyncSession, sku: str) -> Optional[Product]:
    stmt = (
        select(Product)
        .where(Product.sku == sku)
        .options(
            selectinload(Product.images),
            selectinload(Product.attributes_rel),
            selectinload(Product.categories),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_all_products(db: AsyncSession) -> List[Product]:
    stmt = select(Product).options(
        selectinload(Product.images), selectinload(Product.categories)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_products_batch(
    db: AsyncSession, after_id: int = 0, batch: int = 200
) -> List[Product]:
    """Return up to ``batch`` products with ``id > after_id`` in id order.

    Used by the JSON exporter so it never has to hold the entire catalog (with
    selectin-loaded images/attributes/categories) in one ORM identity map.
    """
    stmt = (
        select(Product)
        .where(Product.id > after_id)
        .order_by(Product.id)
        .limit(batch)
        .options(
            selectinload(Product.images),
            selectinload(Product.attributes_rel),
            selectinload(Product.categories),
        )
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_direct_product_counts(db: AsyncSession) -> Dict[str, int]:
    """Map of canonical category path -> number of direct M2M relationships.

    Direct counts are how many rows exist in ``product_categories`` for a
    category, i.e. products linked to that category without considering
    descendants. The family count is ``categories.product_count``.
    """
    cats = await get_all_categories(db)
    path_by_id = {c.id: c.canonical_path for c in cats}
    rows = await db.execute(
        select(
            product_categories.c.category_id,
            func.count(product_categories.c.product_id),
        ).group_by(product_categories.c.category_id)
    )
    counts: Dict[str, int] = {}
    for category_id, n in rows:
        path = path_by_id.get(category_id)
        if path:
            counts[path] = n
    return counts


async def get_coverage_snapshot(db: AsyncSession) -> Dict[str, Any]:
    """Compact data-quality snapshot used by ``check-coverage``.

    Distinguishes unique products from product/category *appearances* (the
    first-version scraper miscounted appearances as products) and reports
    products stored without detail (listing-fallback only).
    """
    total_products = (
        await db.execute(select(func.count(Product.id)))
    ).scalar() or 0
    fallback_only = (
        await db.execute(
            select(func.count(Product.id)).where(Product.brand.is_(None))
        )
    ).scalar() or 0
    appearances = (
        await db.execute(select(func.count()).select_from(product_categories))
    ).scalar() or 0
    distinct_linked = (
        await db.execute(
            select(
                func.count(func.distinct(product_categories.c.product_id))
            ).select_from(product_categories)
        )
    ).scalar() or 0
    categories_total = (
        await db.execute(select(func.count(Category.id)))
    ).scalar() or 0
    last_job = (
        await db.execute(select(ScrapeJob).order_by(ScrapeJob.id.desc()).limit(1))
    ).scalar_one_or_none()
    return {
        "total_products": total_products,
        "fallback_only_products": fallback_only,
        "category_appearances": appearances,
        "distinct_linked_products": distinct_linked,
        "categories_total": categories_total,
        "last_job": {
            "id": last_job.id,
            "status": getattr(last_job, "status", None),
            "job_status": getattr(last_job, "job_status", None),
            "failed": getattr(last_job, "failed", None),
            "succeeded": getattr(last_job, "succeeded", None),
            "finished_at": (
                last_job.finished_at.isoformat() if last_job.finished_at else None
            ),
        }
        if last_job is not None
        else None,
    }


async def get_changed_products_since(db: AsyncSession, since: datetime) -> List[int]:
    stmt = (
        select(Product.id)
        .where(Product.updated_at >= since)
        .order_by(Product.updated_at.desc())
    )
    result = await db.execute(stmt)
    return [row[0] for row in result]


async def get_brands(db: AsyncSession) -> List[dict]:
    stmt = (
        select(Product.brand, func.count(Product.id).label("product_count"))
        .where(Product.brand.isnot(None), Product.brand != "")
        .group_by(Product.brand)
        .order_by(Product.brand)
    )
    result = await db.execute(stmt)
    return [{"brand": row[0], "product_count": row[1]} for row in result]


# ---------------------------------------------------------------------------
# Product upsert
# ---------------------------------------------------------------------------


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
    product_categories_paths: Optional[List[str]] = None,
) -> Tuple[Product, bool, Tuple[int, int]]:
    """Upsert a product by SKU.

    Returns ``(product, is_new, (relationships_created, relationships_existing))``.
    """
    result = await db.execute(select(Product).where(Product.sku == sku))
    product = result.scalar_one_or_none()
    is_new = product is None

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
        if stock is not None:
            product.stock = stock
        elif product.stock is None:
            product.stock = stock
        if stock_status is not None:
            product.stock_status = stock_status
        product.brand = brand or product.brand
        product.currency = currency
        product.url = url or product.url
        if category_ids:
            product.category_ids = category_ids
        if raw_json is not None:
            product.raw_json = raw_json
        product.is_active = True
        product.updated_at = utc_now()
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
        old_images = (
            (await db.execute(select(Image).where(Image.product_id == product.id)))
            .scalars()
            .all()
        )
        if len(images) > 0 or not old_images:
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
        old_attrs = (
            (
                await db.execute(
                    select(Attribute).where(Attribute.product_id == product.id)
                )
            )
            .scalars()
            .all()
        )
        if len(attributes) > 0 or not old_attrs:
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

    rel_created, rel_existing = 0, 0
    if product_categories_paths:
        rel_created, rel_existing = await set_product_categories(
            db, product.id, product_categories_paths
        )

    await db.commit()
    await db.refresh(product)
    return product, is_new, (rel_created, rel_existing)


# ---------------------------------------------------------------------------
# Scrape job / progress state
# ---------------------------------------------------------------------------


async def create_scrape_job(db: AsyncSession, job_type: str) -> int:
    now = utc_now()
    job = ScrapeJob(
        job_type=job_type,
        active_key=ACTIVE_SCRAPE_KEY,
        status="running",
        job_status="RUNNING",
        started_at=now,
        heartbeat_at=now,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job.id


async def create_or_resume_scrape_job(
    db: AsyncSession,
    job_type: str,
    stale_after_seconds: int = 120,
) -> Tuple[int, str]:
    """Claim a scrape job, returning ``(id, claim_state)``.

    ``claim_state`` is ``already_running``, ``resumed``, or ``created``. This
    prevents a second API/CLI invocation from launching duplicate work.
    """
    running_result = await db.execute(
        select(ScrapeJob)
        .where(
            ScrapeJob.job_type == job_type,
            ScrapeJob.status == "running",
        )
        .order_by(ScrapeJob.id.desc())
    )
    running_jobs = list(running_result.scalars().all())
    stale_jobs = []
    for running in running_jobs:
        last_seen = running.heartbeat_at or running.started_at or running.created_at
        cutoff = utc_now() - timedelta(seconds=max(1, stale_after_seconds))
        if last_seen and last_seen >= cutoff:
            return running.id, "already_running"
        stale_jobs.append(running)

    for running in stale_jobs:
        running.status = "interrupted"
        running.job_status = "INTERRUPTED"
    if stale_jobs:
        await db.commit()

    result = await db.execute(
        select(ScrapeJob)
        .where(
            ScrapeJob.job_type == job_type,
            ScrapeJob.status == "interrupted",
        )
        .order_by(ScrapeJob.id.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if job is None:
        try:
            return await create_scrape_job(db, job_type), "created"
        except IntegrityError:
            await db.rollback()
            winner = await db.execute(
                select(ScrapeJob)
                .where(ScrapeJob.active_key == ACTIVE_SCRAPE_KEY)
                .order_by(ScrapeJob.id.desc())
                .limit(1)
            )
            claimed = winner.scalar_one_or_none()
            if claimed is None:
                raise
            return claimed.id, "already_running"
    job.status = "running"
    job.job_status = "RUNNING"
    job.active_key = ACTIVE_SCRAPE_KEY
    job.finished_at = None
    job.heartbeat_at = utc_now()
    await db.commit()
    return job.id, "resumed"


async def mark_running_jobs_interrupted(
    db: AsyncSession, stale_after_seconds: int = 120
) -> int:
    """Mark only expired-heartbeat jobs as resumable."""
    result = await db.execute(
        select(ScrapeJob).where(ScrapeJob.status == "running")
    )
    cutoff = utc_now() - timedelta(seconds=max(1, stale_after_seconds))
    jobs = [
        job
        for job in result.scalars().all()
        if (job.heartbeat_at or job.started_at or job.created_at) is None
        or (job.heartbeat_at or job.started_at or job.created_at) < cutoff
    ]
    for job in jobs:
        job.status = "interrupted"
        job.job_status = "INTERRUPTED"
    if jobs:
        await db.commit()
    return len(jobs)


async def update_scrape_job(db: AsyncSession, job_id: int, **kwargs):
    result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
    job = result.scalar_one_or_none()
    if job:
        if kwargs.get("status") == "running":
            kwargs.setdefault("active_key", ACTIVE_SCRAPE_KEY)
        if kwargs.get("status") in {"completed", "failed"}:
            kwargs.setdefault("active_key", None)
        for key, value in kwargs.items():
            setattr(job, key, value)
        await db.commit()


async def get_scrape_job(db: AsyncSession, job_id: int) -> Optional[ScrapeJob]:
    result = await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
    return result.scalar_one_or_none()


async def mark_category_discovered(
    db: AsyncSession, job_id: int, category: Category
) -> ScrapeProgress:
    result = await db.execute(
        select(ScrapeProgress).where(
            and_(
                ScrapeProgress.job_id == job_id,
                ScrapeProgress.canonical_path == category.canonical_path,
            )
        )
    )
    progress = result.scalar_one_or_none()
    now = utc_now()
    if progress:
        progress.category_id = category.id
        progress.category_url = category.url
        progress.source_count = category.source_product_count
        # Completed rows are the durable resume checkpoint. Only incomplete
        # states are reset to the queue for a resumed process.
        if not progress.completed:
            progress.status = "discovered"
            progress.started_at = None
            progress.completed_at = None
        return progress
    progress = ScrapeProgress(
        job_id=job_id,
        category_id=category.id,
        category_url=category.url,
        canonical_path=category.canonical_path,
        status="discovered",
        source_count=category.source_product_count,
        created_at=now,
        updated_at=now,
    )
    db.add(progress)
    await db.flush()
    return progress


async def get_job_progress(
    db: AsyncSession, job_id: int
) -> List[ScrapeProgress]:
    result = await db.execute(
        select(ScrapeProgress)
        .where(ScrapeProgress.job_id == job_id)
        .order_by(ScrapeProgress.id)
    )
    return list(result.scalars().all())


async def update_category_scrape_state(
    db: AsyncSession,
    canonical_path: str,
    **kwargs,
) -> Optional[Category]:
    category = await get_category_by_path(db, canonical_path)
    if category is None:
        return None
    for key, value in kwargs.items():
        setattr(category, key, value)
    category.updated_at = utc_now()
    await db.commit()
    return category


async def update_category_progress(
    db: AsyncSession,
    job_id: int,
    canonical_path: str,
    **kwargs,
) -> Optional[ScrapeProgress]:
    result = await db.execute(
        select(ScrapeProgress).where(
            and_(
                ScrapeProgress.job_id == job_id,
                ScrapeProgress.canonical_path == canonical_path,
            )
        )
    )
    progress = result.scalar_one_or_none()
    if not progress:
        return None
    now = utc_now()
    for key, value in kwargs.items():
        setattr(progress, key, value)
    progress.updated_at = now
    await db.commit()
    return progress


# Keep the legacy helper names working where the scraper previously used them.
async def upsert_scrape_progress(
    db: AsyncSession, job_id: int, category_url: str, **kwargs
):
    result = await db.execute(
        select(ScrapeProgress).where(
            and_(
                ScrapeProgress.job_id == job_id,
                ScrapeProgress.category_url == category_url,
            )
        )
    )
    progress = result.scalar_one_or_none()
    if progress:
        for key, value in kwargs.items():
            setattr(progress, key, value)
        progress.updated_at = utc_now()
    else:
        progress = ScrapeProgress(job_id=job_id, category_url=category_url, **kwargs)
        db.add(progress)
    await db.commit()


async def get_scrape_progress(
    db: AsyncSession, job_id: int, category_url: str
) -> Optional[ScrapeProgress]:
    result = await db.execute(
        select(ScrapeProgress).where(
            and_(
                ScrapeProgress.job_id == job_id,
                ScrapeProgress.category_url == category_url,
            )
        )
    )
    return result.scalar_one_or_none()
