"""Create options table and insert initial data

Revision ID: a819ed7b61f4
Revises: 
Create Date: 2025-01-28 13:29:07.170631

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a819ed7b61f4'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create the 'options' table
    op.create_table(
        'options',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('meta_key', sa.String(length=255), nullable=False),
        sa.Column('meta_value', sa.Text(), nullable=True),
        sa.UniqueConstraint('meta_key')
    )

    # Insert initial data into 'options' table
    op.bulk_insert(
        sa.table('options',
            sa.column('meta_key', sa.String),
            sa.column('meta_value', sa.Text)
        ),
        [
            {
                'meta_key': 'orders_url',
                'meta_value': 'http://saveaplaya.petetesting.com/wp-json/woo-gender-analytics/v1/analytics'
            },
            {
                'meta_key': 'api_key',
                'meta_value': '3gRvrYWvqEMs5MNbG6iE1HUc4M34E7jiw4sxAQmz'
            }
        ]
    )

def downgrade():
    # Drop the 'options' table
    op.drop_table('options')
