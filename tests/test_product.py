import json
import pytest
from scraper.product import ProductScraper


def test_extract_product_data(sample_product_detail_json):
    """Test parsing of full product detail JSON."""
    scraper = ProductScraper.__new__(ProductScraper)
    result = scraper.extract_product_data(sample_product_detail_json, category_slug="bookshelf-speakers")

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

    assert len(result["attributes"]) == 6
    attr_names = [a["attribute_name"] for a in result["attributes"]]
    assert "Impedance" in attr_names
    assert "Sensitivity" in attr_names
    assert "Article number" in attr_names
    assert "EAN" in attr_names

    raw = json.loads(result["raw_json"])
    assert raw["sku"] == "HI-VI-OS-10"

    assert result["category_ids"] == "bookshelf-speakers"
    assert {
        row["canonical_path"] for row in result["product_categories"]
    } >= {
        "/en/home-audio/speakers/bookshelf-speakers/",
        "/en/bookshelf-speakers/",
    }


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
    assert len(result["attributes"]) == 1
    assert result["attributes"][0]["attribute_name"] == "Article number"
    assert result["attributes"][0]["attribute_value"] == "MIN-001"


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


def test_extract_product_data_without_stable_identity_fails():
    scraper = ProductScraper.__new__(ProductScraper)
    with pytest.raises(ValueError, match="stable product ID"):
        scraper.extract_product_data({"title": "No stable identity"})


def test_zero_price_and_stock_are_preserved():
    scraper = ProductScraper.__new__(ProductScraper)
    result = scraper.extract_product_data(
        {"sku": "ZERO-1", "price": 0, "stock": {"quantity": 0}}
    )
    assert result["price"] == 0.0
    assert result["stock"] == 0


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


def test_extract_soundimports_stock_level_and_statuses():
    scraper = ProductScraper.__new__(ProductScraper)

    # In stock with numeric level (MZF-8624 case)
    res1 = scraper.extract_product_data({
        "sku": "MZF-8624",
        "stock": {
            "available": True,
            "on_stock": True,
            "track": True,
            "allow_outofstock_sale": False,
            "level": 91,
            "minimum": 1,
            "maximum": 91,
            "delivery": False,
        }
    })
    assert res1["stock"] == 91
    assert res1["stock_status"] == "in_stock"

    # Out of stock with backorder allowed (MZF-8625 case)
    res2 = scraper.extract_product_data({
        "sku": "MZF-8625",
        "stock": {
            "available": True,
            "on_stock": False,
            "track": True,
            "allow_outofstock_sale": True,
            "level": 0,
            "minimum": 1,
            "maximum": 10000,
            "delivery": False,
        }
    })
    assert res2["stock"] == 0
    assert res2["stock_status"] == "on_backorder"

    # In stock with level 10 (MMP006 case)
    res3 = scraper.extract_product_data({
        "sku": "MMP006",
        "stock": {
            "available": True,
            "on_stock": True,
            "track": True,
            "allow_outofstock_sale": True,
            "level": 10,
            "minimum": 1,
            "maximum": 10000,
            "delivery": False,
        }
    })
    assert res3["stock"] == 10
    assert res3["stock_status"] == "in_stock"


def test_extract_short_description_from_content_paragraph():
    scraper = ProductScraper.__new__(ProductScraper)
    data = {
        "sku": "SKU-DESC-1",
        "description": "Truncated meta SEO description that cuts off at the...",
        "content": "<p>Leveraging decades of experience in speaker design, Dayton Audio presents the B65 Bookshelf Speakers.</p><h3>Highlights</h3><ul><li>Great sound</li></ul>",
    }
    result = scraper.extract_product_data(data)
    assert result["short_description"] == "Leveraging decades of experience in speaker design, Dayton Audio presents the B65 Bookshelf Speakers."
    assert "Highlights" in result["long_description"]
    assert "B65 Bookshelf Speakers" not in result["long_description"]


def test_extract_specifications_mzf_8624():
    """Verify specification extraction for MZF-8624 from content and HTML."""
    scraper = ProductScraper.__new__(ProductScraper)
    data = {
        "sku": "MZF-8624",
        "ean": "4007754017557",
        "title": "Monacor MZF-8624 Fixing Clamp For Speaker Grilles",
        "stock": {"level": 91, "on_stock": True, "available": True},
        "content": "<p><strong>Specifications</strong>: Material: Metal • Dimensions: 38 x 20 x 14 mm • Weight: 20 g • Admiss. ambient temp. 0-40 °C • Colour: Black • Suitable for: drill hole size: 7 x 11 mm</p>",
    }
    html_page = """
    <section id="specs" class="w-50">
      <dl>
        <div><dt>Article number<dd>MZF-8624</dd></dt></div>
        <div><dt>EAN<dd>4007754017557</dd></dt></div>
      </dl>
    </section>
    """
    result = scraper.extract_product_data(data, html_doc=html_page)
    attrs = {a["attribute_name"]: a["attribute_value"] for a in result["attributes"]}

    assert attrs.get("Article number") == "MZF-8624"
    assert attrs.get("EAN") == "4007754017557"
    assert attrs.get("Material") == "Metal"
    assert attrs.get("Dimensions") == "38 x 20 x 14 mm"
    assert attrs.get("Weight") == "20 g"
    assert attrs.get("Colour") == "Black"
    assert "7 x 11 mm" in attrs.get("Suitable for", "")
    assert result["stock"] == 91
    assert result["stock_status"] == "in_stock"


def test_extract_specifications_mzf_8625():
    """Verify specification extraction for MZF-8625."""
    scraper = ProductScraper.__new__(ProductScraper)
    data = {
        "sku": "MZF-8625",
        "ean": "4007754017564",
        "title": "Monacor MZF-8625 Fixing Clamp",
        "stock": {"level": 0, "on_stock": False, "available": True, "allow_outofstock_sale": True},
        "content": "<p><strong>Specifications</strong>: Material: Metal • Dimensions: 38 x 30 x 20 mm • Weight: 34 g • Admiss. ambient temp. 0-40 °C • Colour: Black • Suitable for: drill hole size: 7 x 11 mm • Packing unit: 1</p>",
    }
    result = scraper.extract_product_data(data)
    attrs = {a["attribute_name"]: a["attribute_value"] for a in result["attributes"]}

    assert attrs.get("Article number") == "MZF-8625"
    assert attrs.get("EAN") == "4007754017564"
    assert attrs.get("Material") == "Metal"
    assert attrs.get("Dimensions") == "38 x 30 x 20 mm"
    assert attrs.get("Weight") == "34 g"
    assert attrs.get("Colour") == "Black"
    assert attrs.get("Packing unit") == "1"
def test_extract_stock_from_user_dom_model():
    """Verify stock extraction from user-provided SoundImports DOM structure:
    <div class="price"><div class="for"><span class="excl-vat" ...> € 305,<sup>74</sup></span>
    <span class="incl-vat incl-vat-desktop" ...>€ 369,<sup>95</sup></span>
    <span class="hurry"> 8  In stock</span></div></div>
    """
    scraper = ProductScraper.__new__(ProductScraper)
    dom_snippet = """
    <div class="price">
      <div class="for">
        <span class="excl-vat" data-dmws-p_w8fprr-dynamic-price="305.7438" data-dmws-p_w8fprr-dynamic-price-base="305.7438" style="opacity: 1; display: none;"> € 305,<sup>74</sup></span>
        <span class="incl-vat incl-vat-desktop" data-dmws-p_w8fprr-dynamic-price="369.95" data-dmws-p_w8fprr-dynamic-price-base="369.95" style="opacity: 1; display: block;">€ 369,<sup>95</sup></span>
        <span class="hurry"> 8  In stock</span>
      </div>
    </div>
    """
    data = {
        "sku": "DOM-TEST-8",
        "title": "Product with DOM stock",
    }
    result = scraper.extract_product_data(data, html_doc=dom_snippet)
    assert result["stock"] == 8
    assert result["stock_status"] == "in_stock"


def test_extract_specifications_filters_empty_templates():
    """Verify that empty template specification fields with value: '' are excluded."""
    scraper = ProductScraper.__new__(ProductScraper)
    data = {
        "sku": "CLASSIC-B65",
        "ean": "0848791010156",
        "title": "Classic B65 Bookshelf Speakers",
        "specs": {
            "1": {"title": "Power Handling (RMS)", "value": "40 Watts"},
            "2": {"title": "Power Handling (max)", "value": "75 Watt"},
            "3": {"title": "Impedance (Z)", "value": "6 Ω"},
            "4": {"title": "Woofer Series", "value": ""},
            "5": {"title": "Nominal Diameter", "value": "   "},
            "6": {"title": "Voice Coil Diameter", "value": None},
        },
    }
    result = scraper.extract_product_data(data)
    attrs = {a["attribute_name"]: a["attribute_value"] for a in result["attributes"]}

    assert "Power Handling (RMS)" in attrs
    assert attrs["Power Handling (RMS)"] == "40 Watts"
    assert "Power Handling (max)" in attrs
    assert "Impedance (Z)" in attrs
    assert "Article number" in attrs
    assert "EAN" in attrs

    # Empty templates must not be included
    assert "Woofer Series" not in attrs
    assert "Nominal Diameter" not in attrs
    assert "Voice Coil Diameter" not in attrs
    assert len(result["attributes"]) == 5


def test_extract_specifications_from_html_headings_and_tables():
    """Verify specification extraction from heading sections, lists, and tables."""
    scraper = ProductScraper.__new__(ProductScraper)
    data = {
        "sku": "AMP-200",
        "ean": "1234567890123",
        "title": "Stereo Amplifier 200W",
        "content": """
        <h2>Highlights</h2>
        <p>Great amplifier</p>
        <h3>Technical Specifications</h3>
        <ul>
            <li>Rated Power Output: 200W RMS</li>
            <li>Signal-to-Noise Ratio: 105 dB</li>
            <li>THD: < 0.01%</li>
        </ul>
        <h3>Physical Parameters</h3>
        <table>
            <tr><th>Weight</th><td>4.5 kg</td></tr>
            <tr><th>Dimensions</th><td>430 x 100 x 300 mm</td></tr>
        </table>
        """,
    }
    result = scraper.extract_product_data(data)
    attrs = {a["attribute_name"]: a["attribute_value"] for a in result["attributes"]}

    assert attrs.get("Rated Power Output") == "200W RMS"
    assert attrs.get("Signal-to-Noise Ratio") == "105 dB"
    assert attrs.get("THD") == "< 0.01%"
    assert attrs.get("Weight") == "4.5 kg"
    assert attrs.get("Dimensions") == "430 x 100 x 300 mm"
    assert attrs.get("Article number") == "AMP-200"
    assert attrs.get("EAN") == "1234567890123"




