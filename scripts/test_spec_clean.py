import re
import html
from bs4 import BeautifulSoup
import httpx
import asyncio

def _clean_text(val: str) -> str:
    if not val:
        return ""
    val = html.unescape(str(val))
    val = val.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", val).strip()

def _normalize_spec_name(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\s\-_/]+", "_", s)
    s = re.sub(r"[^\w]", "", s)
    return s

def extract_specifications_clean(raw: dict, html_doc: str = None) -> list:
    specs = []
    seen_keys = {}  # norm_key -> index in specs

    def add_spec(key: str, val: str):
        k = _clean_text(key)
        v = _clean_text(val)
        if not k or len(k) > 60:
            return
        # Ignore noisy UI strings
        if any(ign in k.lower() for ign in ["reviews", "review this product", "share", "show more", "show less"]):
            return
        
        norm_k = _normalize_spec_name(k)
        if not norm_k:
            return

        if norm_k in seen_keys:
            idx = seen_keys[norm_k]
            # If previous value was empty and new has value, update
            if not specs[idx]["attribute_value"] and v:
                specs[idx]["attribute_value"] = v
            return

        seen_keys[norm_k] = len(specs)
        specs.append({
            "attribute_name": k,
            "attribute_value": v,
            "sort_order": len(specs),
            "normalized_name": norm_k,
        })

    # 1. First, check structured JSON attributes / specs
    raw_specs = raw.get("specs")
    if isinstance(raw_specs, dict):
        for spec_id, spec_data in raw_specs.items():
            if isinstance(spec_data, dict):
                k = spec_data.get("title") or spec_data.get("name") or spec_data.get("label")
                v = spec_data.get("value") or spec_data.get("text")
                add_spec(k, v)

    raw_attrs = raw.get("attributes") or raw.get("specifications") or raw.get("properties") or {}
    if isinstance(raw_attrs, dict):
        for k, v in raw_attrs.items():
            add_spec(k, v)
    elif isinstance(raw_attrs, list):
        for item in raw_attrs:
            if isinstance(item, dict):
                k = item.get("name") or item.get("label") or item.get("key") or item.get("attribute_name")
                v = item.get("value") or item.get("text") or item.get("attribute_value")
                add_spec(k, v)

    # 2. Check HTML document or #specs DL / tables
    if html_doc:
        soup_h = BeautifulSoup(html_doc, "html.parser")
        specs_sec = soup_h.find(id="specs") or soup_h.find("section", class_=re.compile(r"specs|specifications", re.I))
        if specs_sec:
            for dl in specs_sec.find_all("dl"):
                for div in dl.find_all(["div", "dt"]):
                    dt = div if div.name == "dt" else div.find("dt")
                    dd = div.find("dd") if div.name != "dd" else div
                    if dt and dd:
                        dt_clone = BeautifulSoup(str(dt), "html.parser")
                        for nested_dd in dt_clone.find_all("dd"):
                            nested_dd.decompose()
                        k_text = dt_clone.get_text(" ", strip=True)
                        v_text = dd.get_text(" ", strip=True)
                        add_spec(k_text, v_text)

            for table in specs_sec.find_all("table"):
                for row in table.find_all("tr"):
                    cells = row.find_all(["th", "td"])
                    if len(cells) == 2:
                        add_spec(cells[0].get_text(strip=True), cells[1].get_text(strip=True))

    # 3. Check content HTML for dedicated Specifications paragraph / block
    content_html = raw.get("content") or ""
    if content_html:
        soup_c = BeautifulSoup(content_html, "html.parser")
        for tag in soup_c.find_all(["p", "div"]):
            # Check if tag has strong Specifications or starts with Specifications:
            strong = tag.find("strong")
            if strong and "specification" in strong.get_text().lower():
                # Extract text after Specifications
                tag_text = tag.get_text(" ", strip=True)
                clean_text = re.sub(r"^.*?specifications?\s*:?\s*", "", tag_text, flags=re.IGNORECASE).strip()
                # Split by bullet points, pipes, or semicolons
                items = re.split(r"[•▪|;\n]", clean_text)
                for item in items:
                    item = item.strip()
                    if ":" in item:
                        k, v = item.split(":", 1)
                        add_spec(k, v)
                    elif " - " in item:
                        k, v = item.split(" - ", 1)
                        add_spec(k, v)

    # 4. Ensure Article number and EAN are always present if available
    sku = raw.get("sku") or raw.get("number") or raw.get("articleNumber")
    if sku:
        add_spec("Article number", sku)

    ean = raw.get("ean") or raw.get("gtin") or raw.get("upc")
    if ean:
        add_spec("EAN", ean)

    # Re-order sort_order
    for i, item in enumerate(specs):
        item["sort_order"] = i

    return specs

async def main():
    urls = [
        ("MZF-8624", "https://www.soundimports.eu/en/monacor-mzf-8624.html"),
        ("MZF-8625", "https://www.soundimports.eu/en/monacor-mzf-8625.html"),
        ("MMP006", "https://www.soundimports.eu/en/velleman-mmp006.html"),
        ("Classic-B65-Oak", "https://www.soundimports.eu/en/dayton-audio-classic-b65-oak.html"),
    ]
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
        for sku_label, url in urls:
            resp_j = await client.get(f"{url}?format=json")
            resp_h = await client.get(url)
            raw = resp_j.json().get("product", resp_j.json())
            specs = extract_specifications_clean(raw, resp_h.text)
            print("=" * 80)
            print(f"PRODUCT: {sku_label} ({url})")
            print(f"Total Specs: {len(specs)}")
            for s in specs:
                print(f"  [{s['sort_order']}] {repr(s['attribute_name'])}: {repr(s['attribute_value'])}")

if __name__ == "__main__":
    asyncio.run(main())
