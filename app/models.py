import hashlib
import secrets
from datetime import datetime, timezone

from . import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


def _utcnow():
    return datetime.now(timezone.utc)

class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(50), nullable=False, default="user")
    last_login = db.Column(db.DateTime, nullable=True)



    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"

class Option(db.Model):
    __tablename__ = 'options'

    id = db.Column(db.Integer, primary_key=True)
    meta_key = db.Column(db.String(255), unique=True, nullable=False)
    meta_value = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f"<Option {self.meta_key}: {self.meta_value}>"


class ApiToken(db.Model):
    """
    API access token for external integrations (e.g. ROAS Link / MCP).

    Only the SHA-256 hash of the token is stored. The raw token is shown
    exactly once at creation time and cannot be recovered afterwards.
    """
    __tablename__ = "api_tokens"

    PREFIX = "slp"          # identifies tokens issued by this app
    TOKEN_BYTES = 32        # ~43 url-safe chars of entropy

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    token_prefix = db.Column(db.String(16), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked = db.Column(db.Boolean, nullable=False, default=False)

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def generate(cls, name: str):
        """Create a new token instance and return (instance, raw_token)."""
        raw = f"{cls.PREFIX}_{secrets.token_urlsafe(cls.TOKEN_BYTES)}"
        token = cls(
            name=name,
            token_prefix=raw[:12],
            token_hash=cls._hash(raw),
        )
        return token, raw

    @classmethod
    def verify(cls, raw: str):
        """Return the matching, non-revoked ApiToken for a raw token, or None."""
        if not raw:
            return None
        return cls.query.filter_by(token_hash=cls._hash(raw), revoked=False).first()

    def __repr__(self):
        return f"<ApiToken {self.name} {self.token_prefix}… revoked={self.revoked}>"

class CustomerInterview(db.Model):
    """
    One phone-interview record per customer phone line.

    Keyed by the E.164-normalized phone (digits only, country code included,
    e.g. "573013808828") produced by wati.prepare_target, so every format the
    orders data uses for the same line resolves to one row. email is a
    snapshot for human cross-reference, never a key. Answer values are stored
    as snake_case codes; the Spanish labels live in the UI.
    """
    __tablename__ = "customer_interviews"

    CALL_STATUSES = {
        "pending", "completada", "no_contesto", "numero_equivocado",
        "no_desea_participar", "volver_a_llamar",
    }
    EXPERIENCES = {"muy_buena", "buena", "regular", "mala"}
    YES_NO_MAYBE = {"si", "no", "tal_vez"}

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), nullable=True)

    call_status = db.Column(db.String(20), nullable=False, default="pending")
    experience = db.Column(db.String(20), nullable=True)
    experience_notes = db.Column(db.Text, nullable=True)
    buy_again = db.Column(db.String(10), nullable=True)
    buy_again_reason = db.Column(db.Text, nullable=True)
    recommend = db.Column(db.String(10), nullable=True)
    comments = db.Column(db.Text, nullable=True)

    interviewed_by = db.Column(db.String(150), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    def to_dict(self) -> dict:
        return {
            "phone": self.phone,
            "email": self.email or "",
            "call_status": self.call_status,
            "experience": self.experience or "",
            "experience_notes": self.experience_notes or "",
            "buy_again": self.buy_again or "",
            "buy_again_reason": self.buy_again_reason or "",
            "recommend": self.recommend or "",
            "comments": self.comments or "",
            "interviewed_by": self.interviewed_by or "",
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else "",
        }

    def __repr__(self):
        return f"<CustomerInterview {self.phone} {self.call_status}>"
