import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from scraper.product import ProductScraper

skus = [
    ("DSPB-KW", "https://www.soundimports.eu/en/dayton-audio-dspb-kw.html"),
    ("DSPB-EC", "https://www.soundimports.eu/en/dayton-audio-dspb-ec.html"),
    ("AA-AA11476", "https://www.soundimports.eu/en/sure-electronics-aa-aa11476.html"),
    ("KAB-LED", "https://www.soundimports.eu/en/dayton-audio-kab-led.html"),
    ("KDB-JM", "https://www.soundimports.eu/en/dayton-audio-kdb-jm.html"),
    ("AA-KA11111", "https://www.soundimports.eu/en/sure-electronics-aa-ka11111.html"),
    ("AA-KA11112", "https://www.soundimports.eu/en/sure-electronics-aa-ka11112.html"),
    ("AA-KA11113", "https://www.soundimports.eu/en/sure-electronics-aa-ka11113.html"),
    ("AA-AA11435", "https://www.soundimports.eu/en/sure-electronics-aa-aa11435.html"),
    ("AA-JA11116", "https://www.soundimports.eu/en/sure-electronics-aa-ja11116.html"),
    ("LBB-5CL", "https://www.soundimports.eu/en/dayton-audio-lbb-5cl.html"),
    ("AA-JA11118", "https://www.soundimports.eu/en/sure-electronics-aa-ja11118.html"),
    ("PS-BC12311", "https://www.soundimports.eu/en/sure-electronics-ps-bc12311.html"),
    ("AA-AB32500", "https://www.soundimports.eu/en/sure-electronics-aa-ab32500.html"),
    ("KAB-FC", "https://www.soundimports.eu/en/dayton-audio-kab-fc.html"),
    ("KABD-SPF", "https://www.soundimports.eu/en/dayton-audio-kabd-spf.html"),
]

async def check_all():
    scraper = ProductScraper.__new__(ProductScraper)
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
        for sku, url in skus:
            try:
                r_json = await client.get(url, params={"format": "json"})
                data = r_json.json().get("product", {})
                extracted = scraper.extract_product_data(data, category_slug="/en/accessories/amplifier-accessories/cables-cable-sets/")
                print(f"SKU: {sku:<12} | Stock: {str(extracted['stock']):<5} | Status: {extracted['stock_status']:<12} | Specs: {len(extracted['attributes'])}")
            except Exception as e:
                print(f"SKU: {sku:<12} | ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(check_all())
