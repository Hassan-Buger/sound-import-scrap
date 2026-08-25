import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import async_session_factory, Base, engine
from app import crud
from app.models import Product, Category
from app.schemas import ProductListItem, ProductDetail
from scraper.client import HttpClient
from scraper.soundimports import SoundImportsScraper


async def run_verification():
    print("=" * 80)
    print("RUNNING PRODUCTION E2E VERIFICATION SUITE")
    print("=" * 80)

    # 1. Setup DB tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    client = HttpClient()
    supplier = SoundImportsScraper(client)

    try:
        # Step 1: Scrape real products from SoundImports
        test_category_url = "https://www.soundimports.eu/en/home-audio/speakers/bookshelf-speakers/"
        print(f"\n1. Fetching real category listing: {test_category_url}")
        cat_data = await supplier.get_product_list(test_category_url, page=1, limit=5)
        raw_products = supplier.category_scraper.extract_products(cat_data)
        print(f"   Found {len(raw_products)} products on page 1")
        assert len(raw_products) > 0, "No products found on category page"

        # Step 2: Fetch and parse product detail
        first_raw = raw_products[0]
        first_summary = supplier.extract_product_summary(first_raw)
        detail_url = first_summary["url"]
        print(f"\n2. Fetching real product detail: {detail_url}")
        detail_json = await supplier.get_product_detail(detail_url)
        extracted = supplier.extract_product_detail(
            detail_json,
            category_slug="/en/home-audio/speakers/bookshelf-speakers/",
        )

        print("   Extracted fields:")
        print(f"     SKU: {extracted['sku']}")
        print(f"     Title: {extracted['title']}")
        print(f"     Short description: {repr(extracted['short_description'])}")
        print(f"     Long description length: {len(extracted['long_description']) if extracted['long_description'] else 0}")
        print(f"     Stock quantity: {extracted['stock']} (type: {type(extracted['stock'])})")
        print(f"     Stock status: {extracted['stock_status']}")
        print(f"     Specifications count: {len(extracted['attributes'])}")

        assert extracted["sku"], "SKU missing"
        assert extracted["short_description"], "Short description missing"
        assert extracted["stock_status"] in ("in_stock", "out_of_stock", "on_backorder"), f"Invalid stock status: {extracted['stock_status']}"
        assert extracted["stock"] is not None, "Stock count is None"

        # Step 3: Insert into database
        print("\n3. Persisting categories and product to DB...")
        async with async_session_factory() as db:
            cat_obj = await crud.upsert_category_with_parent(
                db,
                name="Bookshelf speakers",
                slug="bookshelf-speakers",
                canonical_path="/en/home-audio/speakers/bookshelf-speakers/",
                url="en/home-audio/speakers/bookshelf-speakers/",
                level=3,
            )
            other_cat = await crud.upsert_category_with_parent(
                db,
                name="Outdoor speakers",
                slug="outdoor-speakers",
                canonical_path="/en/home-audio/speakers/outdoor-speakers/",
                url="en/home-audio/speakers/outdoor-speakers/",
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
                category_ids="bookshelf-speakers",
                raw_json=extracted.get("raw_json"),
                images=extracted.get("images", []),
                attributes=extracted.get("attributes", []),
                product_categories_paths=["/en/home-audio/speakers/bookshelf-speakers/"],
            )
            await db.commit()
            print(f"   Saved product ID={db_prod.id}, SKU={db_prod.sku}")

        # Step 4: Test Category endpoint query & serialization
        print("\n4. Testing Category Endpoint Query (Isolation & Fields)...")
        async with async_session_factory() as db:
            # Query bookshelf-speakers
            prods, total = await crud.get_products_paginated(
                db,
                category_id=cat_obj.id,
                page=1,
                per_page=10,
            )
            assert total == 1, f"Expected 1 product, got {total}"
            item = ProductListItem.model_validate(prods[0])
            print("   Category endpoint returned:")
            print(f"     Title: {item.title}")
            print(f"     Category: {item.category}")
            print(f"     Short description: {repr(item.short_description)}")
            print(f"     Long description: {repr(item.long_description)[:80]}...")
            print(f"     Stock: {item.stock}")
            print(f"     Stock status: {item.stock_status}")
            print(f"     Specifications count: {len(item.specifications)}")
            assert item.long_description is not None, "long_description is None in category endpoint"
            assert len(item.specifications) > 0, "specifications are empty in category endpoint"
            assert item.stock is not None, "stock is None in category endpoint"
            assert item.stock_status is not None, "stock_status is None in category endpoint"

            # Query outdoor-speakers (should return 0 - isolation check)
            outdoor_prods, outdoor_tot = await crud.get_products_paginated(
                db,
                category_id=other_cat.id,
                page=1,
                per_page=10,
            )
            assert outdoor_tot == 0, f"Expected 0 outdoor products, got {outdoor_tot}"
            print("   Isolation check PASSED: 0 products returned for unrelated category")

        # Step 5: Test SKU endpoint query & consistency
        print("\n5. Testing SKU Endpoint Query (Consistency)...")
        async with async_session_factory() as db:
            sku_prod = await crud.get_product_by_sku(db, extracted["sku"])
            assert sku_prod is not None
            detail = ProductDetail.model_validate(sku_prod)
            assert detail.long_description == item.long_description, "long_description mismatch between SKU and Category endpoint"
            assert detail.stock == item.stock, "stock mismatch between SKU and Category endpoint"
            assert detail.stock_status == item.stock_status, "stock_status mismatch between SKU and Category endpoint"
            assert len(detail.specifications) == len(item.specifications), "specifications count mismatch between SKU and Category endpoint"
            print("   SKU endpoint consistency check PASSED")

        print("\n" + "=" * 80)
        print("ALL E2E VERIFICATIONS PASSED SUCCESSFULLY!")
        print("=" * 80)

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run_verification())
