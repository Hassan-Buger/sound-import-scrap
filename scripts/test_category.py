"""Quick test script: fetch and display products from a category.

Usage:
    python scripts/test_category.py
    python scripts/test_category.py --url "https://www.soundimports.eu/en/home-audio/speakers/bookshelf-speakers/"
    python scripts/test_category.py --page 2 --limit 5
"""
import asyncio
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scraper.soundimports import SoundImportsScraper


async def main():
    parser = argparse.ArgumentParser(description="Test category scraper")
    parser.add_argument(
        "--url",
        default="https://www.soundimports.eu/en/home-audio/speakers/bookshelf-speakers/",
        help="Category URL to test",
    )
    parser.add_argument("--page", type=int, default=1, help="Page number")
    parser.add_argument("--limit", type=int, default=10, help="Products per page")
    args = parser.parse_args()

    supplier = SoundImportsScraper()
    try:
        print(f"Fetching: {args.url}")
        print(f"Page: {args.page}, Limit: {args.limit}")
        print("-" * 60)

        data = await supplier.get_product_list(args.url, page=args.page, limit=args.limit)
        products = supplier.category_scraper.extract_products(data)

        print(f"Total products on page: {len(products)}")
        print(f"Total in category: {data.get('total', data.get('count', 'unknown'))}")
        print()

        for i, p in enumerate(products[: args.limit], 1):
            summary = supplier.extract_product_summary(p)
            print(f"{i}. SKU: {summary.get('sku', 'N/A')}")
            print(f"   Name: {summary.get('name', 'N/A')}")
            print(f"   Price: {summary.get('price', 'N/A')}")
            print(f"   URL: {summary.get('url', 'N/A')}")
            print()

    finally:
        await supplier._client.close()


if __name__ == "__main__":
    asyncio.run(main())
