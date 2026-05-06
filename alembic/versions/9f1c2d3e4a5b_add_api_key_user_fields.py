"""add api key user fields

Revision ID: 9f1c2d3e4a5b
Revises: 246cb0602808
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f1c2d3e4a5b"
down_revision: Union[str, Sequence[str], None] = "246cb0602808"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("api_keys", sa.Column("name", sa.String(), nullable=True, server_default="Default API Key"))
    op.add_column("api_keys", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("api_keys", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_foreign_key(
        "fk_api_keys_user_id_users",
        "api_keys",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_api_keys_user_id_users", "api_keys", type_="foreignkey")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")

    op.drop_column("api_keys", "last_used_at")
    op.drop_column("api_keys", "is_active")
    op.drop_column("api_keys", "name")
    op.drop_column("api_keys", "user_id")
