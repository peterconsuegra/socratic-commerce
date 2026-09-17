"""create customer_contacts table

Revision ID: c9e6f3a1b4d7
Revises: b8d5e2f0a3c6
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

revision = "c9e6f3a1b4d7"
down_revision = "b8d5e2f0a3c6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column("label", sa.String(length=150), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("sent_by", sa.String(length=150), nullable=True),
    )
    op.create_index("ix_customer_contacts_email", "customer_contacts", ["email"])
    op.create_index("ix_customer_contacts_sent_at", "customer_contacts", ["sent_at"])
    op.create_index("ix_customer_contacts_email_sent_at", "customer_contacts", ["email", "sent_at"])


def downgrade():
    op.drop_index("ix_customer_contacts_email_sent_at", table_name="customer_contacts")
    op.drop_index("ix_customer_contacts_sent_at", table_name="customer_contacts")
    op.drop_index("ix_customer_contacts_email", table_name="customer_contacts")
    op.drop_table("customer_contacts")
