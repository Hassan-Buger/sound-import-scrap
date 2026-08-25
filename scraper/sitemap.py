import re
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.config import settings
from scraper.client import HttpClient
from scraper.urlutils import (
    normalize_category_path,
    category_slug_from_path,
)

logger = logging.getLogger("scraper.sitemap")


class SitemapParser:
    """Parses the HTML sitemap to discover categories and their hierarchy.

    The SoundImports sitemap at ``/en/sitemap/`` renders categories inside a
    ``div.gui-list[aria-labelledby*=categories]`` as nested ``<ul>/<li>``
    elements, e.g.::

        <div class="gui-list" aria-labelledby="gui-sitemap-group-categories-title">
          <strong ...>Categories:</strong><br />
          <ul>
            <li><a href=".../en/home-audio/" title="Home audio">Home audio <span>(513)</span></a>
              <ul>
                <li><a href=".../en/home-audio/speakers/">Speakers <span>(114)</span></a>
                  ...

    Category identity is the **canonical path** (``/en/home-audio/speakers/``),
    never the final slug. Two categories that share a slug in different branches
    (e.g. ``/en/home-audio/amplifiers/switches/`` and
    ``/en/accessories/electromechanics/switches/``) are both kept.
    """

    def __init__(self, client: HttpClient):
        self.client = client
        self.last_diagnostics: Dict[str, Any] = {}

    async def parse(self) -> List[Dict[str, Any]]:
        """Fetch and parse the sitemap, returning a flat tree of categories.

        Each entry contains::

            name, slug, url, canonical_path, parent_path, level, source_count
        """
        html = await self.client.fetch_html(settings.sitemap_url)
        categories = self._parse_html(html)

        # Merge additional valid categories from XML sitemap if present
        try:
            xml_categories = await self._discover_xml_categories()
            if xml_categories:
                existing_paths = {c["canonical_path"] for c in categories}
                for xml_cat in xml_categories:
                    if xml_cat["canonical_path"] not in existing_paths:
                        categories.append(xml_cat)
                        existing_paths.add(xml_cat["canonical_path"])
        except Exception as exc:
            logger.debug("Optional XML sitemap discovery skipped: %s", exc)

        if not categories:
            raise ValueError(
                "SoundImports sitemap contained no parseable categories; "
                "the source markup may have changed"
            )
        categories = self._dedupe_by_path(categories)
        logger.info("Discovered %d categories from sitemap", len(categories))
        return categories

    async def _discover_xml_categories(self) -> List[Dict[str, Any]]:
        xml_url = urljoin(settings.base_url, "/en/sitemap.xml")
        try:
            xml_text = await self.client.fetch_html(xml_url)
            soup = BeautifulSoup(xml_text, "xml")
            discovered: List[Dict[str, Any]] = []
            from scraper.urlutils import parent_path, path_level
            from app.schemas import format_category_name

            for loc in soup.find_all("loc"):
                url_str = loc.get_text(strip=True)
                if not url_str.startswith("https://www.soundimports.eu/en/"):
                    continue
                if url_str.endswith(".html") or ".html" in url_str:
                    continue
                if any(excl in url_str.lower() for excl in self.EXCLUDED_PATHS):
                    continue
                canon = normalize_category_path(url_str)
                if not canon or canon in (
                    "/",
                    "/en/",
                    "/en/catalog/",
                    "/en/collection/",
                    "/en/sitemap/",
                ):
                    continue
                if canon.startswith("/en/tags/"):
                    continue
                slug = category_slug_from_path(canon)
                discovered.append(
                    {
                        "name": format_category_name(slug) or slug,
                        "slug": slug,
                        "url": url_str,
                        "canonical_path": canon,
                        "parent_path": parent_path(canon),
                        "level": path_level(canon),
                        "source_count": 0,
                    }
                )
            return discovered
        except Exception:
            return []

    # ------------------------------------------------------------- parsing

    def _parse_html(self, html: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")

        categories_section = soup.find(
            "div",
            class_="gui-list",
            attrs={"aria-labelledby": lambda v: v and "categories" in v},
        )
        if not categories_section:
            categories_section = soup.find(
                "div",
                class_="gui-list",
                string=lambda t: t and "Categories" in t,
            )

        result: List[Dict[str, Any]] = []
        if categories_section:
            root_ul = categories_section.find("ul")
            if root_ul:
                self._parse_ul(root_ul, result, parent_node=None, level=1)

        if not result:
            logger.warning("No categories found in gui-list; fallback parsing")
            result = self._parse_fallback(soup)

        raw_count = len(result)
        path_counts: Dict[str, int] = {}
        for category in result:
            path = category.get("canonical_path")
            if path:
                path_counts[path] = path_counts.get(path, 0) + 1
        duplicate_paths = sorted(
            path for path, count in path_counts.items() if count > 1
        )
        result = self._dedupe_by_path(result)
        self.last_diagnostics = {
            "raw_nodes": raw_count,
            "normalized_nodes": len(result),
            "duplicate_paths": duplicate_paths,
        }
        return result

    EXCLUDED_PATHS = (
        "/brands/",
        "/service/",
        "/blogs/",
        "/blog/",
        "/account/",
        "/compare/",
        "/cart/",
        "/wishlist/",
        "/pages/",
    )

    def _parse_ul(
        self,
        ul_tag: Tag,
        result: List[Dict[str, Any]],
        parent_node: Optional[Dict[str, Any]],
        level: int,
    ):
        """Recursively walk nested ``<ul>`` elements building the category tree.

        Parentage is derived from the actual DOM nesting (the parent node's
        canonical path), never from slug similarity.
        """
        for li in ul_tag.find_all("li", recursive=False):
            a_tag = (
                li.find("a", href=True, recursive=False)
                if li.name == "li"
                else None
            )
            if not a_tag:
                continue

            href = a_tag.get("href", "")
            name = self._clean_name(a_tag.get_text(strip=True))
            if not name or not href:
                continue

            if any(excl in href.lower() for excl in self.EXCLUDED_PATHS):
                continue

            full_url = urljoin(settings.base_url, href)
            canonical_path = normalize_category_path(full_url)
            if not canonical_path or canonical_path == "/":
                continue

            node = {
                "name": name,
                "slug": category_slug_from_path(canonical_path),
                "url": full_url,
                "canonical_path": canonical_path,
                "parent_path": parent_node["canonical_path"] if parent_node else None,
                "level": level,
                "source_count": self._extract_count(a_tag),
            }
            result.append(node)

            child_ul = li.find("ul", recursive=False)
            if child_ul:
                self._parse_ul(child_ul, result, parent_node=node, level=level + 1)

    def _parse_fallback(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []

        for ul in soup.find_all("ul"):
            top_level_count = sum(
                1
                for li in ul.find_all("li", recursive=False)
                if li.find("a", href=True)
            )
            if top_level_count < 3:
                continue
            hrefs = [a.get("href", "") for a in ul.find_all("a", href=True)]
            cat_hrefs = [
                h
                for h in hrefs
                if "/en/" in h and not any(e in h for e in self.EXCLUDED_PATHS)
            ]
            if len(cat_hrefs) < 3:
                continue
            result = self._parse_ul_fallback(ul, parent_node=None, level=1)
            if result:
                break

        return result

    def _parse_ul_fallback(
        self,
        ul_tag: Tag,
        parent_node: Optional[Dict[str, Any]],
        level: int,
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for li in ul_tag.find_all("li", recursive=False):
            a_tag = li.find("a", href=True, recursive=False)
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            name = self._clean_name(a_tag.get_text(strip=True))
            if not name or not href:
                continue
            if not href.startswith("http") and not href.startswith("/"):
                continue
            if any(excl in href.lower() for excl in self.EXCLUDED_PATHS):
                continue

            full_url = urljoin(settings.base_url, href)
            canonical_path = normalize_category_path(full_url)
            if not canonical_path or canonical_path == "/":
                continue

            node = {
                "name": name,
                "slug": category_slug_from_path(canonical_path),
                "url": full_url,
                "canonical_path": canonical_path,
                "parent_path": parent_node["canonical_path"] if parent_node else None,
                "level": level,
                "source_count": self._extract_count(a_tag),
            }
            result.append(node)

            child_ul = li.find("ul", recursive=False)
            if child_ul:
                result.extend(
                    self._parse_ul_fallback(child_ul, parent_node=node, level=level + 1)
                )

        return result

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _dedupe_by_path(categories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set = set()
        cleaned: List[Dict[str, Any]] = []
        for cat in categories:
            path = cat.get("canonical_path")
            if not path or path in seen:
                logger.warning(
                    "Skipping duplicate canonical path: %s (%s)",
                    path,
                    cat.get("name"),
                )
                continue
            seen.add(path)
            cleaned.append(cat)
        return cleaned

    @staticmethod
    def _extract_count(a_tag: Tag) -> int:
        """Extract the product count from ``<a>Name <span>(N)</span></a>``."""
        span = a_tag.find("span")
        if span:
            match = re.search(r"\((\d{1,8})\)", span.get_text(strip=True))
            if match:
                return int(match.group(1))
        text = a_tag.get_text(strip=True)
        match = re.search(r"\((\d{1,8})\)$", text)
        if match:
            return int(match.group(1))
        return 0

    def _clean_name(self, raw_name: str) -> str:
        name = raw_name.strip()
        name = re.sub(r"\s*\(\d+\)\s*$", "", name)
        return name.strip()

    def _extract_slug(self, url: str) -> str:
        return category_slug_from_path(normalize_category_path(url))

    @staticmethod
    def _slugify(name: str) -> str:
        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug.strip("-")
