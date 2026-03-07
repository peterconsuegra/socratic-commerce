"""add role to users

Revision ID: 2f3a9b7c1d11
Revises: 1c5d721f1976
Create Date: 2026-01-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "2f3a9b7c1d11"
down_revision = "1c5d721f1976"
branch_labels = None
depends_on = None


def upgrade():
    # Add column with a server_default so existing rows get a value
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("role", sa.String(length=50), nullable=False, server_default="user")
        )

    # Optional: remove the default after backfilling existing rows
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("role", server_default=None)


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("role")
