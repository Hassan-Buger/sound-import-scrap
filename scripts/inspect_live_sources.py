import asyncio
import json
import re
from bs4 import BeautifulSoup
import httpx

TEST_URLS = [
    "https://www.soundimports.eu/en/monacor-mzf-8624.html",
    "https://www.soundimports.eu/en/monacor-mzf-8625.html",
    "https://www.soundimports.eu/en/velleman-mmp006.html",
    "https://www.soundimports.eu/en/monacor-mzf-8604.html",
]

async def inspect():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
        for url in TEST_URLS:
            print("=" * 80)
            print(f"INSPECTING: {url}")
            print("=" * 80)

            # 1. Fetch JSON endpoint (?format=json)
            json_url = f"{url}?format=json"
            try:
                resp_json = await client.get(json_url)
                print(f"JSON Status: {resp_json.status_code}")
                if resp_json.status_code == 200:
                    data = resp_json.json()
                    prod_data = data.get("product", data)
                    print(f"  SKU: {prod_data.get('sku') or prod_data.get('number') or prod_data.get('articleNumber')}")
                    print(f"  Stock data in JSON: {prod_data.get('stock')}")
                    print(f"  Inventory in JSON: {prod_data.get('inventory')}")
                    print(f"  Availability in JSON: {prod_data.get('availability')}")
                    print(f"  Attributes in JSON: {prod_data.get('attributes')}")
                    print(f"  Specs in JSON: {prod_data.get('specs') or prod_data.get('specifications')}")
                    print(f"  Content length in JSON: {len(prod_data.get('content', '')) if prod_data.get('content') else 0}")
                    print(f"  Description in JSON: {repr(prod_data.get('description'))}")
            except Exception as e:
                print(f"JSON fetch error: {e}")

            # 2. Fetch HTML page
            try:
                resp_html = await client.get(url)
                print(f"HTML Status: {resp_html.status_code}")
                if resp_html.status_code == 200:
                    html = resp_html.text
                    soup = BeautifulSoup(html, "html.parser")

                    # Look for Stock in HTML
                    print("\n  --- Stock in HTML ---")
                    stock_snippets = []
                    for s in soup.find_all(string=re.compile(r"stock|in stock|out of stock|available|leverbaar", re.IGNORECASE)):
                        parent = s.parent
                        if parent and parent.name not in ["script", "style"]:
                            text = parent.get_text(strip=True)
                            if len(text) < 100:
                                stock_snippets.append(f"<{parent.name} class='{parent.get('class')}'>{text}</{parent.name}>")
                    print("  Stock text occurrences in HTML:")
                    for sn in set(stock_snippets[:10]):
                        print("   ", sn)

                    # Look for Specifications in HTML
                    print("\n  --- Specifications in HTML ---")
                    spec_headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "strong"], string=re.compile(r"specifications|specificaties", re.IGNORECASE))
                    for sh in spec_headings:
                        print(f"  Found spec heading: <{sh.name}>{sh.get_text(strip=True)}</{sh.name}>")
                        # Inspect siblings/parent
                        container = sh.find_parent(["div", "section", "article"]) or sh.parent
                        print(f"  Container: <{container.name} class='{container.get('class')}'>")
                        # Look for tables or dls or lists or key-value rows
                        tables = container.find_all("table")
                        if tables:
                            print(f"    Found {len(tables)} tables in spec container")
                            for tbl in tables:
                                for row in tbl.find_all("tr")[:5]:
                                    print("     Table row:", [td.get_text(strip=True) for td in row.find_all(["td", "th"])])
                        dls = container.find_all("dl")
                        if dls:
                            print(f"    Found {len(dls)} dl in spec container")
                        # Also check next siblings of heading
                        for sib in sh.find_next_siblings()[:5]:
                            print(f"    Sibling <{sib.name} class='{sib.get('class')}'>: {sib.get_text(strip=True)[:100]}")

                    # Search for any tables with class or in product description
                    all_tables = soup.find_all("table")
                    print(f"\n  Total tables on page: {len(all_tables)}")
                    for i, t in enumerate(all_tables):
                        classes = t.get("class")
                        rows = t.find_all("tr")
                        print(f"    Table {i} class={classes}, rows={len(rows)}")
                        for r in rows[:4]:
                            cells = [c.get_text(strip=True) for c in r.find_all(["th", "td"])]
                            print(f"      {cells}")

                    # Search scripts for embedded product/inventory data
                    print("\n  --- Scripts / JSON-LD / Data Attributes ---")
                    for sc in soup.find_all("script", type="application/ld+json"):
                        try:
                            ld_json = json.loads(sc.string or "{}")
                            print("  LD+JSON:", ld_json.get("@type"), ld_json.get("offers"))
                        except Exception:
                            pass

                    for sc in soup.find_all("script"):
                        content = sc.string or ""
                        if "window.product" in content or "theme.product" in content or "stock" in content.lower():
                            for line in content.splitlines():
                                if any(w in line.lower() for w in ["stock", "quantity", "inventory", "article", "ean"]):
                                    if len(line.strip()) < 150:
                                        print("    Script line:", line.strip())

            except Exception as e:
                print(f"HTML fetch error: {e}")

if __name__ == "__main__":
    asyncio.run(inspect())
