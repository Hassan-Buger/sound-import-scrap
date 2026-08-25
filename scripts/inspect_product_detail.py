import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup
from scraper.client import HttpClient


async def inspect_products():
    client = HttpClient()
    test_urls = [
        "https://www.soundimports.eu/en/swan-speakers-d300.html",
        "https://www.soundimports.eu/en/dayton-audio-ep-5-24.html",
        "https://www.soundimports.eu/en/dayton-audio-um18-22.html",
        "https://www.soundimports.eu/en/tang-band-w3-1876s.html",
    ]

    for url in test_urls:
        print("\n" + "=" * 80)
        print("PRODUCT URL:", url)

        # 1. Fetch JSON
        try:
            json_data = await client.fetch_json(url, params={"format": "json"})
            prod = json_data.get("product", json_data)
            print("--- JSON KEYS ---")
            print(list(prod.keys()))
            print("JSON description:", repr(prod.get("description"))[:200])
            print("JSON shortDescription:", repr(prod.get("shortDescription"))[:200])
            print("JSON content (first 300):", repr(prod.get("content"))[:300])
            print("JSON stock:", prod.get("stock"))
            print("JSON stock_level:", prod.get("stock_level"))
            print("JSON stockStatus:", prod.get("stockStatus"))
            print("JSON inventory:", prod.get("inventory"))
            print("JSON availability:", prod.get("availability"))
            print("JSON quantity:", prod.get("quantity"))
            print("JSON variants:")
            variants = prod.get("variants")
            if isinstance(variants, dict):
                for vid, vdata in variants.items():
                    print(f"  Variant {vid}: stock={vdata.get('stock')}, stock_level={vdata.get('stock_level')}, title={vdata.get('title')}, matrix={vdata.get('matrix')}")
            elif isinstance(variants, list):
                for vdata in variants:
                    print(f"  Variant: stock={vdata.get('stock')}, stock_level={vdata.get('stock_level')}, title={vdata.get('title')}")
        except Exception as e:
            print("JSON error:", e)

        # 2. Fetch HTML
        try:
            html = await client.fetch_html(url)
            soup = BeautifulSoup(html, "lxml")

            print("\n--- JSON-LD STRUCTURED DATA ---")
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    ld = json.loads(script.get_text())
                    print("JSON-LD:", json.dumps(ld, indent=2)[:500])
                except Exception as e:
                    print("JSON-LD parse error:", e)

            print("\n--- HTML SELECTORS FOR SHORT DESCRIPTION ---")
            # Let's inspect product summary / description areas
            desc_candidates = soup.find_all(class_=lambda c: c and any(w in c.lower() for w in ["short", "summary", "intro", "excerpt", "tagline"]))
            for cand in desc_candidates[:5]:
                print(f"  Class '{cand.get('class')}': {repr(cand.get_text(strip=True))[:150]}")

            # Let's look at the main product content container
            content_div = soup.find("div", class_=lambda c: c and "product" in c.lower())
            print(f"  Product container classes: {content_div.get('class') if content_div else 'None'}")

            print("\n--- HTML SELECTORS FOR STOCK / AVAILABILITY ---")
            stock_candidates = soup.find_all(class_=lambda c: c and any(w in c.lower() for w in ["stock", "availab", "delivery", "inventory", "status"]))
            for cand in stock_candidates[:10]:
                print(f"  Class '{cand.get('class')}': {repr(cand.get_text(strip=True))}")

            # Look for buy button / add to cart button
            cart_btn = soup.find(lambda t: t.name in ["button", "input", "a"] and t.get("class") and any("cart" in c or "buy" in c or "order" in c for c in t.get("class")))
            if cart_btn:
                print(f"  Cart button: tag={cart_btn.name}, class={cart_btn.get('class')}, text={repr(cart_btn.get_text(strip=True))}, disabled={cart_btn.get('disabled')}")

        except Exception as e:
            print("HTML error:", e)

    await client.close()


if __name__ == "__main__":
    asyncio.run(inspect_products())
