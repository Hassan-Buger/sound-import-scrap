import pytest
from app.models import Category, Product
from app.schemas import _resolve_primary_category, ProductListItem, ProductDetail


def test_resolve_primary_category():
    # Should prioritize leaf child category over top-level generic parents
    cats1 = ["speakers", "home-audio", "bookshelf-speakers"]
    assert _resolve_primary_category(cats1) == "bookshelf-speakers"

    cats2 = ["speakers", "tower-speakers"]
    assert _resolve_primary_category(cats2) == "tower-speakers"

    cats3 = ["speakers"]
    assert _resolve_primary_category(cats3) == "speakers"

    assert _resolve_primary_category([]) == "Uncategorized"


def test_product_schema_primary_category():
    product = Product(
        id=50,
        sku="TEST-SPEAKER-01",
        title="Test Bookshelf Speaker",
        price=199.99,
        category_ids="speakers,home-audio,bookshelf-speakers",
    )
    item = ProductListItem.model_validate(product)
    assert item.category == "bookshelf-speakers"
    assert item.categories == ["speakers", "home-audio", "bookshelf-speakers"]

    detail = ProductDetail.model_validate(product)
    assert detail.category == "bookshelf-speakers"
