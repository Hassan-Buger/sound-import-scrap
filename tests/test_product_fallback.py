from scraper.pipeline import ScrapePipeline


def test_listing_fallback_keeps_membership_without_erasing_rich_data():
    data = ScrapePipeline._listing_fallback_product_data(
        {"id": 42, "code": "BROKEN-SKU", "title": "Fallback product"},
        {
            "product_id": "42",
            "sku": "BROKEN-SKU",
            "name": "Fallback product",
            "price": "99.95",
            "url": "https://example.test/product.html",
        },
        "/en/audio-components/woofers/",
    )

    assert data["sku"] == "BROKEN-SKU"
    assert data["price"] == 99.95
    assert data["images"] is None
    assert data["attributes"] is None
    assert data["product_categories"] == [
        {"canonical_path": "/en/audio-components/woofers/"}
    ]
