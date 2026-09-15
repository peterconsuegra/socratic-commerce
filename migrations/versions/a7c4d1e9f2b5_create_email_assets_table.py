"""create email_assets table

Revision ID: a7c4d1e9f2b5
Revises: f6b3c9e5d8a2
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "a7c4d1e9f2b5"
down_revision = "f6b3c9e5d8a2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("email_templates.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="hero"),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=60), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=150), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_email_assets_template_id", "email_assets", ["template_id"])
    op.create_index("ix_email_assets_token", "email_assets", ["token"], unique=True)


def downgrade():
    op.drop_index("ix_email_assets_token", table_name="email_assets")
    op.drop_index("ix_email_assets_template_id", table_name="email_assets")
    op.drop_table("email_assets")
