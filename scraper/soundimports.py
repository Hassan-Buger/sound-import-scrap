import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

from app.config import settings
from scraper.base import BaseSupplierScraper
from scraper.client import HttpClient
from scraper.sitemap import SitemapParser
from scraper.category import CategoryScraper
from scraper.product import ProductScraper

logger = logging.getLogger("scraper.soundimports")


class SoundImportsScraper(BaseSupplierScraper):
    """Concrete scraper implementation for SoundImports.eu."""

    # Soundimports CDN pattern for images
    IMAGE_CDN_BASE = "https://cdn.webshopapp.com/shops/188510/files/"

    def __init__(self, client: Optional[HttpClient] = None):
        self._client = client or HttpClient(
            base_url=settings.base_url,
            concurrency=settings.product_concurrency,
            request_delay=settings.request_delay,
            max_retries=settings.max_retries,
            timeout=settings.request_timeout,
        )
        self.sitemap_parser = SitemapParser(self._client)
        self.category_scraper = CategoryScraper(self._client)
        self.product_scraper = ProductScraper(self._client)

    @property
    def name(self) -> str:
        return "SoundImports"

    @property
    def concurrency(self) -> int:
        return settings.concurrency

    async def discover_categories(self) -> List[Dict[str, Any]]:
        return await self.sitemap_parser.parse()

    async def get_product_list(
        self, category_url: str, page: int = 1, limit: int = 100
    ) -> Dict[str, Any]:
        return await self.category_scraper.fetch_page(
            category_url, page=page, limit=limit
        )

    def extract_product_summary(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Extract minimal product summary from a category-list product entry."""
        url_raw = raw.get("url", "")
        full_url = urljoin(settings.base_url + "/en/", url_raw) if url_raw else ""

        price_data = raw.get("price", {})
        price = None
        if isinstance(price_data, dict):
            price = price_data.get("price") or price_data.get("price_incl")

        # Include image ID for constructing full image URL
        image_id = raw.get("image")

        return {
            "product_id": str(raw.get("id", "")),
            "sku": str(raw.get("code") or raw.get("sku", "")),
            "url": full_url,
            "name": raw.get("title") or raw.get("fulltitle", ""),
            "price": price,
            "image_id": image_id,
        }

    async def get_product_detail(self, product_url: str) -> Dict[str, Any]:
        return await self.product_scraper.fetch_detail(product_url)

    def extract_product_detail(
        self, raw: Dict[str, Any], category_slug: str = None
    ) -> Dict[str, Any]:
        return self.product_scraper.extract_product_data(
            raw, category_slug=category_slug
        )
