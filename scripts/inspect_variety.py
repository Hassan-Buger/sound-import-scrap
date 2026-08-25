import asyncio
import json
import re
from bs4 import BeautifulSoup
import httpx

TEST_URLS = [
    "https://www.soundimports.eu/en/monacor-mzf-8624.html",
    "https://www.soundimports.eu/en/monacor-mzf-8625.html",
    "https://www.soundimports.eu/en/dayton-audio-classic-b65-oak.html",
    "https://www.soundimports.eu/en/hivi-swans-d300.html",
    "https://www.soundimports.eu/en/peerless-by-tymphany-tc9fd18-08.html",
    "https://www.soundimports.eu/en/dayton-audio-dta-120bt2.html",
    "https://www.soundimports.eu/en/jantzen-audio-001-0234.html",
]

async def inspect_variety():
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
        for url in TEST_URLS:
            print("=" * 80)
            print(f"PRODUCT: {url}")
            
            # Fetch HTML
            resp_h = await client.get(url)
            if resp_h.status_code == 200:
                soup = BeautifulSoup(resp_h.text, "html.parser")
                
                # Check Stock in HTML
                stock_hurry = soup.find_all(class_=re.compile(r"hurry|stock", re.IGNORECASE))
                for sh in stock_hurry:
                    print(f"  HTML Stock element <{sh.name} class='{sh.get('class')}'>: {sh.get_text(' ', strip=True)}")
                
                # Check #specs section in HTML
                specs_sec = soup.find(id="specs") or soup.find(class_=re.compile(r"specs|specifications", re.IGNORECASE))
                if specs_sec:
                    print(f"  Found specs container: <{specs_sec.name} id='{specs_sec.get('id')}' class='{specs_sec.get('class')}'>")
                    # Check dl
                    for dl in specs_sec.find_all("dl"):
                        dts = [dt.get_text(" ", strip=True) for dt in dl.find_all("dt")]
                        dds = [dd.get_text(" ", strip=True) for dd in dl.find_all("dd")]
                        print(f"    DL entries ({len(dts)}): {list(zip(dts, dds))[:6]}")
                    # Check table
                    for tbl in specs_sec.find_all("table"):
                        rows = tbl.find_all("tr")
                        print(f"    Table entries ({len(rows)})")
                else:
                    print("  No #specs container found in HTML")
            
            # Fetch JSON
            resp_j = await client.get(f"{url}?format=json")
            if resp_j.status_code == 200:
                data = resp_j.json()
                prod = data.get("product", data)
                stock_obj = prod.get("stock")
                attrs_obj = prod.get("attributes")
                content_html = prod.get("content") or ""
                print(f"  JSON Stock: {stock_obj}")
                print(f"  JSON Attributes count: {len(attrs_obj) if isinstance(attrs_obj, dict) else 0}")
                
                # Check if content has "Specifications" paragraph or list
                soup_c = BeautifulSoup(content_html, "html.parser")
                for p in soup_c.find_all(["p", "div", "ul"]):
                    txt = p.get_text(" ", strip=True)
                    if "specification" in txt.lower() or "specifications:" in txt.lower():
                        print(f"  JSON Content spec snippet: {txt[:150]}...")

if __name__ == "__main__":
    asyncio.run(inspect_variety())
