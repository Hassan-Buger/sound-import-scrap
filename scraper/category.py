import logging
from typing import List, Dict, Any, Optional

from app.config import settings
from scraper.client import HttpClient

logger = logging.getLogger("scraper.category")


class CategoryScraper:
    """Scrapes product lists from category JSON endpoints."""

    def __init__(self, client: HttpClient):
        self.client = client

    async def fetch_page(
        self,
        category_url: str,
        page: int = 1,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Fetch a single page of products for a category.

        The category URL gets ?format=json&page=N&limit=M appended.
        """
        params = {
            "format": "json",
            "page": page,
            "limit": limit,
        }
        data = await self.client.fetch_json(category_url, params=params)
        return data

    def _get_collection(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return data.get("collection", data)

    def has_more_pages(self, data: Dict[str, Any], page: int) -> bool:
        """Check if there are more pages to scrape."""
        collection = self._get_collection(data)
        pages = collection.get("pages", 0)
        if pages:
            return page < pages
        total = collection.get("count") or data.get("total", 0)
        limit = collection.get("limit") or data.get("limit", 100)
        if total == 0:
            return False
        return page < (total + limit - 1) // limit

    def get_total_pages(self, data: Dict[str, Any]) -> int:
        collection = self._get_collection(data)
        pages = collection.get("pages", 0)
        if pages:
            return pages
        total = collection.get("count") or data.get("total", 0)
        limit = collection.get("limit") or data.get("limit", 100)
        if total == 0:
            return 0
        return (total + limit - 1) // limit

    def get_total_count(self, data: Dict[str, Any]) -> int:
        collection = self._get_collection(data)
        return collection.get("count", 0)

    def extract_products(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract product list from category JSON response.

        Handles:
          - {collection: {products: [{...}]}}
          - {collection: {products: {id: {...}}}}
          - {products: [...]}
          - {items: [...]}
          - {data: [...]}
        """
        collection = self._get_collection(data)
        products = collection.get("products", data.get("products", data.get("items", data.get("data", {}))))

        if isinstance(products, list):
            return products
        if isinstance(products, dict):
            return list(products.values())
        return []
