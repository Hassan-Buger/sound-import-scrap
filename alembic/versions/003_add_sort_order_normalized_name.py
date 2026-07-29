"""Add sort_order and normalized_name to attributes

Revision ID: 003
Revises: 002
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("attributes", sa.Column("normalized_name", sa.String(500), nullable=True))
    op.add_column("attributes", sa.Column("sort_order", sa.Integer(), server_default="0"))
    op.create_index("idx_attributes_normalized_name", "attributes", ["normalized_name"])


def downgrade() -> None:
    op.drop_index("idx_attributes_normalized_name", table_name="attributes")
    op.drop_column("attributes", "sort_order")
    op.drop_column("attributes", "normalized_name")
