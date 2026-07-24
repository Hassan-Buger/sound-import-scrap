import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup, Tag

from app.config import settings
from scraper.client import HttpClient

logger = logging.getLogger("scraper.sitemap")


class SitemapParser:
    """Parses the HTML sitemap to discover categories and their hierarchy.

    The SoundImports sitemap at /en/sitemap/ renders categories in nested
    <ul> elements inside a div.gui-list section labelled "Categories:".
    """

    def __init__(self, client: HttpClient):
        self.client = client

    async def parse(self) -> List[Dict[str, Any]]:
        """Fetch and parse the sitemap, returning a flat list of categories.

        Each entry:
            - name: str
            - slug: str
            - url: str (absolute)
            - level: int (0=root, 1=sub, 2=sub-sub)
            - parent_slug: Optional[str]
        """
        html = await self.client.fetch_html(settings.sitemap_url)
        return self._parse_html(html)

    def _parse_html(self, html: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")

        categories_section = soup.find(
            "div",
            class_="gui-list",
            attrs={"aria-labelledby": lambda v: v and "categories" in v},
        )
        if not categories_section:
            categories_section = soup.find(
                "div", class_="gui-list",
                string=lambda t: t and "Categories" in t,
            )

        categories: List[Dict[str, Any]] = []
        seen_slugs: set = set()

        if categories_section:
            root_ul = categories_section.find("ul")
            if root_ul:
                self._parse_ul(root_ul, categories, seen_slugs, parent_slug=None, level=0)

        if not categories:
            logger.warning("No categories found in gui-list; fallback parsing")
            categories = self._parse_fallback(soup)

        logger.info("Discovered %d categories from sitemap", len(categories))
        return categories

    EXCLUDED_PATHS = (
        "/brands/", "/service/", "/blogs/", "/account/",
        "/compare/", "/cart/", "/wishlist/",
    )

    def _parse_ul(
        self,
        ul_tag: Tag,
        result: List[Dict[str, Any]],
        seen_slugs: set,
        parent_slug: Optional[str],
        level: int,
    ):
        for li in ul_tag.find_all("li", recursive=False):
            a_tag = li.find("a", href=True) if li.name == "li" else None
            if not a_tag:
                continue

            href = a_tag.get("href", "")
            name = a_tag.get_text(strip=True)
            if not name or not href:
                continue

            if any(excl in href for excl in self.EXCLUDED_PATHS):
                continue

            name = self._clean_name(name)
            if not name:
                continue

            full_url = urljoin(settings.base_url, href)
            slug = self._extract_slug(full_url)
            if not slug:
                continue

            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            entry = {
                "name": name,
                "slug": slug,
                "url": full_url,
                "level": level,
                "parent_slug": parent_slug,
            }
            result.append(entry)

            child_ul = li.find("ul")
            if child_ul:
                self._parse_ul(child_ul, result, seen_slugs, parent_slug=slug, level=level + 1)

    def _clean_name(self, raw_name: str) -> str:
        name = raw_name.strip()
        name = re.sub(r'\s*\(\d+\)\s*$', '', name)
        name = name.strip()
        return name

    def _parse_fallback(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        categories: List[Dict[str, Any]] = []
        seen_slugs: set = set()

        for ul in soup.find_all("ul"):
            links = ul.find_all("a", href=True)
            if len(links) < 3:
                continue
            top_level_count = 0
            for li in ul.find_all("li", recursive=False):
                if li.find("a", href=True):
                    top_level_count += 1
            if top_level_count < 3:
                continue
            hrefs = [a.get("href", "") for a in links]
            cat_hrefs = [h for h in hrefs if "/en/" in h and "/en/brands/" not in h and "/en/service/" not in h]
            if len(cat_hrefs) < 3:
                continue
            return self._parse_ul_fallback(ul, seen_slugs)

        return categories

    def _parse_ul_fallback(self, ul_tag: Tag, seen_slugs: set, parent_slug: Optional[str] = None, level: int = 0) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for li in ul_tag.find_all("li", recursive=False):
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            name = self._clean_name(a_tag.get_text(strip=True))
            if not name or not href:
                continue
            if not href.startswith("http") and not href.startswith("/"):
                continue
            if "/brands/" in href or "/service/" in href or "/blogs/" in href or "/account/" in href:
                continue

            full_url = urljoin(settings.base_url, href)
            slug = self._extract_slug(full_url)
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            result.append({
                "name": name,
                "slug": slug,
                "url": full_url,
                "level": level,
                "parent_slug": parent_slug,
            })

            child_ul = li.find("ul")
            if child_ul:
                result.extend(self._parse_ul_fallback(child_ul, seen_slugs, parent_slug=slug, level=level + 1))

        return result

    def _extract_slug(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        parts = [p for p in path.split("/") if p and p != "en"]
        if parts:
            return parts[-1]
        return ""

    def _slugify(self, name: str) -> str:
        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug.strip("-")
