# app/services/email_subscriptions.py
"""
The marketing-email switch per customer address (models.EmailSubscription).

tokens_for() runs before a send, so every recipient's email can link to
their own unsubscribe page; unsubscribed_emails() is what the email page
hides and the send route skips.
"""
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app import db
from app.models import EmailSubscription
from app.services.sendgrid import is_sendable_email

# Chunked IN clauses, as in routes.lapsed: a 5000-row send would otherwise put
# thousands of parameters in one query, past what older SQLite builds accept.
_CHUNK = 500

_HEX = set("0123456789abcdef")


def _lowered(emails) -> list[str]:
    """Stripped, lowercased and de-duplicated, keeping the first-seen order."""
    return list(dict.fromkeys(e for e in (str(x or "").strip().lower() for x in emails) if e))


def _existing_tokens(emails: list[str]) -> dict[str, str]:
    found = {}
    for i in range(0, len(emails), _CHUNK):
        rows = (db.session.query(EmailSubscription.email, EmailSubscription.token)
                .filter(EmailSubscription.email.in_(emails[i:i + _CHUNK])).all())
        found.update({email: token for email, token in rows})
    return found


def tokens_for(emails) -> dict[str, str]:
    """
    {lowercased email: token} for these addresses, creating the rows that do
    not exist yet (switch off: still subscribed). Invalid addresses are left
    out; the sender skips them anyway.
    """
    wanted = [e for e in _lowered(emails) if is_sendable_email(e)]
    for attempt in (1, 2):
        found = _existing_tokens(wanted)
        missing = [e for e in wanted if e not in found]
        if not missing:
            return found
        rows = [EmailSubscription(email=e, token=EmailSubscription.new_token()) for e in missing]
        db.session.add_all(rows)
        try:
            db.session.commit()
        except IntegrityError:
            # A concurrent send created some of the same rows first: keep
            # theirs and create only what is still missing.
            db.session.rollback()
            if attempt == 2:
                raise
            continue
        found.update({r.email: r.token for r in rows})
        return found


def unsubscribed_emails(emails=None) -> set[str]:
    """Lowercased addresses that unsubscribed; with emails, only among those."""
    query = db.session.query(EmailSubscription.email).filter(EmailSubscription.unsubscribed.is_(True))
    if emails is None:
        return {e for (e,) in query.all()}
    wanted = _lowered(emails)
    found = set()
    for i in range(0, len(wanted), _CHUNK):
        found.update(e for (e,) in query.filter(EmailSubscription.email.in_(wanted[i:i + _CHUNK])).all())
    return found


def subscription_by_token(token) -> EmailSubscription | None:
    """The row a link's token belongs to, or None for anything malformed or unknown."""
    token = str(token or "").strip().lower()
    if len(token) != 2 * EmailSubscription.TOKEN_BYTES or not set(token) <= _HEX:
        return None
    return EmailSubscription.query.filter_by(token=token).first()


def set_unsubscribed(sub: EmailSubscription, unsubscribed: bool, source: str | None = None) -> bool:
    """
    Flip the switch. Returns False when it was already in that position, so
    a second click keeps the original date and source.
    """
    if sub.unsubscribed == unsubscribed:
        return False
    sub.unsubscribed = unsubscribed
    sub.unsubscribed_at = datetime.now(timezone.utc).replace(tzinfo=None) if unsubscribed else None
    sub.unsubscribe_source = source if unsubscribed else None
    db.session.commit()
    return True
