"""Add budget and cost tracking tables

Revision ID: 76f41adbf0b2
Revises: 76f41adbf0b1
Create Date: 2026-04-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '76f41adbf0b2'
down_revision: Union[str, Sequence[str], None] = '76f41adbf0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add budget and cost tracking tables."""
    op.create_table(
        "budget_configs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("api_key_id", sa.Integer, sa.ForeignKey("api_keys.id"), nullable=False),
        sa.Column("provider", sa.String(255), nullable=False),
        sa.Column("monthly_budget_usd", sa.Float, nullable=False),
        sa.Column("warning_threshold_percent", sa.Float, default=80.0),
        sa.Column("hard_limit_percent", sa.Float, default=100.0),
        sa.Column("is_enabled", sa.String(255), default="true"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "cost_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("api_key_id", sa.Integer, sa.ForeignKey("api_keys.id"), nullable=False),
        sa.Column("provider", sa.String(255), nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False),
        sa.Column("tokens_used", sa.Integer, default=0),
        sa.Column("month", sa.String(7), nullable=False),  # "YYYY-MM" format
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    """Downgrade schema - drop budget and cost tracking tables."""
    op.drop_table("cost_logs")
    op.drop_table("budget_configs")
