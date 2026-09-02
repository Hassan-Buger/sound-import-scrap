import pytest
from app.models import Category, Product, Attribute
from app.schemas import _resolve_primary_category, ProductListItem, ProductDetail
from app import crud


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
        long_description="<p>Full detailed sound explanation.</p>",
        short_description="Short intro text",
        stock=25,
        stock_status="in_stock",
        category_ids="speakers,home-audio,bookshelf-speakers",
    )
    attr1 = Attribute(
        product_id=50,
        attribute_name="Impedance",
        attribute_value="8 Ohm",
        sort_order=0,
    )
    product.attributes_rel = [attr1]

    item = ProductListItem.model_validate(product)
    assert item.category == "Bookshelf Speakers"
    assert item.categories == ["Speakers", "Home Audio", "Bookshelf Speakers"]
    assert item.category_slugs == ["speakers", "home-audio", "bookshelf-speakers"]
    assert item.long_description == "<p>Full detailed sound explanation.</p>"
    assert item.short_description == "Short intro text"
    assert item.stock == 25
    assert item.stock_status == "in_stock"
    assert len(item.specifications) == 1
    assert item.specifications[0].name == "Impedance"
    assert item.specifications[0].value == "8 Ohm"

    detail = ProductDetail.model_validate(product)
    assert detail.category == "Bookshelf Speakers"
    assert detail.categories == ["Speakers", "Home Audio", "Bookshelf Speakers"]
    assert detail.category_slugs == ["speakers", "home-audio", "bookshelf-speakers"]
    assert detail.long_description == item.long_description
    assert detail.stock == item.stock
    assert detail.stock_status == item.stock_status
    assert len(detail.specifications) == len(item.specifications)


@pytest.mark.asyncio
async def test_category_products_isolation_and_fields(session_factory):
    """Verify category filtering strictly isolates products and loads long_description and specifications."""
    async with session_factory() as db:
        # Create categories
        bookshelf = await crud.upsert_category_with_parent(
            db,
            name="Bookshelf Speakers",
            slug="bookshelf-speakers",
            canonical_path="/en/home-audio/speakers/bookshelf-speakers/",
            url="en/home-audio/speakers/bookshelf-speakers/",
        )
        outdoor = await crud.upsert_category_with_parent(
            db,
            name="Outdoor Speakers",
            slug="outdoor-speakers",
            canonical_path="/en/home-audio/speakers/outdoor-speakers/",
            url="en/home-audio/speakers/outdoor-speakers/",
        )
        await db.commit()

        # Product 1 in Bookshelf
        p1, _, _ = await crud.upsert_product(
            db,
            product_id="P1",
            sku="SKU-BOOKSHELF-1",
            ean="11111111",
            title="Swan D300 Bookshelf Speaker",
            description="Short desc 1",
            short_description="Short desc 1",
            long_description="<h3>Highlights</h3><p>Ribbon tweeter</p>",
            price=399.95,
            regular_price=399.95,
            stock=10,
            stock_status="in_stock",
            brand="Swan",
            currency="EUR",
            url="https://example.com/p1",
            category_ids="bookshelf-speakers",
            raw_json=None,
            images=[],
            attributes=[{"attribute_name": "Power", "attribute_value": "120W", "sort_order": 0}],
            product_categories_paths=["/en/home-audio/speakers/bookshelf-speakers/"],
        )

        # Product 2 in Outdoor
        p2, _, _ = await crud.upsert_product(
            db,
            product_id="P2",
            sku="SKU-OUTDOOR-1",
            ean="22222222",
            title="Rock Outdoor Speaker",
            description="Short desc 2",
            short_description="Short desc 2",
            long_description="<h3>Highlights</h3><p>Weatherproof</p>",
            price=150.00,
            regular_price=150.00,
            stock=0,
            stock_status="out_of_stock",
            brand="Dayton",
            currency="EUR",
            url="https://example.com/p2",
            category_ids="outdoor-speakers",
            raw_json=None,
            images=[],
            attributes=[{"attribute_name": "IP Rating", "attribute_value": "IP65", "sort_order": 0}],
            product_categories_paths=["/en/home-audio/speakers/outdoor-speakers/"],
        )
        await db.commit()

        # Query bookshelf-speakers
        products, total = await crud.get_products_paginated(
            db,
            category_id=bookshelf.id,
            page=1,
            per_page=10,
        )
        assert total == 1
        assert len(products) == 1
        assert products[0].sku == "SKU-BOOKSHELF-1"
        assert products[0].title == "Swan D300 Bookshelf Speaker"

        # Validate serialized fields
        item = ProductListItem.model_validate(products[0])
        assert item.long_description == "<h3>Highlights</h3><p>Ribbon tweeter</p>"
        assert len(item.specifications) == 1
        assert item.specifications[0].name == "Power"
        assert item.specifications[0].value == "120W"
        assert item.stock == 10
        assert item.stock_status == "in_stock"

        # Query outdoor-speakers
        outdoor_prods, outdoor_tot = await crud.get_products_paginated(
            db,
            category_id=outdoor.id,
            page=1,
            per_page=10,
        )
        assert outdoor_tot == 1
        assert len(outdoor_prods) == 1
        assert outdoor_prods[0].sku == "SKU-OUTDOOR-1"
        item2 = ProductListItem.model_validate(outdoor_prods[0])
        assert item2.stock == 0
        assert item2.stock_status == "out_of_stock"


def test_product_schema_omits_empty_specifications():
    """Verify that ProductListItem and ProductDetail omit attributes with empty or whitespace values."""
    product = Product(
        id=60,
        sku="TEST-EMPTY-SPECS",
        title="Test Speaker Empty Specs",
        price=99.99,
        stock=5,
        stock_status="in_stock",
    )
    product.attributes_rel = [
        Attribute(product_id=60, attribute_name="Impedance", attribute_value="8 Ohm", sort_order=0),
        Attribute(product_id=60, attribute_name="EmptySpec1", attribute_value="", sort_order=1),
        Attribute(product_id=60, attribute_name="EmptySpec2", attribute_value="   ", sort_order=2),
        Attribute(product_id=60, attribute_name="Power", attribute_value="50W", sort_order=3),
    ]

    item = ProductListItem.model_validate(product)
    assert len(item.specifications) == 2
    assert item.specifications[0].name == "Impedance"
    assert item.specifications[0].value == "8 Ohm"
    assert item.specifications[0].sort_order == 0
    assert item.specifications[1].name == "Power"
    assert item.specifications[1].value == "50W"
    assert item.specifications[1].sort_order == 1

    detail = ProductDetail.model_validate(product)
    assert len(detail.specifications) == 2
    assert detail.specifications[0].name == "Impedance"
    assert detail.specifications[1].name == "Power"


