import re
import html
from bs4 import BeautifulSoup
import httpx
import asyncio

def normalize_spec_name(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\s\-_/]+", "_", s)
    s = re.sub(r"[^\w]", "", s)
    return s

def extract_specifications(raw: dict, html_content: str = None) -> list:
    specs = []
    seen = set()

    def add_entry(key: str, val: str):
        if not key:
            return
        k = html.unescape(str(key)).strip()
        v = html.unescape(str(val)).strip() if val is not None else ""
        if not k:
            return
        norm_k = normalize_spec_name(k)
        norm_v = v.lower().strip()
        if norm_k in [normalize_spec_name(x['attribute_name']) for x in specs]:
            # Update value if old was empty
            for x in specs:
                if normalize_spec_name(x['attribute_name']) == norm_k and not x['attribute_value'] and v:
                    x['attribute_value'] = v
            return
        seen.add((norm_k, norm_v))
        specs.append({
            "attribute_name": k,
            "attribute_value": v,
            "sort_order": len(specs),
            "normalized_name": norm_k,
        })

    # 1. Check raw attributes / specs dicts (from SoundImports JSON)
    raw_specs = raw.get("specs")
    if isinstance(raw_specs, dict):
        for spec_id, spec_data in raw_specs.items():
            if isinstance(spec_data, dict):
                k = spec_data.get("title") or spec_data.get("name") or spec_data.get("label")
                v = spec_data.get("value") or spec_data.get("text")
                add_entry(k, v)

    raw_attrs = raw.get("attributes") or raw.get("specifications") or raw.get("properties") or {}
    if isinstance(raw_attrs, dict):
        for k, v in raw_attrs.items():
            add_entry(k, v)
    elif isinstance(raw_attrs, list):
        for item in raw_attrs:
            if isinstance(item, dict):
                k = item.get("name") or item.get("label") or item.get("key") or item.get("attribute_name")
                v = item.get("value") or item.get("text") or item.get("attribute_value")
                add_entry(k, v)

    # 2. Check HTML content or page HTML for #specs / dl / table / spec paragraphs
    html_sources = []
    if html_content:
        html_sources.append(html_content)
    content_html = raw.get("content")
    if content_html and content_html != html_content:
        html_sources.append(content_html)

    for h_src in html_sources:
        soup = BeautifulSoup(h_src, "html.parser")
        
        # 2a. Look for #specs or section.specs DL definitions
        specs_sec = soup.find(id="specs") or soup.find("section", class_=re.compile(r"specs|specifications", re.I))
        if specs_sec:
            for dl in specs_sec.find_all("dl"):
                for div in dl.find_all(["div", "dt"]):
                    dt = div if div.name == "dt" else div.find("dt")
                    dd = div.find("dd") if div.name != "dd" else div
                    if dt and dd:
                        # dt might contain nested dd text if not decomposed
                        dt_clone = BeautifulSoup(str(dt), "html.parser")
                        for nested_dd in dt_clone.find_all("dd"):
                            nested_dd.decompose()
                        k_text = dt_clone.get_text(" ", strip=True)
                        v_text = dd.get_text(" ", strip=True)
                        add_entry(k_text, v_text)

        # 2b. Look for tables inside soup
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                if len(cells) == 2:
                    add_entry(cells[0].get_text(strip=True), cells[1].get_text(strip=True))

        # 2c. Look for <p><strong>Specifications</strong>: ... or paragraphs with key: value separated by bullets
        for tag in soup.find_all(["p", "div", "li"]):
            text = tag.get_text(" ", strip=True)
            if "specification" in text.lower() or "•" in text or "▪" in text:
                clean_text = re.sub(r"^specifications?\s*:?\s*", "", text, flags=re.IGNORECASE).strip()
                # Split by bullet points or pipes
                parts = re.split(r"[•▪|\n]", clean_text)
                for part in parts:
                    part = part.strip()
                    if ":" in part:
                        k, v = part.split(":", 1)
                        if 1 < len(k.strip()) < 40 and len(v.strip()) > 0:
                            add_entry(k.strip(), v.strip())

    # 3. Always ensure foundational identity specs (Article number, EAN) are present
    sku = raw.get("sku") or raw.get("number") or raw.get("articleNumber")
    if sku:
        add_entry("Article number", sku)

    ean = raw.get("ean") or raw.get("gtin") or raw.get("upc")
    if ean:
        add_entry("EAN", ean)

    # Re-index sort order
    for i, item in enumerate(specs):
        item["sort_order"] = i

    return specs


async def test():
    urls = [
        "https://www.soundimports.eu/en/monacor-mzf-8624.html",
        "https://www.soundimports.eu/en/monacor-mzf-8625.html",
        "https://www.soundimports.eu/en/velleman-mmp006.html",
        "https://www.soundimports.eu/en/dayton-audio-classic-b65-oak.html",
    ]
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
        for url in urls:
            resp_j = await client.get(f"{url}?format=json")
            resp_h = await client.get(url)
            raw = resp_j.json().get("product", resp_j.json())
            specs = extract_specifications(raw, resp_h.text)
            print("=" * 80)
            print("URL:", url)
            print("SKU:", raw.get("sku"))
            print(f"Total Specs Extracted: {len(specs)}")
            for s in specs[:10]:
                print(f"  {s['attribute_name']}: {s['attribute_value']}")

if __name__ == "__main__":
    asyncio.run(test())
