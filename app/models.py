from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime,
    ForeignKey, BigInteger, Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(500), nullable=False)
    slug = Column(String(500), nullable=False)
    url = Column(String(2000), nullable=False)
    level = Column(Integer, default=0)
    product_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    children = relationship("Category", backref="parent", remote_side=[id], lazy="selectin")

    __table_args__ = (
        Index("idx_categories_slug", "slug"),
    )


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String(100), nullable=True)
    sku = Column(String(200), nullable=False, unique=True)
    ean = Column(String(50), nullable=True)
    title = Column(String(1000), nullable=True)
    short_description = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    long_description = Column(Text, nullable=True)
    regular_price = Column(Float, nullable=True)
    price = Column(Float, nullable=True)
    stock = Column(Integer, nullable=True)
    stock_status = Column(String(50), nullable=True)
    brand = Column(String(500), nullable=True)
    currency = Column(String(10), default="EUR")
    url = Column(String(2000), nullable=True)
    category_ids = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    images = relationship("Image", back_populates="product", cascade="all, delete-orphan", lazy="selectin")
    attributes_rel = relationship("Attribute", back_populates="product", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        Index("idx_products_sku", "sku"),
        Index("idx_products_brand", "brand"),
        Index("idx_products_updated", "updated_at"),
        Index("idx_products_product_id", "product_id"),
    )


class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    image_url = Column(String(2000), nullable=False)
    sort_order = Column(Integer, default=0)
    is_cover = Column(Boolean, default=False)

    product = relationship("Product", back_populates="images")

    __table_args__ = (
        Index("idx_images_product", "product_id"),
    )


class Attribute(Base):
    __tablename__ = "attributes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    attribute_name = Column(String(500), nullable=False)
    attribute_value = Column(Text, nullable=True)
    normalized_name = Column(String(500), nullable=True, index=True)
    sort_order = Column(Integer, default=0)

    product = relationship("Product", back_populates="attributes_rel")

    __table_args__ = (
        Index("idx_attributes_product", "product_id"),
    )


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String(50), nullable=False)
    status = Column(String(50), default="pending")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    total_categories = Column(Integer, default=0)
    completed_categories = Column(Integer, default=0)
    total_products = Column(Integer, default=0)
    new_products = Column(Integer, default=0)
    updated_products = Column(Integer, default=0)
    failed_products = Column(Integer, default=0)
    errors = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ScrapeProgress(Base):
    __tablename__ = "scrape_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("scrape_jobs.id", ondelete="CASCADE"), nullable=False)
    category_url = Column(String(2000), nullable=False)
    page = Column(Integer, default=1)
    completed = Column(Boolean, default=False)
    total_pages = Column(Integer, default=0)
    total_products = Column(Integer, default=0)
    errors = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
