import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import async_session_factory, Base, engine
from app import crud
from app.models import Product, Category
from app.schemas import ProductListItem, ProductDetail
from scraper.client import HttpClient
from scraper.soundimports import SoundImportsScraper


async def run_final_verification():
    print("=" * 80)
    print("RUNNING FINAL PRODUCTION VERIFICATION SUITE")
    print("=" * 80)

    # 1. Initialize SQLite/PostgreSQL tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    client = HttpClient()
    supplier = SoundImportsScraper(client)

    test_cases = [
        {
            "sku": "MZF-8624",
            "url": "https://www.soundimports.eu/en/monacor-mzf-8624.html",
            "category_path": "/en/accessories/cabinet-hardware/speaker-grills/",
            "category_name": "Speaker grills",
            "expected_status": "in_stock",
            "min_specs": 4,
        },
        {
            "sku": "MZF-8625",
            "url": "https://www.soundimports.eu/en/monacor-mzf-8625.html",
            "category_path": "/en/accessories/cabinet-hardware/speaker-grills/",
            "category_name": "Speaker grills",
            "expected_status": "on_backorder",
            "min_specs": 4,
        },
        {
            "sku": "MMP006",
            "url": "https://www.soundimports.eu/en/velleman-mmp006.html",
            "category_path": "/en/accessories/cables/cable-management/",
            "category_name": "Cable management",
            "expected_status": "in_stock",
            "min_specs": 2,
        },
    ]

    try:
        for tc in test_cases:
            print("\n" + "-" * 80)
            print(f"TESTING SKU: {tc['sku']} ({tc['url']})")
            print("-" * 80)

            # Step 1: Fetch live JSON & HTML
            detail_data = await supplier.get_product_detail(tc["url"])
            try:
                html_doc = await client.fetch_html(tc["url"])
            except Exception:
                html_doc = None

            # Step 2: Parse Product
            extracted = supplier.extract_product_detail(
                detail_data,
                category_slug=tc["category_path"],
                html_doc=html_doc,
            )

            print("1. PARSER OUTPUT:")
            print(f"   SKU: {extracted['sku']}")
            print(f"   Title: {extracted['title']}")
            print(f"   Stock: {extracted['stock']} (type: {type(extracted['stock'])})")
            print(f"   Stock Status: {extracted['stock_status']}")
            print(f"   Specifications count: {len(extracted['attributes'])}")
            for idx, spec in enumerate(extracted["attributes"][:6]):
                print(f"     [{idx}] {spec['attribute_name']}: {spec['attribute_value']}")

            assert extracted["sku"] == tc["sku"], f"SKU mismatch: {extracted['sku']} != {tc['sku']}"
            assert extracted["stock"] is not None, f"Stock is None for {tc['sku']}"
            assert extracted["stock_status"] in ("in_stock", "out_of_stock", "on_backorder")
            assert len(extracted["attributes"]) >= tc["min_specs"], f"Insufficient specs for {tc['sku']}"

            # Step 3: Persist to DB
            async with async_session_factory() as db:
                cat_obj = await crud.upsert_category_with_parent(
                    db,
                    name=tc["category_name"],
                    slug=tc["category_path"].strip("/").split("/")[-1],
                    canonical_path=tc["category_path"],
                    url=tc["category_path"].lstrip("/"),
                    level=3,
                )
                await db.commit()

                db_prod, is_new, _ = await crud.upsert_product(
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
                    currency=extracted.get("currency", "EUR"),
                    url=extracted.get("url"),
                    category_ids=cat_obj.slug,
                    raw_json=extracted.get("raw_json"),
                    images=extracted.get("images", []),
                    attributes=extracted.get("attributes", []),
                    product_categories_paths=[tc["category_path"]],
                )
                await db.commit()
                print(f"2. DB PERSISTENCE: Saved Product ID={db_prod.id}, SKU={db_prod.sku}")

            # Step 4: Verify SKU API Endpoint Output
            async with async_session_factory() as db:
                sku_product = await crud.get_product_by_sku(db, tc["sku"])
                assert sku_product is not None
                sku_detail = ProductDetail.model_validate(sku_product)

                print("3. SKU ENDPOINT SERIALIZATION:")
                print(f"   sku: {sku_detail.sku}")
                print(f"   stock: {sku_detail.stock}")
                print(f"   stock_status: {sku_detail.stock_status}")
                print(f"   specifications: {len(sku_detail.specifications)} items")

                assert sku_detail.stock == extracted["stock"]
                assert sku_detail.stock_status == extracted["stock_status"]
                assert len(sku_detail.specifications) == len(extracted["attributes"])

            # Step 5: Verify Category API Endpoint Output
            async with async_session_factory() as db:
                cat_products, total = await crud.get_products_paginated(
                    db,
                    category_id=cat_obj.id,
                    page=1,
                    per_page=10,
                )
                matching = [p for p in cat_products if p.sku == tc["sku"]]
                assert len(matching) == 1, f"Product {tc['sku']} not found in category query"
                cat_item = ProductListItem.model_validate(matching[0])

                print("4. CATEGORY ENDPOINT SERIALIZATION:")
                print(f"   sku: {cat_item.sku}")
                print(f"   stock: {cat_item.stock}")
                print(f"   stock_status: {cat_item.stock_status}")
                print(f"   specifications: {len(cat_item.specifications)} items")

                # Step 6: Verify API Consistency between Endpoints
                assert cat_item.stock == sku_detail.stock, "Stock mismatch between SKU and Category endpoint"
                assert cat_item.stock_status == sku_detail.stock_status, "Stock status mismatch"
                assert len(cat_item.specifications) == len(sku_detail.specifications), "Spec count mismatch"
                assert cat_item.long_description == sku_detail.long_description, "Long desc mismatch"
                print("5. ENDPOINT CONSISTENCY: PASSED (SKU and Category endpoints agree)")

        print("\n" + "=" * 80)
        print("ALL FINAL PRODUCTION VERIFICATIONS PASSED SUCCESSFULLY!")
        print("=" * 80)

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run_final_verification())
