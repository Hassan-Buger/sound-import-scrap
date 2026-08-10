from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    BigInteger,
    Index,
    Table,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.timeutils import utc_now


product_categories = Table(
    "product_categories",
    Base.metadata,
    Column(
        "product_id",
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "category_id",
        Integer,
        ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("created_at", DateTime, default=utc_now),
    Index("idx_product_categories_category", "category_id"),
    Index("idx_product_categories_product", "product_id"),
)


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(
        Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    name = Column(String(500), nullable=False)
    slug = Column(String(500), nullable=False)
    canonical_path = Column(String(2000), nullable=False)
    url = Column(String(2000), nullable=False)
    level = Column(Integer, default=0)
    product_count = Column(Integer, default=0)
    source_product_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    last_scraped_at = Column(DateTime, nullable=True)
    scrape_status = Column(String(50), default="pending")
    attempt_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    source_checked_at = Column(DateTime, nullable=True)
    missing_streak = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    parent = relationship(
        "Category",
        remote_side=[id],
        back_populates="children",
        lazy="selectin",
    )
    children = relationship(
        "Category",
        back_populates="parent",
        lazy="selectin",
        join_depth=20,
    )
    products = relationship(
        "Product",
        secondary=product_categories,
        back_populates="categories",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_categories_slug", "slug"),
        Index("idx_categories_parent", "parent_id"),
        Index("idx_categories_active", "is_active"),
        Index("uq_categories_canonical_path", "canonical_path", unique=True),
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
    # Legacy comma-separated category reference, retained for backward
    # compatibility only. Relationship data lives in ``product_categories``.
    category_ids = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    images = relationship(
        "Image", back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )
    attributes_rel = relationship(
        "Attribute",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    categories = relationship(
        "Category",
        secondary=product_categories,
        back_populates="products",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_products_sku", "sku"),
        Index("idx_products_brand", "brand"),
        Index("idx_products_updated", "updated_at"),
        Index("idx_products_product_id", "product_id"),
    )


class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    image_url = Column(String(2000), nullable=False)
    sort_order = Column(Integer, default=0)
    is_cover = Column(Boolean, default=False)

    product = relationship("Product", back_populates="images")

    __table_args__ = (Index("idx_images_product", "product_id"),)


class Attribute(Base):
    __tablename__ = "attributes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    attribute_name = Column(String(500), nullable=False)
    attribute_value = Column(Text, nullable=True)
    normalized_name = Column(String(500), nullable=True, index=True)
    sort_order = Column(Integer, default=0)

    product = relationship("Product", back_populates="attributes_rel")

    __table_args__ = (Index("idx_attributes_product", "product_id"),)


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String(50), nullable=False)
    active_key = Column(String(50), nullable=True)
    status = Column(String(50), default="pending")
    # Job-grade status: SUCCESS / PARTIAL_SUCCESS / FAILED
    job_status = Column(String(50), nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    total_categories = Column(Integer, default=0)
    completed_categories = Column(Integer, default=0)
    categories_succeeded = Column(Integer, default=0)
    categories_failed = Column(Integer, default=0)
    categories_skipped = Column(Integer, default=0)
    total_products = Column(Integer, default=0)
    new_products = Column(Integer, default=0)
    updated_products = Column(Integer, default=0)
    failed_products = Column(Integer, default=0)
    relationships_created = Column(Integer, default=0)
    relationships_existing = Column(Integer, default=0)
    category_discrepancies = Column(Integer, default=0)
    summary = Column(Text, nullable=True)
    errors = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    __table_args__ = (
        Index("uq_scrape_jobs_active_key", "active_key", unique=True),
    )


class ScrapeProgress(Base):
    __tablename__ = "scrape_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(
        Integer, ForeignKey("scrape_jobs.id", ondelete="CASCADE"), nullable=False
    )
    category_url = Column(String(2000), nullable=False)
    category_id = Column(
        Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    canonical_path = Column(String(2000), nullable=True)
    # Category lifecycle state:
    #   DISCOVERED -> QUEUED -> RUNNING -> COMPLETED
    #                                          -> RETRYING -> COMPLETED/FAILED
    status = Column(String(50), default="discovered")
    attempt_count = Column(Integer, default=0)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    last_error_class = Column(String(200), nullable=True)
    page = Column(Integer, default=1)
    completed = Column(Boolean, default=False)
    total_pages = Column(Integer, default=0)
    total_products = Column(Integer, default=0)
    products_scraped = Column(Integer, default=0)
    source_count = Column(Integer, default=0)
    pages_processed = Column(Integer, default=0)
    errors = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("idx_scrape_progress_job", "job_id"),
        Index("idx_scrape_progress_cat", "category_id"),
        Index(
            "uq_scrape_progress_job_path",
            "job_id",
            "canonical_path",
            unique=True,
        ),
    )
