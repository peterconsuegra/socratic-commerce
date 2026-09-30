"""create email_subscriptions table

Revision ID: e8b2d6f4a1c7
Revises: d0f7a4b2c5e8
Create Date: 2026-09-30
"""
from alembic import op
import sqlalchemy as sa

revision = "e8b2d6f4a1c7"
down_revision = "d0f7a4b2c5e8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("unsubscribed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unsubscribed_at", sa.DateTime(), nullable=True),
        sa.Column("unsubscribe_source", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_email_subscriptions_email", "email_subscriptions", ["email"], unique=True)
    op.create_index("ix_email_subscriptions_token", "email_subscriptions", ["token"], unique=True)


def downgrade():
    op.drop_index("ix_email_subscriptions_token", table_name="email_subscriptions")
    op.drop_index("ix_email_subscriptions_email", table_name="email_subscriptions")
    op.drop_table("email_subscriptions")
