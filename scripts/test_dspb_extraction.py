import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from scraper.product import ProductScraper

async def test_dspb_ec():
    url = "https://www.soundimports.eu/en/dayton-audio-dspb-ec.html"
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
        r_json = await client.get(url, params={"format": "json"})
        data = r_json.json().get("product", {})
        
        scraper = ProductScraper.__new__(ProductScraper)
        extracted = scraper.extract_product_data(data)
        
        print("Extracted SKU:", extracted["sku"])
        print("Extracted Stock:", extracted["stock"])
        print("Extracted Stock Status:", extracted["stock_status"])
        print("Extracted Price:", extracted["price"])
        print("Extracted Specifications count:", len(extracted["attributes"]))
        for s in extracted["attributes"]:
            print(f"  {s['attribute_name']}: {s['attribute_value']}")

if __name__ == "__main__":
    asyncio.run(test_dspb_ec())
