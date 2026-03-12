"""create api_keys and requests tables

Revision ID: 76f41adbf0b1
Revises: 
Create Date: 2026-03-01 18:25:45.347129

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '76f41adbf0b1'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String(255), nullable=False, unique=True),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("api_key_id", sa.Integer, sa.ForeignKey("api_keys.id")),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(255)),
        sa.Column("model", sa.String(255)),
        sa.Column("status_code", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("requests")
    op.drop_table("api_keys")