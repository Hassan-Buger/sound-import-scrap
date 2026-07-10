import json
import pytest
from scraper.product import ProductScraper


def test_extract_product_data(sample_product_detail_json):
    """Test parsing of full product detail JSON."""
    scraper = ProductScraper.__new__(ProductScraper)
    result = scraper.extract_product_data(sample_product_detail_json)

    assert result["sku"] == "HI-VI-OS-10"
    assert result["ean"] == "6923527272722"
    assert result["title"] == "HiVi OS-10 2-Way Bookshelf Speaker"
    assert result["brand"] == "HiVi"
    assert result["price"] == 299.00
    assert result["regular_price"] == 299.00
    assert result["stock"] == 15
    assert result["stock_status"] == "in_stock"

    assert len(result["images"]) == 3
    cover_images = [i for i in result["images"] if i["is_cover"]]
    assert len(cover_images) == 1
    assert cover_images[0]["image_url"] == "https://www.soundimports.eu/media/image/hivi-os-10.jpg"

    assert len(result["attributes"]) == 4
    attr_names = [a["attribute_name"] for a in result["attributes"]]
    assert "Impedance" in attr_names
    assert "Sensitivity" in attr_names

    raw = json.loads(result["raw_json"])
    assert raw["sku"] == "HI-VI-OS-10"

    assert result["category_ids"] == "home-audio/speakers/bookshelf-speakers"


def test_extract_product_data_minimal():
    """Test parsing of minimal product JSON."""
    scraper = ProductScraper.__new__(ProductScraper)
    minimal = {"sku": "MIN-001"}
    result = scraper.extract_product_data(minimal)

    assert result["sku"] == "MIN-001"
    assert result["title"] is None
    assert result["price"] is None
    assert result["regular_price"] is None
    assert result["images"] == []
    assert result["attributes"] == []


def test_extract_product_data_brand_string():
    """Test when brand is a string instead of dict."""
    scraper = ProductScraper.__new__(ProductScraper)
    data = {"sku": "TEST-001", "brand": "Yamaha"}
    result = scraper.extract_product_data(data)
    assert result["brand"] == "Yamaha"


def test_extract_product_data_no_sku():
    """Test when no SKU is provided, uses product_id."""
    scraper = ProductScraper.__new__(ProductScraper)
    data = {"id": 5000, "title": "No SKU Product"}
    result = scraper.extract_product_data(data)
    assert result["sku"] == "5000"


def test_extract_product_data_images_dedup():
    """Test that duplicate image URLs are deduplicated."""
    scraper = ProductScraper.__new__(ProductScraper)
    data = {
        "sku": "DUP-IMG",
        "cover": "https://example.com/img.jpg",
        "images": [
            {"url": "https://example.com/img.jpg"},
            {"url": "https://example.com/img2.jpg"},
        ],
    }
    result = scraper.extract_product_data(data)
    assert len(result["images"]) == 2
