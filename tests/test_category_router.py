import pytest
from app.models import Category, Product
from app.schemas import _resolve_primary_category, ProductListItem, ProductDetail


def test_resolve_primary_category():
    # Should prioritize leaf child category over top-level generic parents and title case
    cats1 = ["speakers", "home-audio", "bookshelf-speakers"]
    assert _resolve_primary_category(cats1) == "Bookshelf Speakers"

    cats2 = ["speakers", "tower-speakers"]
    assert _resolve_primary_category(cats2) == "Tower Speakers"

    cats3 = ["speakers"]
    assert _resolve_primary_category(cats3) == "Speakers"

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
    assert item.category == "Bookshelf Speakers"
    assert item.categories == ["Speakers", "Home Audio", "Bookshelf Speakers"]
    assert item.category_slugs == ["speakers", "home-audio", "bookshelf-speakers"]

    detail = ProductDetail.model_validate(product)
    assert detail.category == "Bookshelf Speakers"
    assert detail.categories == ["Speakers", "Home Audio", "Bookshelf Speakers"]
    assert detail.category_slugs == ["speakers", "home-audio", "bookshelf-speakers"]
