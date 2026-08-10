import logging
from typing import List, Dict, Any, Optional, Tuple

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

        ``?format=json&page=N&limit=M`` is appended to the category URL.
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

    def get_metadata(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Expose the source's own counter information for a category page."""
        collection = self._get_collection(data)
        return {
            "total": collection.get("count", 0) or data.get("total", 0) or 0,
            "pages": collection.get("pages", 0) or 0,
            "page": collection.get("page", 1) or 1,
            "limit": collection.get("limit", 100) or 100,
            "category_id": collection.get("category_id"),
            "page_next": collection.get("page_next"),
        }

    def get_total_count(self, data: Dict[str, Any]) -> int:
        return self.get_metadata(data)["total"]

    def get_total_pages(self, data: Dict[str, Any]) -> int:
        meta = self.get_metadata(data)
        if meta["pages"]:
            return meta["pages"]
        total = meta["total"] or 0
        limit = meta["limit"] or 100
        if total == 0:
            return 0
        return (total + limit - 1) // limit

    def has_more_pages(self, data: Dict[str, Any], page: int) -> bool:
        pages = self.get_total_pages(data)
        if pages:
            return page < pages
        meta = self.get_metadata(data)
        if meta["total"] == 0:
            return False
        return page < (meta["total"] + meta["limit"] - 1) // meta["limit"]

    def extract_products(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract product list from category JSON response.

        Handles:
          - {collection: {products: {id: {...}}}}
          - {collection: {products: [...]}}
          - {products: [...]}
          - {items: [...]}
          - {data: [...]}
        """
        collection = self._get_collection(data)
        products = collection.get(
            "products",
            data.get("products", data.get("items", data.get("data", {}))),
        )

        if isinstance(products, list):
            return products
        if isinstance(products, dict):
            return list(products.values())
        return []
