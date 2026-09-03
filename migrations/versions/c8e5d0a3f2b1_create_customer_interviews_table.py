"""create customer_interviews table

Revision ID: c8e5d0a3f2b1
Revises: b7d4c9f2a1e0
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa

revision = "c8e5d0a3f2b1"
down_revision = "b7d4c9f2a1e0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_interviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("call_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("experience", sa.String(length=20), nullable=True),
        sa.Column("experience_notes", sa.Text(), nullable=True),
        sa.Column("buy_again", sa.String(length=10), nullable=True),
        sa.Column("buy_again_reason", sa.Text(), nullable=True),
        sa.Column("recommend", sa.String(length=10), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("interviewed_by", sa.String(length=150), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_customer_interviews_phone", "customer_interviews", ["phone"], unique=True
    )


def downgrade():
    op.drop_index("ix_customer_interviews_phone", table_name="customer_interviews")
    op.drop_table("customer_interviews")
