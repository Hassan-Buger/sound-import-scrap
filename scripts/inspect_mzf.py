import asyncio
import json
from bs4 import BeautifulSoup
import httpx

TEST_URLS = [
    "https://www.soundimports.eu/en/monacor-mzf-8624.html",
    "https://www.soundimports.eu/en/monacor-mzf-8625.html",
]

async def inspect_mzf():
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
        for url in TEST_URLS:
            print("=" * 80)
            print(f"URL: {url}")
            # JSON
            resp = await client.get(f"{url}?format=json")
            if resp.status_code == 200:
                data = resp.json()
                prod = data.get("product", data)
                content = prod.get("content") or ""
                print(f"JSON content:\n{content}\n")
                print(f"JSON sku: {prod.get('sku')}, ean: {prod.get('ean')}")

            # HTML
            resp_h = await client.get(url)
            if resp_h.status_code == 200:
                soup = BeautifulSoup(resp_h.text, "html.parser")
                # Look for everything with text "Specifications"
                for elem in soup.find_all(string=lambda t: t and "specification" in t.lower()):
                    p = elem.parent
                    print(f"HTML parent <{p.name}>: {p}")
                    print(f"HTML grand-parent <{p.parent.name} class='{p.parent.get('class')}'>:\n{p.parent.prettify()[:1000]}")

if __name__ == "__main__":
    asyncio.run(inspect_mzf())
