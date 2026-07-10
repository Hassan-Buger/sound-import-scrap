import json
import logging
import uuid
from typing import Dict, Any, Optional, List

from app.config import settings
from scraper.client import HttpClient

logger = logging.getLogger("scraper.product")


class ProductScraper:
    """Scrapes full product details from product JSON endpoints."""

    def __init__(self, client: HttpClient):
        self.client = client

    async def fetch_detail(self, product_url: str) -> Dict[str, Any]:
        """Fetch full product JSON from the detail endpoint."""
        params = {"format": "json"}
        data = await self.client.fetch_json(product_url, params=params)
        if "product" in data and isinstance(data["product"], dict):
            data = data["product"]
        return data

    def extract_product_data(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize raw product JSON into a structured dict for DB upsert."""
        product_id = str(raw.get("id") or raw.get("productId") or raw.get("product_id", ""))
        variant_id = raw.get("vid") or raw.get("variantId") or ""
        sku = raw.get("sku") or raw.get("number") or raw.get("articleNumber") or variant_id or product_id
        if not sku:
            sku = f"SI-FALLBACK-{uuid.uuid4().hex[:12]}"
        ean = raw.get("ean") or raw.get("gtin") or raw.get("upc")
        title = raw.get("title") or raw.get("name") or raw.get("productName")
        description = raw.get("description") or raw.get("shortDescription")
        long_description = raw.get("longDescription") or raw.get("descriptionDetail")

        price_data = raw.get("price") or raw.get("prices") or {}
        if isinstance(price_data, dict):
            price = price_data.get("amount") or price_data.get("value") or price_data.get("price")
        else:
            price = price_data

        stock_data = raw.get("stock") or raw.get("inventory") or raw.get("availability") or {}
        if isinstance(stock_data, dict):
            stock = stock_data.get("quantity") or stock_data.get("qty") or stock_data.get("stock")
        else:
            stock = None

        stock_status = raw.get("stockStatus") or raw.get("availabilityText")
        if isinstance(stock_data, dict):
            stock_status = stock_status or stock_data.get("status") or stock_data.get("text")

        brand_data = raw.get("brand") or raw.get("manufacturer") or {}
        if isinstance(brand_data, dict):
            brand = brand_data.get("name") or brand_data.get("title")
        else:
            brand = str(brand_data) if brand_data else None

        currency = raw.get("currency") or "EUR"
        url = raw.get("url") or raw.get("productUrl") or raw.get("link")

        categories = raw.get("categories") or raw.get("category") or []
        category_ids = None
        if isinstance(categories, list):
            slugs = []
            for cat in categories:
                if isinstance(cat, dict):
                    slugs.append(cat.get("slug") or cat.get("url") or str(cat.get("id", "")))
                elif isinstance(cat, str):
                    slugs.append(cat)
            if slugs:
                category_ids = ",".join(slugs)
        elif isinstance(categories, str):
            category_ids = categories

        images_list = self._extract_images(raw)
        attributes_list = self._extract_attributes(raw)

        price_val = float(price) if price else None

        return {
            "product_id": product_id,
            "sku": str(sku),
            "ean": str(ean) if ean else None,
            "title": title,
            "description": description,
            "short_description": description,
            "long_description": long_description,
            "regular_price": price_val,
            "price": price_val,
            "stock": int(stock) if stock else None,
            "stock_status": stock_status,
            "brand": brand,
            "currency": currency if currency else "EUR",
            "url": url,
            "category_ids": category_ids,
            "raw_json": json.dumps(raw, ensure_ascii=False, default=str),
            "images": images_list,
            "attributes": attributes_list,
        }

    def _extract_images(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        images: List[Dict[str, Any]] = []
        seen_urls: set = set()

        image_sources = []

        cover = raw.get("cover") or raw.get("image") or raw.get("mainImage") or raw.get("thumbnail")
        if cover:
            image_sources.append(("cover", cover))

        gallery = raw.get("images") or raw.get("gallery") or raw.get("pictures") or []
        if isinstance(gallery, list):
            for i, img in enumerate(gallery):
                if isinstance(img, dict):
                    url = img.get("url") or img.get("src") or img.get("path") or img.get("image")
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
                images.append({
                    "image_url": url_str,
                    "sort_order": sort_order,
                    "is_cover": label == "cover",
                })

        return images

    def _extract_attributes(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        attributes: List[Dict[str, Any]] = []
        seen_keys: set = set()

        spec_sources = [
            raw.get("attributes", {}),
            raw.get("specifications", {}),
            raw.get("properties", {}),
            raw.get("features", {}),
        ]

        raw_attrs = raw.get("attributes") or raw.get("specifications") or raw.get("properties") or {}
        if isinstance(raw_attrs, dict):
            for key, value in raw_attrs.items():
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    val_str = str(value) if value is not None else ""
                    attributes.append({
                        "attribute_name": key,
                        "attribute_value": val_str,
                    })

        specs_list = raw.get("specificationsList") or raw.get("featureList") or []
        if isinstance(specs_list, list):
            for item in specs_list:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("label") or item.get("key")
                    value = item.get("value") or item.get("text")
                    if name and name not in seen_keys:
                        seen_keys.add(name)
                        attributes.append({
                            "attribute_name": name,
                            "attribute_value": str(value) if value else "",
                        })

        return attributes
