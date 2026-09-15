# app/services/sendgrid.py
"""
SendGrid integration: send an HTML email template to a customer segment.

Contract (https://www.twilio.com/docs/sendgrid/api-reference/mail-send):
    POST https://api.sendgrid.com/v3/mail/send
    Authorization: Bearer <api key>
    {"personalizations": [{"to": [{"email": "maria@x.com", "name": "Maria"}]}],
     "from": {"email": "hola@saveaplaya.com", "name": "Save a Playa"},
     "subject": "...",
     "content": [{"type": "text/plain", "value": "..."},
                 {"type": "text/html", "value": "..."}]}
    -> 202 Accepted with an empty body.

One request per recipient, on purpose. The endpoint takes up to 1000
personalizations per call, but then one bad address fails the whole batch
and the operator cannot tell who was and was not emailed. Per-recipient
calls give a per-row outcome, the same reporting shape as the WATI tagging.

Placeholders ({{name}}, {{last_sku}}, ...) are substituted here, before the
request, with HTML-escaped values, so the editor preview shows exactly what
is sent and customer data can never inject markup into the email.
"""
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.sendgrid.com/v3"
MAIL_SEND_URL = f"{API_BASE}/mail/send"

# Cap on one request, so a mis-click cannot fan out unbounded sends.
MAX_RECIPIENTS_PER_RUN = 500

# SendGrid's mail/send limit is far higher; this keeps one run quick without
# holding many sockets open from a web worker.
CONCURRENCY = 8

REQUEST_TIMEOUT = 20

# Tagged onto every send so the SendGrid stats page can separate these
# win-back emails from anything else the account sends.
CATEGORY = "second_order_reconnect"

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_sendable_email(email) -> bool:
    return bool(email) and bool(_EMAIL.match(str(email).strip()))


# {{name}} style tags. Only identifiers, so nothing that looks like Jinja
# logic ({% ... %}, filters, attribute access) is ever interpreted.
_PLACEHOLDER = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")

# tag -> (description shown in the editor, sample value used by previews).
PLACEHOLDERS = {
    "name": ("Customer first name", "María"),
    "last_name": ("Customer last name", "Gómez"),
    "email": ("Customer email address", "maria@example.com"),
    "last_sku": ("SKU(s) of the customer's last purchase", "pack_favorito"),
    "days_since_last_order": ("Days since the last order", "97"),
    "orders_count": ("Number of orders placed", "2"),
    "total_spent": ("Lifetime spend, formatted as COP", "COP $180.000"),
    "last_order_date": ("Date of the last order (YYYY-MM-DD)", "2026-06-10"),
}

SAMPLE_CONTEXT = {tag: sample for tag, (_desc, sample) in PLACEHOLDERS.items()}


def customer_context(row: dict) -> dict:
    """The placeholder values for one customer row from get_recurrent_customers."""
    from app.helpers import format_cop

    days = row.get("days_since_last_order")
    last_date = (row.get("last_order") or "") or str(row.get("last_order_utc") or "")[:10]
    return {
        "name": (row.get("name") or "").strip(),
        "last_name": (row.get("last_name") or "").strip(),
        "email": (row.get("email") or "").strip(),
        "last_sku": ", ".join(row.get("last_skus") or []),
        "days_since_last_order": "" if days is None else str(days),
        "orders_count": str(row.get("orders_count") or 0),
        "total_spent": format_cop(row.get("total_spent") or 0),
        "last_order_date": last_date,
    }


def render_placeholders(text: str, context: dict, escape: bool = True) -> str:
    """
    Replace {{tag}} with the context value. Unknown tags are left untouched so
    a typo stays visible in the preview instead of silently vanishing.
    """
    def _sub(match):
        tag = match.group(1)
        if tag not in context:
            return match.group(0)
        value = "" if context[tag] is None else str(context[tag])
        return html.escape(value, quote=True) if escape else value

    return _PLACEHOLDER.sub(_sub, text or "")


def unknown_placeholders(*texts: str) -> list[str]:
    """Tags used in the given texts that no customer field can fill."""
    seen = []
    for text in texts:
        for tag in _PLACEHOLDER.findall(text or ""):
            if tag not in PLACEHOLDERS and tag not in seen:
                seen.append(tag)
    return seen


_BLOCK_END = re.compile(r"</(p|div|h[1-6]|li|tr|table|blockquote|section|article|header|footer)\s*>", re.I)
_LINE_BREAK = re.compile(r"<br\s*/?>", re.I)
_DROP_BLOCKS = re.compile(r"<(style|script|head)[^>]*>.*?</\1\s*>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def html_to_text(markup: str) -> str:
    """
    A plain-text twin of the HTML body. Mail clients that hide HTML and spam
    filters both expect one; deriving it keeps the editor to a single field.
    """
    text = _DROP_BLOCKS.sub("", markup or "")
    text = _LINE_BREAK.sub("\n", text)
    text = _BLOCK_END.sub("\n\n", text)
    text = _TAGS.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render_email(subject: str, html_body: str, context: dict) -> dict:
    """Subject, HTML and text for one recipient. The subject is plain text, so
    its values are not HTML-escaped."""
    rendered_html = render_placeholders(html_body, context, escape=True)
    return {
        "subject": render_placeholders(subject, context, escape=False),
        "html": rendered_html,
        "text": html_to_text(rendered_html),
    }


def _api_errors(resp) -> str:
    """SendGrid's error bodies are {"errors": [{"message", "field", "help"}]}."""
    try:
        data = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:200]
    if not isinstance(data, dict):
        return ""
    errors = data.get("errors")
    if not isinstance(errors, list):
        return str(data.get("message") or "")[:200]
    parts = []
    for err in errors:
        if not isinstance(err, dict):
            continue
        message = str(err.get("message") or "").strip()
        field = str(err.get("field") or "").strip()
        parts.append(f"{message} ({field})" if field else message)
    return "; ".join(p for p in parts if p)[:400]


def _describe_failure(status: int, resp) -> str:
    detail = _api_errors(resp)
    if status == 401:
        return "API key rejected (401). It is invalid, revoked, or from another SendGrid account."
    if status == 403:
        base = ("API key accepted but not permitted (403). Give it the Mail Send permission in "
                "SendGrid: Settings → API Keys, or check the sender is verified.")
        return f"{base} SendGrid says: {detail}" if detail else base
    if status == 429:
        return "Rate limited by SendGrid (429). Wait a moment and try again."
    if status >= 500:
        return f"SendGrid server error ({status}). Not a credential problem; try again shortly."
    return f"Unexpected response ({status}). {detail}".strip()


def test_connection(
    *,
    api_key: str,
    from_email: str = "",
    session: requests.Session | None = None,
) -> dict:
    """
    Verify the API key with read-only calls. Nothing is sent.

    GET /v3/scopes lists what the key may do, so a key without mail.send is
    caught here rather than on the first campaign. The sender check is
    best-effort: single-sender verification is one way to authorise a From
    address, domain authentication is the other and does not list addresses,
    so "not found" is reported as a note, never as a failure.
    """
    api_key = (api_key or "").strip()
    from_email = (from_email or "").strip().lower()
    if not api_key:
        return {"ok": False, "message": "SendGrid is not configured: save an API key first."}

    http = session or requests.Session()
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    try:
        resp = http.get(f"{API_BASE}/scopes", headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.Timeout:
        return {"ok": False, "message": f"Timed out after {REQUEST_TIMEOUT}s contacting SendGrid."}
    except requests.ConnectionError as e:
        return {"ok": False, "message": f"Could not reach api.sendgrid.com ({type(e).__name__})."}
    except requests.RequestException as e:
        return {"ok": False, "message": f"Request failed: {e}"}

    if resp.status_code >= 300:
        return {"ok": False, "message": _describe_failure(resp.status_code, resp),
                "status": resp.status_code}

    try:
        scopes = resp.json().get("scopes") or []
    except (ValueError, AttributeError):
        scopes = []

    result = {"ok": True, "message": "API key is valid.", "scope_count": len(scopes)}
    if "mail.send" not in scopes:
        result["ok"] = False
        result["message"] = ("API key is valid but lacks the Mail Send permission, so no email can "
                             "be sent with it. Edit the key in SendGrid: Settings → API Keys.")
        return result

    if from_email:
        result.update(_check_sender(http, headers, from_email))
    return result


def _check_sender(http, headers, from_email: str) -> dict:
    """Best-effort: is the From address a verified single sender? Never fails the test."""
    try:
        resp = http.get(f"{API_BASE}/verified_senders", headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 300:
            return {}
        senders = resp.json().get("results") or []
    except Exception:
        logger.debug("SendGrid verified sender lookup skipped", exc_info=True)
        return {}

    for sender in senders:
        if not isinstance(sender, dict):
            continue
        if str(sender.get("from_email") or "").strip().lower() == from_email:
            return {"sender_verified": bool(sender.get("verified")),
                    "sender_note": (f"{from_email} is a verified sender."
                                    if sender.get("verified")
                                    else f"{from_email} is listed but not yet verified in SendGrid.")}
    return {"sender_verified": None,
            "sender_note": (f"{from_email} is not in the Single Sender list. That is fine if its "
                            "domain is authenticated in SendGrid; otherwise sends will be rejected.")}


def send_template(
    *,
    api_key: str,
    from_email: str,
    from_name: str = "",
    reply_to: str = "",
    asm_group_id: int | None = None,
    subject: str,
    html_body: str,
    customers: list[dict],
    template_name: str = "",
    template_id: int | None = None,
    session: requests.Session | None = None,
) -> dict:
    """
    Send one rendered email per customer. customers are rows from
    get_recurrent_customers (email, name, last_skus, ...).

    Returns counts plus per-customer skipped/failed detail, so the caller can
    report exactly who was and was not emailed. Sending is irreversible; the
    caller is expected to have confirmed with the operator first.
    """
    api_key = (api_key or "").strip()
    from_email = (from_email or "").strip()
    subject = (subject or "").strip()

    if not api_key or not from_email:
        raise ValueError("SendGrid API key and From email must be configured in Settings.")
    if not is_sendable_email(from_email):
        raise ValueError(f"The From email {from_email!r} is not a valid address.")
    if not subject:
        raise ValueError("The template has no subject.")
    if not (html_body or "").strip():
        raise ValueError("The template has no HTML body.")
    if not customers:
        raise ValueError("No customers selected.")
    if len(customers) > MAX_RECIPIENTS_PER_RUN:
        raise ValueError(
            f"{len(customers)} customers selected; the limit is {MAX_RECIPIENTS_PER_RUN} per run."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    http = session or requests.Session()

    sent, skipped, failed = [], [], []

    sendable = []
    for c in customers:
        email = (c.get("email") or "").strip()
        if not is_sendable_email(email):
            skipped.append({"email": email, "reason": "invalid email address"})
            continue
        sendable.append((c, email))

    base_payload = {"from": {"email": from_email}, "categories": [CATEGORY]}
    if from_name:
        base_payload["from"]["name"] = from_name
    if reply_to and is_sendable_email(reply_to):
        base_payload["reply_to"] = {"email": reply_to}
    if asm_group_id:
        base_payload["asm"] = {"group_id": int(asm_group_id)}
    custom_args = {}
    if template_id is not None:
        custom_args["template_id"] = str(template_id)
    if template_name:
        custom_args["template_name"] = template_name[:100]

    def send_one(entry):
        c, email = entry
        rendered = render_email(subject, html_body, customer_context(c))
        to = {"email": email}
        full_name = " ".join(p for p in ((c.get("name") or "").strip(),
                                         (c.get("last_name") or "").strip()) if p)
        if full_name:
            to["name"] = full_name
        payload = {
            **base_payload,
            "personalizations": [{"to": [to]}],
            "subject": rendered["subject"],
            "content": [
                {"type": "text/plain", "value": rendered["text"] or " "},
                {"type": "text/html", "value": rendered["html"]},
            ],
        }
        if custom_args:
            payload["custom_args"] = custom_args
        try:
            resp = http.post(MAIL_SEND_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            # Collected, never raised: one dead address must not abandon the rest.
            logger.warning("SendGrid send error for %s: %s", email, e)
            return ("failed", {"email": email, "status": None, "error": str(e)})

        if resp.status_code >= 300:
            detail = _describe_failure(resp.status_code, resp)
            logger.warning("SendGrid send failed for %s (%s): %s", email, resp.status_code, detail)
            return ("failed", {"email": email, "status": resp.status_code, "error": detail})
        return ("sent", {"email": email, "message_id": resp.headers.get("X-Message-Id", "")})

    if sendable:
        with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(sendable))) as pool:
            for outcome, detail in pool.map(send_one, sendable):
                (sent if outcome == "sent" else failed).append(detail)

    return {
        "template": template_name,
        "subject": subject,
        "from_email": from_email,
        "selected": len(customers),
        "sent": len(sent),
        "skipped": len(skipped),
        "failed": len(failed),
        "skipped_detail": skipped[:50],
        "failed_detail": failed[:50],
    }
