"""create email_templates table

Revision ID: d4f1a7c9e2b3
Revises: c8e5d0a3f2b1
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

revision = "d4f1a7c9e2b3"
down_revision = "c8e5d0a3f2b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=150), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_email_templates_name"),
    )


def downgrade():
    op.drop_table("email_templates")
