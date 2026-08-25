import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup
from urllib.parse import urljoin

from scraper.client import HttpClient
from scraper.sitemap import SitemapParser
from scraper.urlutils import normalize_category_path, category_slug_from_path, parent_path


async def main():
    client = HttpClient()
    try:
        print("=== 1. FETCHING HTML SITEMAP ===")
        sitemap_parser = SitemapParser(client)
        html_categories = await sitemap_parser.parse()
        print(f"Total HTML sitemap categories: {len(html_categories)}")
        html_paths = {c["canonical_path"]: c for c in html_categories}

        print("\n=== 2. FETCHING XML SITEMAP (/en/sitemap.xml) ===")
        xml_text = await client.fetch_html("https://www.soundimports.eu/en/sitemap.xml")
        soup = BeautifulSoup(xml_text, "xml")
        xml_locs = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
        print(f"Total XML URLs: {len(xml_locs)}")

        xml_category_paths = set()
        xml_product_urls = set()
        for loc in xml_locs:
            if not loc.startswith("https://www.soundimports.eu/en/"):
                continue
            if any(x in loc for x in ["/service/", "/blogs/", "/blog/", "/cart", "/account", "/brands/", "/pages/", "/compare/", "/wishlist/"]):
                continue
            if loc.endswith(".html") or ".html" in loc:
                xml_product_urls.add(loc)
            else:
                p = normalize_category_path(loc)
                if p and p not in ("/", "/en/", "/en/catalog/", "/en/collection/", "/en/sitemap/"):
                    xml_category_paths.add(p)

        print(f"XML non-excluded category paths: {len(xml_category_paths)}")
        xml_not_in_html = sorted(xml_category_paths - set(html_paths.keys()))
        print(f"Paths in XML sitemap but NOT in HTML sitemap ({len(xml_not_in_html)}):")
        for p in xml_not_in_html:
            print(f"  {p}")

        print("\n=== 3. CHECKING HOMEPAGE & HEADER NAVIGATION ===")
        home_html = await client.fetch_html("https://www.soundimports.eu/en/")
        home_soup = BeautifulSoup(home_html, "lxml")
        nav_paths = set()
        for a in home_soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href for x in ["/service/", "/blogs/", "/blog/", "/cart", "/account", "/brands/", "/pages/", "/compare/", "/wishlist/"]):
                continue
            full = urljoin("https://www.soundimports.eu", href)
            p = normalize_category_path(full)
            if p and p not in ("/", "/en/", "/en/catalog/", "/en/collection/", "/en/sitemap/"):
                nav_paths.add(p)

        print(f"Homepage category paths: {len(nav_paths)}")
        nav_not_in_html = sorted(nav_paths - set(html_paths.keys()))
        print(f"Paths in Homepage nav but NOT in HTML sitemap ({len(nav_not_in_html)}):")
        for p in nav_not_in_html:
            print(f"  {p}")

        print("\n=== 4. CHECKING CATEGORIES FROM PRODUCT JSON DATA ===")
        # Test 10 product URLs from XML sitemap to see what category nodes they return
        product_discovered_categories = {}
        for p_url in list(xml_product_urls)[:10]:
            try:
                p_data = await client.fetch_json(p_url, params={"format": "json"})
                if "product" in p_data:
                    p_data = p_data["product"]
                cats = p_data.get("categories", {})
                if isinstance(cats, dict):
                    for cid, cinfo in cats.items():
                        c_url = cinfo.get("url")
                        if c_url:
                            canon = normalize_category_path(c_url, base_url="https://www.soundimports.eu/en/")
                            if canon:
                                product_discovered_categories[canon] = {
                                    "id": cid,
                                    "title": cinfo.get("title"),
                                    "url": c_url,
                                    "count": cinfo.get("count"),
                                }
            except Exception as e:
                print(f"Error fetching product {p_url}: {e}")

        print(f"Categories discovered inside 10 product details: {len(product_discovered_categories)}")
        prod_not_in_html = sorted(set(product_discovered_categories.keys()) - set(html_paths.keys()))
        print(f"Categories in products but NOT in HTML sitemap ({len(prod_not_in_html)}):")
        for p in prod_not_in_html:
            print(f"  {p} -> {product_discovered_categories[p]}")

        print("\n=== 5. CHECKING CATEGORY FETCHING (COLLECTION VS CATALOG) ===")
        sample_cats = [
            "/en/home-audio/",
            "/en/home-audio/speakers/",
            "/en/home-audio/speakers/bookshelf-speakers/",
            "/en/audio-components/",
            "/en/audio-components/woofers/",
            "/en/sale/",
            "/en/sale/sale/",
        ]
        for cpath in sample_cats:
            curl = f"https://www.soundimports.eu{cpath}"
            cdata = await client.fetch_json(curl, params={"format": "json", "limit": 100})
            has_coll = "collection" in cdata
            has_cat = "catalog" in cdata
            coll_cnt = cdata.get("collection", {}).get("count") if has_coll else None
            coll_prods = len(cdata.get("collection", {}).get("products", [])) if has_coll else 0
            cat_subcats = len(cdata.get("catalog", {}).get("categories", {})) if has_cat else 0
            print(f"  {cpath}: collection={has_coll} (cnt={coll_cnt}, prods={coll_prods}), catalog={has_cat} (subcats={cat_subcats})")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
