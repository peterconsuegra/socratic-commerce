"""encrypt the store's orders API key in the options table

Revision ID: b4e9d2a7c1f3
Revises: f3c7a9e1b5d2
Create Date: 2026-10-06

options.api_key, the store's orders API key, was the one credential kept in
plain text, and the Settings page listed it. It is now stored like the WATI
and SendGrid secrets: Fernet ciphertext under the key from
WATI_ENCRYPTION_KEY or FLASK_KEY (app/services/secrets.py), so this must run
with the same environment as the app - which `flask db upgrade` on deploy
does.

A value that already decrypts is left alone, so the upgrade can run twice.
The downgrade stores the plain value again.
"""
from alembic import op
import sqlalchemy as sa

from app.services.secrets import decrypt, encrypt

revision = "b4e9d2a7c1f3"
down_revision = "f3c7a9e1b5d2"
branch_labels = None
depends_on = None

KEY = "api_key"
# Every Fernet token starts like this (version byte 0x80, then the timestamp).
FERNET_PREFIX = "gAAAAA"


def _stored():
    row = op.get_bind().execute(
        sa.text("SELECT meta_value FROM options WHERE meta_key = :key"), {"key": KEY}
    ).fetchone()
    return row.meta_value if row and row.meta_value else None


def _store(value: str):
    op.get_bind().execute(
        sa.text("UPDATE options SET meta_value = :value WHERE meta_key = :key"), {"value": value, "key": KEY}
    )


def upgrade():
    value = _stored()
    # Ciphertext this environment cannot decrypt is left as it is: encrypting
    # it again would not make it usable, only harder to recognise.
    if value and decrypt(value) is None and not value.startswith(FERNET_PREFIX):
        _store(encrypt(value))


def downgrade():
    value = _stored()
    plain = decrypt(value) if value else None
    if plain is not None:
        _store(plain)
