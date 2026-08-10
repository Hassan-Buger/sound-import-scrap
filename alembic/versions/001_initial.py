"""Initial database schema

Revision ID: 001
Revises:
Create Date: 2026-07-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(500), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("level", sa.Integer(), server_default="0"),
        sa.Column("product_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["parent_id"], ["categories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_categories_slug", "categories", ["slug"])

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.String(100), nullable=True),
        sa.Column("sku", sa.String(200), nullable=False),
        sa.Column("ean", sa.String(50), nullable=True),
        sa.Column("title", sa.String(1000), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("long_description", sa.Text(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("stock_status", sa.String(50), nullable=True),
        sa.Column("brand", sa.String(500), nullable=True),
        sa.Column("currency", sa.String(10), server_default="EUR"),
        sa.Column("url", sa.String(2000), nullable=True),
        sa.Column("category_ids", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku"),
    )
    op.create_index("idx_products_sku", "products", ["sku"])
    op.create_index("idx_products_brand", "products", ["brand"])
    op.create_index("idx_products_updated", "products", ["updated_at"])
    op.create_index("idx_products_product_id", "products", ["product_id"])

    op.create_table(
        "scrape_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("total_categories", sa.Integer(), server_default="0"),
        sa.Column("completed_categories", sa.Integer(), server_default="0"),
        sa.Column("total_products", sa.Integer(), server_default="0"),
        sa.Column("new_products", sa.Integer(), server_default="0"),
        sa.Column("updated_products", sa.Integer(), server_default="0"),
        sa.Column("failed_products", sa.Integer(), server_default="0"),
        sa.Column("errors", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.String(2000), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column("is_cover", sa.Boolean(), server_default="false"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_images_product", "images", ["product_id"])

    op.create_table(
        "attributes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("attribute_name", sa.String(500), nullable=False),
        sa.Column("attribute_value", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_attributes_product", "attributes", ["product_id"])

    op.create_table(
        "scrape_progress",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("category_url", sa.String(2000), nullable=False),
        sa.Column("page", sa.Integer(), server_default="1"),
        sa.Column("completed", sa.Boolean(), server_default="false"),
        sa.Column("total_pages", sa.Integer(), server_default="0"),
        sa.Column("total_products", sa.Integer(), server_default="0"),
        sa.Column("errors", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["job_id"], ["scrape_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("scrape_progress")
    op.drop_table("attributes")
    op.drop_table("images")
    op.drop_table("scrape_jobs")
    op.drop_table("products")
    op.drop_table("categories")
