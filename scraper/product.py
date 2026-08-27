import json
import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urljoin

from app.config import settings
from scraper.client import HttpClient
from scraper.urlutils import normalize_category_path, category_slug_from_path

logger = logging.getLogger("scraper.product")


class ProductScraper:
    """Scrapes full product details from product JSON endpoints."""

    IMAGE_CDN_BASE = "https://cdn.webshopapp.com/shops/188510/files/"

    # Sections that belong in long_description
    LONG_DESC_SECTIONS = {
        "highlights",
        "features",
        "product details",
        "benefits",
        "construction",
        "applications",
        "compatibility",
        "usage",
        "additional information",
        "description",
        "details",
        "specifications",
    }

    # Sections to stop at
    STOP_SECTIONS = {
        "reviews",
        "ratings",
        "alternatives",
        "related products",
        "recommended products",
        "frequently bought together",
        "recently viewed",
        "shipping",
        "returns",
        "payment",
        "support",
        "footer",
    }

    def __init__(self, client: HttpClient):
        self.client = client

    @staticmethod
    def _first_present(*values):
        for value in values:
            if value is not None and value != "":
                return value
        return None

    async def fetch_detail(self, product_url: str) -> Dict[str, Any]:
        """Fetch full product JSON from the detail endpoint."""
        params = {"format": "json"}
        data = await self.client.fetch_json(product_url, params=params)
        if "product" in data and isinstance(data["product"], dict):
            data = data["product"]
        return data

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text for comparison (strip tags, collapse whitespace)."""
        import html

        text = html.unescape(text)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("\u00a0", " ")
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text

    @staticmethod
    def _normalize_spec_name(name: str) -> str:
        """Normalize a spec-name for de-duplication."""
        import html

        name = html.unescape(name)
        name = re.sub(r":\s*$", "", name.strip())
        name = re.sub(r"\s+", " ", name).strip().lower()
        return name

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Description logic (Part 1 & 2)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_paragraph_text(text: str) -> str:
        if not text:
            return ""
        import html
        text = html.unescape(text)
        text = text.replace("\u00a0", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _build_descriptions(self, raw: Dict[str, Any]) -> Dict[str, str]:
        """Build short_description and long_description from raw product data."""
        content_html = raw.get("content")
        raw_desc = (raw.get("description") or raw.get("shortDescription") or "").strip()

        short_desc = None
        long_desc = None

        if content_html and isinstance(content_html, str) and content_html.strip():
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(content_html, "html.parser")

                # 1. Find the intro <p> tag, extract its text for short_desc,
                #    then REMOVE it from the DOM so it can never appear in long_desc
                intro_tag = self._find_intro_paragraph_tag(soup)
                if intro_tag:
                    short_desc = self._normalize_paragraph_text(intro_tag.get_text())
                    content_container = intro_tag.parent or soup
                    intro_tag.decompose()
                else:
                    content_container = soup

                # 2. Extract sections from the content container (intro already removed)
                long_desc = self._extract_long_description_sections(content_container)
            except Exception:
                pass

        if not short_desc and raw_desc:
            short_desc = self._normalize_paragraph_text(raw_desc)

        return {
            "short_description": short_desc or None,
            "long_description": long_desc or None,
        }

    def _find_intro_paragraph_tag(self, soup: "BeautifulSoup") -> Optional[Any]:
        """Return the first meaningful <p> tag for the intro."""
        for tag in soup.find_all(["p", "div"]):
            if self._is_heading(tag):
                continue
            if tag.find(["h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"]):
                continue
            text = tag.get_text(strip=True)
            if not text or len(text) < 15:
                continue
            if text.startswith("<"):
                continue
            norm_text = text.lower()
            if any(
                norm_text.startswith(x)
                for x in [
                    "highlights",
                    "features",
                    "specifications",
                    "product details",
                    "description",
                    "details",
                ]
            ):
                continue
            return tag
        return None

    def _extract_long_description_sections(
        self, container: "BeautifulSoup"
    ) -> Optional[str]:
        """Extract descriptive sections within a container using DOM traversal.

        Walks heading elements inside *container*, collects each target section
        and its following siblings until the next heading (or stop section).
        The intro <p> should already have been removed from the DOM before
        calling this.

        Using a scoped container (e.g. the intro paragraph's parent) avoids
        picking up layout-level headings like ``<h2>Product description ...</h2>``
        that sit outside the actual content area.
        """
        parts = []

        for heading in container.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            heading_text = heading.get_text(strip=True).lower()

            if any(stop in heading_text for stop in self.STOP_SECTIONS):
                break

            if any(section in heading_text for section in self.LONG_DESC_SECTIONS):
                section_parts = [str(heading)]
                for sibling in heading.find_next_siblings():
                    if sibling.name and sibling.name.startswith("h"):
                        break
                    section_parts.append(str(sibling))
                combined = "".join(section_parts).strip()
                if self._has_text(combined):
                    parts.append(combined)

        return "\n".join(parts) if parts else None

    @staticmethod
    def _has_text(html_str: str) -> bool:
        from bs4 import BeautifulSoup as _BS

        return bool(_BS(html_str, "html.parser").get_text(strip=True))

    @staticmethod
    def _is_heading(tag: "BeautifulSoup") -> bool:
        return tag.name and tag.name.startswith("h")

    # ------------------------------------------------------------------
    # Specification extraction (Part 3)
    # ------------------------------------------------------------------

    def _extract_attributes(
        self, raw: Dict[str, Any], html_doc: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Extract all product specifications as a sorted list of dicts.

        Combines structured JSON attributes/specs, HTML #specs DL/tables,
        and content HTML specification paragraphs.

        Each item::

            {"attribute_name": str, "attribute_value": str,
             "sort_order": int, "normalized_name": str}
        """
        attributes: List[Dict[str, Any]] = []
        seen_keys: Dict[str, int] = {}  # norm_name -> index in attributes

        def add_spec(key: Any, val: Any) -> None:
            if key is None:
                return
            import html

            k = html.unescape(str(key)).replace("\u00a0", " ")
            k = re.sub(r"\s+", " ", k).strip()
            k = re.sub(r":\s*$", "", k).strip()

            v = html.unescape(str(val)) if val is not None else ""
            v = v.replace("\u00a0", " ")
            v = re.sub(r"\s+", " ", v).strip()

            if not k or len(k) > 70:
                return
            # Ignore UI, navigation, or action buttons
            if any(
                ign in k.lower()
                for ign in [
                    "review",
                    "reviews",
                    "show more",
                    "show less",
                    "share",
                    "add to cart",
                    "delivery",
                ]
            ):
                return

            norm_k = self._normalize_spec_name(k)
            if not norm_k:
                return

            if norm_k in seen_keys:
                idx = seen_keys[norm_k]
                # If existing value was empty and new has a value, fill it
                if not attributes[idx]["attribute_value"] and v:
                    attributes[idx]["attribute_value"] = v
                return

            seen_keys[norm_k] = len(attributes)
            attributes.append(
                {
                    "attribute_name": k,
                    "attribute_value": v,
                    "sort_order": len(attributes),
                    "normalized_name": norm_k,
                }
            )

        # 1. Structured JSON attributes / specs
        raw_specs = raw.get("specs")
        if isinstance(raw_specs, dict):
            for spec_id, spec_data in raw_specs.items():
                if isinstance(spec_data, dict):
                    k = (
                        spec_data.get("title")
                        or spec_data.get("name")
                        or spec_data.get("label")
                    )
                    v = spec_data.get("value") or spec_data.get("text")
                    add_spec(k, v)

        raw_attrs = (
            raw.get("attributes")
            or raw.get("specifications")
            or raw.get("properties")
            or {}
        )
        if isinstance(raw_attrs, dict):
            for key, value in raw_attrs.items():
                add_spec(key, value)
        elif isinstance(raw_attrs, list):
            for item in raw_attrs:
                if isinstance(item, dict):
                    k = (
                        item.get("name")
                        or item.get("label")
                        or item.get("key")
                        or item.get("attribute_name")
                    )
                    v = (
                        item.get("value")
                        or item.get("text")
                        or item.get("attribute_value")
                    )
                    add_spec(k, v)

        # 2. Parse HTML content or document for dedicated Specifications
        content_html = raw.get("content") or ""
        html_sources = []
        if content_html:
            html_sources.append(content_html)
        if html_doc and html_doc != content_html:
            html_sources.append(html_doc)

        for h_src in html_sources:
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(h_src, "html.parser")

                # Look for #specs or section.specs (e.g. from full page HTML)
                specs_sec = soup.find(id="specs") or soup.find(
                    "section", class_=re.compile(r"specs|specifications", re.I)
                )
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
                                add_spec(
                                    cells[0].get_text(strip=True),
                                    cells[1].get_text(strip=True),
                                )

                # Look for dedicated Specifications paragraph in content HTML
                for tag in soup.find_all(["p", "div", "li"]):
                    strong = tag.find("strong")
                    has_spec_label = bool(
                        strong and "specification" in strong.get_text().lower()
                    )
                    tag_text = tag.get_text(" ", strip=True)
                    if has_spec_label or tag_text.lower().startswith("specifications:"):
                        clean_text = re.sub(
                            r"^.*?specifications?\s*:?\s*",
                            "",
                            tag_text,
                            flags=re.IGNORECASE,
                        ).strip()
                        items = re.split(r"[•▪|;\n]", clean_text)
                        for itm in items:
                            itm = itm.strip()
                            if ":" in itm:
                                k, v = itm.split(":", 1)
                                add_spec(k, v)
                            elif " - " in itm:
                                k, v = itm.split(" - ", 1)
                                add_spec(k, v)
            except Exception as exc:
                logger.debug("HTML specification extraction error: %s", exc)

        # 3. Always include foundational identity specifications (Article number & EAN)
        sku = raw.get("sku") or raw.get("number") or raw.get("articleNumber")
        if sku:
            add_spec("Article number", sku)

        ean = raw.get("ean") or raw.get("gtin") or raw.get("upc")
        if ean:
            add_spec("EAN", ean)

        # Reassign sort_order based on final sequence
        for i, attr in enumerate(attributes):
            attr["sort_order"] = i

        return attributes

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------

    def _extract_images(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        images: List[Dict[str, Any]] = []
        seen_urls: set = set()

        image_sources = []

        cover = (
            raw.get("cover")
            or raw.get("image")
            or raw.get("mainImage")
            or raw.get("thumbnail")
        )
        if cover:
            image_sources.append(("cover", cover))

        gallery = raw.get("images") or raw.get("gallery") or raw.get("pictures") or []
        if isinstance(gallery, list):
            for i, img in enumerate(gallery):
                if isinstance(img, dict):
                    url = (
                        img.get("url")
                        or img.get("src")
                        or img.get("path")
                        or img.get("image")
                    )
                elif isinstance(img, str):
                    url = img
                else:
                    continue
                if url:
                    image_sources.append((f"gallery_{i}", url))

        for sort_order, (label, url) in enumerate(image_sources):
            url_str = str(url)
            if url_str and url_str not in seen_urls:
                seen_urls.add(url_str)
                images.append(
                    {
                        "image_url": self._build_image_url(url_str),
                        "sort_order": sort_order,
                        "is_cover": label == "cover",
                    }
                )

        return images

    def _build_image_url(self, image_ref: str) -> str:
        if image_ref.startswith("http://") or image_ref.startswith("https://"):
            return image_ref
        if image_ref.isdigit():
            return f"{self.IMAGE_CDN_BASE}{image_ref}/500x500x2.jpg"
        return urljoin(settings.base_url + "/", image_ref)

    @staticmethod
    def _slugify_category(raw: str) -> str:
        """Best-effort slug for a category name/path fragment.

        Prefers the canonical path leaf; falls back to a simple slugify so the
        legacy ``category_ids`` column keeps a usable value.
        """
        if not raw:
            return ""
        path = normalize_category_path(raw, base_url=settings.base_url)
        slug = category_slug_from_path(path)
        if slug:
            return slug
        slug = raw.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug.strip("-")

    def _extract_product_categories(
        self, raw: Dict[str, Any], category_slug: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Extract the authoritative list of categories for a product.

        SoundImports product JSON exposes ``product.categories`` as a dict
        keyed by numeric category id, where each node is::

            {
              "id": 4401362, "parent": ..., "path": [...ids...],
              "depth": 3, "url": "home-audio/speakers/bookshelf-speakers",
              "title": "Bookshelf speakers", "count": 52
            }

        Every node is normalized to a canonical path so the relationship is
        keyed by stable category identity, never by slug alone.
        """
        result: List[Dict[str, Any]] = []
        seen_paths: set = set()

        categories = raw.get("categories") or raw.get("category") or []
        if isinstance(categories, dict):
            raw_nodes = list(categories.values())
        elif isinstance(categories, list):
            raw_nodes = categories
        else:
            raw_nodes = []

        for node in raw_nodes:
            if not isinstance(node, dict):
                continue
            raw_url = node.get("url") or node.get("path_url") or node.get("link") or ""
            title = node.get("title") or node.get("name") or node.get("slug") or ""
            canonical_path = normalize_category_path(
                raw_url, base_url=settings.base_url.rstrip("/") + "/en/"
            )
            if not canonical_path or canonical_path == "/":
                continue
            if canonical_path in seen_paths:
                continue
            seen_paths.add(canonical_path)
            cat_id = node.get("id") or node.get("category_id")
            result.append(
                {
                    "category_id": str(cat_id) if cat_id is not None else None,
                    "canonical_path": canonical_path,
                    "slug": category_slug_from_path(canonical_path),
                    "name": title,
                    "url": canonical_path.lstrip("/"),
                }
            )

        # A product discovered inside a category page must always be associated
        # with that source category even if the detail JSON omits it.
        if category_slug:
            source_path = normalize_category_path(
                category_slug,
                base_url=settings.base_url.rstrip("/") + "/en/",
            )
            if source_path and source_path != "/" and source_path not in seen_paths:
                result.append(
                    {
                        "category_id": None,
                        "canonical_path": source_path,
                        "slug": category_slug_from_path(source_path),
                        "name": category_slug,
                        "url": source_path.lstrip("/"),
                    }
                )

        return result

    def extract_product_data(
        self,
        raw: Dict[str, Any],
        category_slug: Optional[str] = None,
        html_doc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Normalize raw product JSON into a structured dict for DB upsert."""
        product_id = str(
            raw.get("id") or raw.get("productId") or raw.get("product_id", "")
        )
        variant_id = raw.get("vid") or raw.get("variantId") or ""
        sku = (
            raw.get("sku")
            or raw.get("number")
            or raw.get("articleNumber")
            or variant_id
            or product_id
        )
        if not sku:
            # A random fallback creates a new product on every rerun. Missing
            # stable identity is a data-quality failure and must be visible.
            raise ValueError("Product detail has neither SKU nor stable product ID")
        ean = raw.get("ean") or raw.get("gtin") or raw.get("upc")
        title = raw.get("title") or raw.get("name") or raw.get("productName")

        price_data = raw.get("price")
        if price_data is None:
            price_data = raw.get("prices") or {}
        if isinstance(price_data, dict):
            price = self._first_present(
                price_data.get("amount"),
                price_data.get("value"),
                price_data.get("price"),
            )
        else:
            price = price_data

        stock_data = raw.get("stock")
        if stock_data is None:
            stock_data = raw.get("inventory") or raw.get("availability") or {}

        stock = None
        if isinstance(stock_data, dict):
            raw_qty = self._first_present(
                stock_data.get("level"),
                stock_data.get("quantity"),
                stock_data.get("qty"),
                stock_data.get("stock"),
            )
            if raw_qty is not None and str(raw_qty).strip() != "":
                try:
                    stock = int(raw_qty)
                except (ValueError, TypeError):
                    stock = None
        # Check variants if stock level was not in root stock dict
        if stock is None:
            variants = raw.get("variants")
            if isinstance(variants, dict):
                var_levels = []
                for v in variants.values():
                    if isinstance(v, dict):
                        v_st = v.get("stock")
                        if isinstance(v_st, dict) and v_st.get("level") is not None:
                            try:
                                var_levels.append(int(v_st["level"]))
                            except (ValueError, TypeError):
                                pass
                        elif v.get("stock_level") is not None:
                            try:
                                var_levels.append(int(v["stock_level"]))
                            except (ValueError, TypeError):
                                pass
                if var_levels:
                    stock = sum(var_levels)

        # Fallback to HTML if stock was not in JSON
        if stock is None and html_doc:
            match = re.search(r"(\d+)\s*\+?\s*in\s*stock", html_doc, re.IGNORECASE)
            if match:
                try:
                    stock = int(match.group(1))
                except (ValueError, TypeError):
                    pass
            elif re.search(r"out\s*of\s*stock", html_doc, re.IGNORECASE):
                stock = 0

        stock_status = None
        if isinstance(stock_data, dict):
            on_stock = stock_data.get("on_stock")
            available = stock_data.get("available")
            allow_backorder = stock_data.get("allow_outofstock_sale") or stock_data.get("delivery")
            level = stock_data.get("level")

            if on_stock is True or (isinstance(level, int) and level > 0):
                stock_status = "in_stock"
            elif on_stock is False and (available is True or allow_backorder):
                stock_status = "on_backorder"
            elif on_stock is False or available is False or level == 0:
                stock_status = "out_of_stock"
            else:
                raw_status = str(stock_data.get("status") or stock_data.get("text") or "").lower()
                if "in stock" in raw_status or "available" in raw_status:
                    stock_status = "in_stock"
                elif "out of stock" in raw_status:
                    stock_status = "out_of_stock"
                elif "backorder" in raw_status or "preorder" in raw_status:
                    stock_status = "on_backorder"
        elif isinstance(stock_data, str):
            st_lower = stock_data.lower()
            if "in stock" in st_lower or "in_stock" in st_lower:
                stock_status = "in_stock"
            elif "out of stock" in st_lower or "out_of_stock" in st_lower:
                stock_status = "out_of_stock"
            elif "backorder" in st_lower:
                stock_status = "on_backorder"

        if not stock_status:
            raw_st = str(raw.get("stockStatus") or raw.get("availabilityText") or "").lower()
            if "in stock" in raw_st or "in_stock" in raw_st:
                stock_status = "in_stock"
            elif "out of stock" in raw_st or "out_of_stock" in raw_st:
                stock_status = "out_of_stock"
            elif "backorder" in raw_st:
                stock_status = "on_backorder"
            elif stock is not None:
                stock_status = "in_stock" if stock > 0 else "out_of_stock"

        if not stock_status and html_doc:
            if re.search(r"in\s*stock", html_doc, re.IGNORECASE):
                stock_status = "in_stock"
            elif re.search(r"out\s*of\s*stock", html_doc, re.IGNORECASE):
                stock_status = "out_of_stock"
            elif re.search(r"back\s*in\s*stock|backorder", html_doc, re.IGNORECASE):
                stock_status = "on_backorder"

        brand_data = raw.get("brand") or raw.get("manufacturer") or {}
        if isinstance(brand_data, dict):
            brand = brand_data.get("name") or brand_data.get("title")
        else:
            brand = str(brand_data) if brand_data else None

        currency = raw.get("currency") or "EUR"
        url = raw.get("url") or raw.get("productUrl") or raw.get("link")
        if url and not url.startswith("http"):
            url = urljoin(settings.base_url.rstrip("/") + "/en/", url)

        product_categories = self._extract_product_categories(
            raw, category_slug=category_slug
        )

        # Legacy comma-separated leaf slugs, retained for backward
        # compatibility/diagnostics. The authoritative relationships live in
        # product_categories (a list of category nodes with canonical paths).
        category_ids = None
        slugs = []
        for pc in product_categories:
            slug = pc.get("slug")
            if slug and slug not in slugs:
                slugs.append(slug)
        if slugs:
            category_ids = ",".join(slugs)
        source_slug = self._slugify_category(category_slug) if category_slug else None
        if not category_ids and source_slug:
            category_ids = source_slug

        images_list = self._extract_images(raw)
        attributes_list = self._extract_attributes(raw, html_doc=html_doc)
        price_val = float(price) if price is not None and price != "" else None
        descriptions = self._build_descriptions(raw)

        logger.info(
            "Extracted product SKU=%s stock=%s stock_status=%s specifications_count=%d",
            sku,
            stock,
            stock_status,
            len(attributes_list),
        )

        return {
            "product_id": product_id,
            "sku": str(sku),
            "ean": str(ean) if ean else None,
            "title": title,
            "description": descriptions["short_description"],
            "short_description": descriptions["short_description"],
            "long_description": descriptions["long_description"],
            "regular_price": price_val,
            "price": price_val,
            "stock": int(stock) if stock is not None and stock != "" else None,
            "stock_status": stock_status,
            "brand": brand,
            "currency": currency if currency else "EUR",
            "url": url,
            "category_ids": category_ids,
            "product_categories": product_categories,
            "raw_json": json.dumps(raw, ensure_ascii=False, default=str),
            "images": images_list,
            "attributes": attributes_list,
        }
