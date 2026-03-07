"""Add last_login to users

Revision ID: e2ea1eb5a594
Revises: 2f3a9b7c1d11
Create Date: 2026-01-22 16:13:43.183447

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2ea1eb5a594'
down_revision = '2f3a9b7c1d11'
branch_labels = None
depends_on = None


def upgrade():
    # SQLite safe alter using batch mode
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("last_login", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("last_login")
