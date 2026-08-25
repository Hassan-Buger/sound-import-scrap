import asyncio
import json
from bs4 import BeautifulSoup
import httpx

TEST_URLS = [
    "https://www.soundimports.eu/en/monacor-mzf-8624.html",
    "https://www.soundimports.eu/en/monacor-mzf-8625.html",
    "https://www.soundimports.eu/en/velleman-mmp006.html",
    "https://www.soundimports.eu/en/hivi-swans-d300.html",
    "https://www.soundimports.eu/en/dayton-audio-classic-b65-oak.html",
]

async def inspect_content():
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
        for url in TEST_URLS:
            print("=" * 80)
            print(f"URL: {url}")
            resp = await client.get(f"{url}?format=json")
            if resp.status_code == 200:
                data = resp.json()
                prod = data.get("product", data)
                content = prod.get("content") or ""
                print(f"--- RAW CONTENT HTML (length: {len(content)}) ---")
                print(content)
                print("\n--- CONTENT SOUP STRUCTURE ---")
                soup = BeautifulSoup(content, "html.parser")
                for elem in soup.find_all(True):
                    print(f"Tag: <{elem.name}>: {repr(elem.get_text(strip=True))[:80]}")
            else:
                print(f"Status: {resp.status_code}")

if __name__ == "__main__":
    asyncio.run(inspect_content())
