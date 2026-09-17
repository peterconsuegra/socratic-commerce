"""add last_order_at to customer_contacts

Revision ID: d0f7a4b2c5e8
Revises: c9e6f3a1b4d7
Create Date: 2026-09-17

Contacts logged before this column exists keep it NULL and are not counted
as attempts since the customer's last order.
"""
from alembic import op
import sqlalchemy as sa

revision = "d0f7a4b2c5e8"
down_revision = "c9e6f3a1b4d7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("customer_contacts", sa.Column("last_order_at", sa.String(length=32), nullable=True))
    op.create_index("ix_customer_contacts_email_last_order", "customer_contacts", ["email", "last_order_at"])


def downgrade():
    op.drop_index("ix_customer_contacts_email_last_order", table_name="customer_contacts")
    op.drop_column("customer_contacts", "last_order_at")
