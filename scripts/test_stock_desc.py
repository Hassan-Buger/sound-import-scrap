import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup
from scraper.client import HttpClient
from scraper.soundimports import SoundImportsScraper


async def test_stock_and_descriptions():
    client = HttpClient()
    supplier = SoundImportsScraper(client)

    # Let's get products from a few category listings
    urls = [
        "https://www.soundimports.eu/en/home-audio/speakers/bookshelf-speakers/",
        "https://www.soundimports.eu/en/audio-components/woofers/",
        "https://www.soundimports.eu/en/accessories/binding-posts/binding-posts/",
    ]

    all_products = []
    for u in urls:
        data = await supplier.get_product_list(u, page=1, limit=10)
        prods = supplier.category_scraper.extract_products(data)
        for p in prods[:5]:
            summary = supplier.extract_product_summary(p)
            if summary.get("url"):
                all_products.append(summary["url"])

    print(f"Testing {len(all_products)} product URLs...")
    for p_url in all_products[:8]:
        print("\n" + "-" * 70)
        print("URL:", p_url)
        json_data = await supplier.get_product_detail(p_url)
        raw_stock = json_data.get("stock")
        print("Raw stock in JSON:", raw_stock)
        print("Raw description in JSON:", repr(json_data.get("description")))
        print("Raw shortDescription in JSON:", repr(json_data.get("shortDescription")))
        print("Raw content in JSON (first 150):", repr(json_data.get("content"))[:150])

        # Also fetch HTML to see how it looks
        html = await client.fetch_html(p_url)
        soup = BeautifulSoup(html, "lxml")
        stock_tags = soup.find_all(class_=lambda c: c and "stock" in c.lower())
        for st in stock_tags[:3]:
            print(f"HTML stock tag ({st.get('class')}): {repr(st.get_text(strip=True))}")

        # Check short description in HTML
        # Look for the product description box or intro
        intro_elem = soup.select_one(".product-intro, .short-description, .product-description-short, .gui-description-short, [itemprop='description']")
        if intro_elem:
            print(f"HTML intro element ({intro_elem.get('class')}): {repr(intro_elem.get_text(strip=True))[:150]}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(test_stock_and_descriptions())
