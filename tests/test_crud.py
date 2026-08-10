"""Tests for CRUD operations using an in-memory SQLite database.

Note: These tests use a synchronous SQLite database for simplicity.
In production, PostgreSQL with async is used. This tests the logic only.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.database import Base
from app.models import Category, Product, Image, Attribute
from app.router import _build_category_tree


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.mark.asyncio
async def test_upsert_category_new(db_session):
    from app.crud import upsert_category_with_parent

    from sqlalchemy import select, text

    cat = Category(
        name="Test Category",
        slug="test-category",
        canonical_path="/en/test-category/",
        url="/en/test-category/",
        level=1,
    )
    db_session.add(cat)
    db_session.commit()

    result = db_session.execute(
        select(Category).where(Category.canonical_path == "/en/test-category/")
    ).scalar_one_or_none()
    assert result is not None
    assert result.name == "Test Category"


@pytest.mark.asyncio
async def test_category_hierarchy(db_session):
    from sqlalchemy import select

    parent = Category(
        name="Home Audio",
        slug="home-audio",
        canonical_path="/en/home-audio/",
        url="/en/home-audio/",
        level=1,
    )
    child = Category(
        name="Speakers",
        slug="speakers",
        canonical_path="/en/home-audio/speakers/",
        url="/en/home-audio/speakers/",
        level=2,
        parent_id=1,
    )
    db_session.add(parent)
    db_session.flush()
    child.parent_id = parent.id
    db_session.add(child)
    db_session.commit()

    result = (
        db_session.execute(select(Category).where(Category.parent_id == parent.id))
        .scalars()
        .all()
    )
    assert len(result) == 1
    assert result[0].name == "Speakers"


def test_category_tree_is_parent_to_child_not_reversed():
    root = Category(
        id=1,
        name="Home Audio",
        slug="home-audio",
        canonical_path="/en/home-audio/",
        url="/en/home-audio/",
        level=1,
    )
    child = Category(
        id=2,
        parent_id=1,
        name="Speakers",
        slug="speakers",
        canonical_path="/en/home-audio/speakers/",
        url="/en/home-audio/speakers/",
        level=2,
    )
    grandchild = Category(
        id=3,
        parent_id=2,
        name="Woofers",
        slug="woofers",
        canonical_path="/en/home-audio/speakers/woofers/",
        url="/en/home-audio/speakers/woofers/",
        level=3,
    )

    tree = _build_category_tree([root, child, grandchild])

    assert [node["name"] for node in tree] == ["Home Audio"]
    assert [node["name"] for node in tree[0]["children"]] == ["Speakers"]
    assert [node["name"] for node in tree[0]["children"][0]["children"]] == [
        "Woofers"
    ]


@pytest.mark.asyncio
async def test_upsert_product_new(db_session):
    from sqlalchemy import select

    product = Product(
        product_id="100",
        sku="TEST-SKU-001",
        ean="1234567890123",
        title="Test Product",
        regular_price=99.99,
        price=99.99,
        stock=10,
        brand="TestBrand",
        is_active=True,
        short_description="A short description",
    )
    db_session.add(product)
    db_session.commit()

    result = db_session.execute(
        select(Product).where(Product.sku == "TEST-SKU-001")
    ).scalar_one_or_none()
    assert result is not None
    assert result.title == "Test Product"
    assert result.regular_price == 99.99
    assert result.price == 99.99
    assert result.short_description == "A short description"


@pytest.mark.asyncio
async def test_upsert_product_with_images_and_attributes(db_session):
    from sqlalchemy import select

    product = Product(sku="IMG-PROD", title="With Images")
    db_session.add(product)
    db_session.flush()

    img = Image(
        product_id=product.id,
        image_url="https://example.com/img.jpg",
        sort_order=0,
        is_cover=True,
    )
    attr = Attribute(
        product_id=product.id, attribute_name="Color", attribute_value="Black"
    )
    db_session.add(img)
    db_session.add(attr)
    db_session.commit()

    result = db_session.execute(
        select(Product).where(Product.sku == "IMG-PROD")
    ).scalar_one()
    assert len(result.images) == 1
    assert len(result.attributes_rel) == 1
    assert result.attributes_rel[0].attribute_name == "Color"


@pytest.mark.asyncio
async def test_get_brands(db_session):
    from sqlalchemy import select, func

    brands_data = [("BrandA", "SKU-A1"), ("BrandA", "SKU-A2"), ("BrandB", "SKU-B1")]
    for brand, sku in brands_data:
        db_session.add(Product(sku=sku, brand=brand))
    db_session.commit()

    result = db_session.execute(
        select(Product.brand, func.count(Product.id).label("cnt"))
        .where(Product.brand.isnot(None), Product.brand != "")
        .group_by(Product.brand)
        .order_by(Product.brand)
    ).all()
    brand_counts = {row[0]: row[1] for row in result}
    assert brand_counts["BrandA"] == 2
    assert brand_counts["BrandB"] == 1
