"""add agent_token_hash to pcs

Revision ID: 7a1b3c9d
Revises: 685ecbcf8095
Create Date: 2026-10-06 15:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "7a1b3c9d"
down_revision = "685ecbcf8095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pcs", sa.Column("agent_token_hash", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("pcs", "agent_token_hash")
