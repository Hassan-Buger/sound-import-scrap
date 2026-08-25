import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import async_session_factory, Base, engine
from app import crud
from app.schemas import ProductListItem, ProductDetail, ProductsResponse
from scraper.client import HttpClient
from scraper.soundimports import SoundImportsScraper


async def demo():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    client = HttpClient()
    supplier = SoundImportsScraper(client)

    products_to_sync = [
        ("DSPB-KW", "https://www.soundimports.eu/en/dayton-audio-dspb-kw.html"),
        ("DSPB-EC", "https://www.soundimports.eu/en/dayton-audio-dspb-ec.html"),
        ("KAB-FC", "https://www.soundimports.eu/en/dayton-audio-kab-fc.html"),
    ]

    try:
        async with async_session_factory() as db:
            cat = await crud.upsert_category_with_parent(
                db,
                name="Cables Cable Sets",
                slug="cables-cable-sets",
                canonical_path="/en/accessories/amplifier-accessories/cables-cable-sets/",
                url="accessories/amplifier-accessories/cables-cable-sets",
                level=3,
            )
            await db.commit()

        product_ids = []
        for sku, url in products_to_sync:
            raw_detail = await supplier.get_product_detail(url)
            try:
                html = await client.fetch_html(url)
            except Exception:
                html = None

            extracted = supplier.extract_product_detail(
                raw_detail,
                category_slug="/en/accessories/amplifier-accessories/cables-cable-sets/",
                html_doc=html,
            )

            async with async_session_factory() as db:
                db_p, _, _ = await crud.upsert_product(
                    db,
                    product_id=extracted["product_id"],
                    sku=extracted["sku"],
                    ean=extracted.get("ean"),
                    title=extracted.get("title"),
                    description=extracted.get("description"),
                    short_description=extracted.get("short_description"),
                    long_description=extracted.get("long_description"),
                    price=extracted.get("price"),
                    regular_price=extracted.get("regular_price"),
                    stock=extracted.get("stock"),
                    stock_status=extracted.get("stock_status"),
                    brand=extracted.get("brand"),
                    currency="EUR",
                    url=extracted.get("url"),
                    category_ids="cables-cable-sets",
                    raw_json=extracted.get("raw_json"),
                    images=extracted.get("images", []),
                    attributes=extracted.get("attributes", []),
                    product_categories_paths=["/en/accessories/amplifier-accessories/cables-cable-sets/"],
                )
                await db.commit()
                product_ids.append(db_p.id)

        # -------------------------------------------------------------
        # 1. Output from GET /api/category/cables-cable-sets/products
        # -------------------------------------------------------------
        async with async_session_factory() as db:
            db_cat = await crud.get_category_by_id_or_slug(db, "cables-cable-sets")
            db_prods, total = await crud.get_products_paginated(
                db, category_id=db_cat.id, page=1, per_page=10
            )
            cat_resp = ProductsResponse(
                total=total,
                page=1,
                limit=10,
                products=[ProductListItem.model_validate(p) for p in db_prods],
            )
            print("=" * 80)
            print("1. RESPONSE FOR: GET /api/category/cables-cable-sets/products")
            print("=" * 80)
            print(json.dumps(cat_resp.model_dump(), indent=2, default=str))

        # -------------------------------------------------------------
        # 2. Output from GET /api/product/{product_id}
        # -------------------------------------------------------------
        async with async_session_factory() as db:
            for pid in product_ids:
                prod = await crud.get_product_by_id(db, pid)
                detail_resp = ProductDetail.model_validate(prod)
                print("\n" + "=" * 80)
                print(f"2. RESPONSE FOR: GET /api/product/{pid}  (SKU: {prod.sku})")
                print("=" * 80)
                print(json.dumps(detail_resp.model_dump(), indent=2, default=str))

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(demo())
