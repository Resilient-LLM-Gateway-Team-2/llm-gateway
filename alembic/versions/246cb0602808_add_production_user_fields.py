"""add production user fields

Revision ID: 246cb0602808
Revises: 886689fc1926
Create Date: 2026-05-06 03:12:58.519847
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "246cb0602808"
down_revision: Union[str, Sequence[str], None] = "886689fc1926"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("preferred_model", sa.String(), nullable=True))
    op.add_column("users", sa.Column("profile_image", sa.String(), nullable=True))
    op.add_column("users", sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "updated_at")
    op.drop_column("users", "last_active_at")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "is_active")
    op.drop_column("users", "total_cost_usd")
    op.drop_column("users", "total_tokens")
    op.drop_column("users", "profile_image")
    op.drop_column("users", "preferred_model")
