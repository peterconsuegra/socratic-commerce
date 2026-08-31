# app/services/secrets.py
"""
Encrypted storage for third-party credentials (e.g. the WATI API token).

Secrets are kept in the options table as Fernet ciphertext, so a database
dump - or a copy of the SQLite file - does not hand over a usable token. The
encryption key never lives in the database: it comes from the environment.

Key resolution, in order:
  1. WATI_ENCRYPTION_KEY - a urlsafe-base64 32-byte Fernet key, if you want a
     dedicated key you can rotate independently.
  2. Otherwise a key derived from FLASK_KEY via PBKDF2-HMAC-SHA256. This needs
     no new environment variable, but note the consequence: changing FLASK_KEY
     makes every stored secret undecryptable and they must be re-entered.

Decryption failures are reported as None rather than raising, so a rotated key
degrades to "no token configured" instead of breaking the settings page.
"""
import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from app import db
from app.models import Option

logger = logging.getLogger(__name__)

# Fixed, non-secret salt. The secret is FLASK_KEY; the salt only domain-separates
# this derivation from any other use of the same key.
_KDF_SALT = b"save-a-playa/secrets/v1"
_KDF_ITERATIONS = 200_000


def _fernet() -> Fernet:
    explicit = (os.getenv("WATI_ENCRYPTION_KEY") or "").strip()
    if explicit:
        return Fernet(explicit.encode())

    base = (os.getenv("FLASK_KEY") or "default-secret-key").encode()
    derived = hashlib.pbkdf2_hmac("sha256", base, _KDF_SALT, _KDF_ITERATIONS, dklen=32)
    return Fernet(base64.urlsafe_b64encode(derived))


def set_secret(meta_key: str, raw_value: str) -> None:
    """Encrypt and store a secret. An empty value clears it."""
    raw_value = (raw_value or "").strip()

    row = Option.query.filter_by(meta_key=meta_key).first()
    if not raw_value:
        if row:
            db.session.delete(row)
            db.session.commit()
        return

    token = _fernet().encrypt(raw_value.encode()).decode()
    if row:
        row.meta_value = token
    else:
        db.session.add(Option(meta_key=meta_key, meta_value=token))
    db.session.commit()


def get_secret(meta_key: str) -> str | None:
    """Return the decrypted secret, or None if unset or undecryptable."""
    row = Option.query.filter_by(meta_key=meta_key).first()
    if not row or not row.meta_value:
        return None
    try:
        return _fernet().decrypt(row.meta_value.encode()).decode()
    except (InvalidToken, ValueError):
        # Wrong/rotated key, or a value that was stored unencrypted.
        logger.warning("Could not decrypt secret %r; it must be re-entered.", meta_key)
        return None


def has_secret(meta_key: str) -> bool:
    row = Option.query.filter_by(meta_key=meta_key).first()
    return bool(row and row.meta_value)


def mask_secret(value: str | None) -> str:
    """Render a secret for display: last 4 characters only."""
    if not value:
        return ""
    tail = value[-4:] if len(value) > 4 else value
    return f"{'•' * 8}{tail}"
