"""Quick test script: fetch and display a single product detail.

Usage:
    python scripts/test_product.py
    python scripts/test_product.py --url "https://www.soundimports.eu/en/hivi-os-10.html"
"""
import asyncio
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scraper.soundimports import SoundImportsScraper


async def main():
    parser = argparse.ArgumentParser(description="Test product scraper")
    parser.add_argument(
        "--url",
        default="https://www.soundimports.eu/en/hivi-os-10.html",
        help="Product URL to test",
    )
    parser.add_argument("--raw", action="store_true", help="Show raw JSON")
    args = parser.parse_args()

    supplier = SoundImportsScraper()
    try:
        print(f"Fetching: {args.url}")
        print("-" * 60)

        data = await supplier.get_product_detail(args.url)
        parsed = supplier.extract_product_detail(data)

        if args.raw:
            print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
            return

        print(f"SKU:              {parsed.get('sku', 'N/A')}")
        print(f"Product ID:        {parsed.get('product_id', 'N/A')}")
        print(f"Title:             {parsed.get('title', 'N/A')}")
        print(f"Brand:             {parsed.get('brand', 'N/A')}")
        print(f"Price:             {parsed.get('price', 'N/A')} {parsed.get('currency', 'EUR')}")
        print(f"Stock:             {parsed.get('stock', 'N/A')}")
        print(f"Stock Status:      {parsed.get('stock_status', 'N/A')}")
        print(f"EAN:               {parsed.get('ean', 'N/A')}")
        print(f"URL:               {parsed.get('url', 'N/A')}")
        print(f"Category IDs:      {parsed.get('category_ids', 'N/A')}")
        print()
        print(f"Description:       {(parsed.get('description') or 'N/A')[:200]}")
        print()
        print(f"Long Description:  {(parsed.get('long_description') or 'N/A')[:200]}")
        print()
        print(f"Images ({len(parsed.get('images', []))}):")
        for img in parsed.get("images", []):
            cover_tag = " [COVER]" if img.get("is_cover") else ""
            print(f"  {img['image_url']}{cover_tag}")
        print()
        print(f"Attributes ({len(parsed.get('attributes', []))}):")
        for attr in parsed.get("attributes", []):
            print(f"  {attr['attribute_name']}: {attr.get('attribute_value', '')}")

    finally:
        await supplier._client.close()


if __name__ == "__main__":
    asyncio.run(main())
