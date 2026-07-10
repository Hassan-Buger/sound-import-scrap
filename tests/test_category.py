import pytest
from scraper.category import CategoryScraper


def test_extract_products_from_list(sample_category_json):
    """Test extracting product list from category JSON."""
    scraper = CategoryScraper.__new__(CategoryScraper)
    products = scraper.extract_products(sample_category_json)
    assert len(products) == 2
    assert products[0]["sku"] == "PROD-001"
    assert products[1]["sku"] == "PROD-002"


def test_extract_products_empty():
    """Test extraction from empty category JSON."""
    scraper = CategoryScraper.__new__(CategoryScraper)
    assert scraper.extract_products({}) == []
    assert scraper.extract_products({"products": []}) == []


def test_has_more_pages():
    """Test pagination detection."""
    scraper = CategoryScraper.__new__(CategoryScraper)

    assert scraper.has_more_pages({"total": 250, "limit": 100}, page=1) is True
    assert scraper.has_more_pages({"total": 250, "limit": 100}, page=2) is True
    assert scraper.has_more_pages({"total": 250, "limit": 100}, page=3) is False
    assert scraper.has_more_pages({"total": 100, "limit": 100}, page=1) is False
    assert scraper.has_more_pages({"total": 0, "limit": 100}, page=1) is False


def test_get_total_pages():
    """Test total page calculation."""
    scraper = CategoryScraper.__new__(CategoryScraper)

    assert scraper.get_total_pages({"total": 250, "limit": 100}) == 3
    assert scraper.get_total_pages({"total": 100, "limit": 100}) == 1
    assert scraper.get_total_pages({"total": 0}) == 0
    assert scraper.get_total_pages({"total": 1, "limit": 100}) == 1


def test_extract_products_various_formats():
    """Test extraction from various JSON response formats."""
    scraper = CategoryScraper.__new__(CategoryScraper)

    assert scraper.extract_products({"items": [{"sku": "A"}, {"sku": "B"}]}) == [{"sku": "A"}, {"sku": "B"}]
    assert scraper.extract_products({"data": [{"sku": "C"}]}) == [{"sku": "C"}]
    assert scraper.extract_products({"products": {"key": {"sku": "D"}}}) == [{"sku": "D"}]
