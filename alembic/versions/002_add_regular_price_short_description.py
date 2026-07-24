"""Add regular_price and short_description columns

Revision ID: 002
Revises: 001
Create Date: 2026-07-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("regular_price", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("short_description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "short_description")
    op.drop_column("products", "regular_price")
