from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseSupplierScraper(ABC):
    """Abstract base class for supplier scrapers.

    All supplier-specific scrapers must inherit from this class
    and implement the abstract methods. This ensures a consistent
    interface for the pipeline and the CLI.
    """

    @abstractmethod
    async def discover_categories(self) -> List[Dict[str, Any]]:
        """Fetch the sitemap and return a list of categories with hierarchy.

        Each category dict must have:
            - name: str
            - slug: str
            - url: str
            - level: int
            - parent_slug: Optional[str]
        """
        ...

    @abstractmethod
    async def get_product_list(
        self, category_url: str, page: int = 1, limit: int = 100
    ) -> Dict[str, Any]:
        """Fetch a single page of products for a given category.

        Returns a dict with at minimum:
            - products: list[dict]
            - total: int
            - pages: int
            - current_page: int
        """
        ...

    @abstractmethod
    def extract_product_summary(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Extract a minimal product summary from the category-list JSON.

        Returns a dict with at minimum:
            - product_id: str
            - sku: str
            - url: str
        """
        ...

    @abstractmethod
    async def get_product_detail(self, product_url: str) -> Dict[str, Any]:
        """Fetch full product JSON from the detail endpoint."""
        ...

    @abstractmethod
    def extract_product_detail(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Parse the detail JSON into a normalized dict for DB upsert.

        Must return a dict compatible with crud.upsert_product parameters.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable supplier name."""
        ...

    @property
    @abstractmethod
    def concurrency(self) -> int:
        """Recommended concurrency for this supplier."""
        ...
