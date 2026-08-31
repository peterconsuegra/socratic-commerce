# app/services/wati.py
"""
WATI integration: tag customer segments with a remarketing attribute.

This module only ever creates or updates CONTACTS. It does not send WhatsApp
messages - sending is a separate, chargeable, irreversible action that needs an
approved template and valid opt-in, and is deliberately not wired up here.

Contract (https://docs.wati.io/reference):
    POST {tenant_url}/api/ext/v3/contacts
    Authorization: Bearer <token>
    {"whatsapp_number": "573001112233",
     "name": "Maria",
     "custom_params": [{"name": "remarketing", "value": "winback_sept"}]}

The same endpoint creates a contact or updates an existing one, so re-running a
segment re-labels those customers rather than duplicating them.
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

DEFAULT_ATTRIBUTE = "remarketing"

# Cap on one request, so a mis-click cannot fan out unbounded writes.
MAX_CONTACTS_PER_RUN = 500

REQUEST_TIMEOUT = 20

# E.164: "+" then 8-15 digits. Phones are normalised at ingest (sanitize_phone),
# but truncated or multi-number values survive as-is and must be skipped.
_E164 = re.compile(r"^\+\d{8,15}$")


def is_sendable_phone(phone: str | None) -> bool:
    return bool(phone) and bool(_E164.match(str(phone).strip()))


def _wa_number(phone: str) -> str:
    """WATI expects the international number without the leading '+'."""
    return str(phone).strip().lstrip("+")


def tag_contacts(
    *,
    tenant_url: str,
    api_token: str,
    customers: list[dict],
    label: str,
    attribute: str = DEFAULT_ATTRIBUTE,
    extra_attributes: bool = True,
    session: requests.Session | None = None,
) -> dict:
    """
    Create/update each customer in WATI with <attribute> = <label>.

    customers: dicts with at least "phone"; "name", "email", "last_skus",
        "days_since_last_order" and "total_spent" are used as extra attributes.

    Returns a summary: counts plus per-customer skipped/failed detail, so the
    caller can report exactly who was and was not tagged.
    """
    tenant_url = (tenant_url or "").strip().rstrip("/")
    label = (label or "").strip()
    attribute = (attribute or DEFAULT_ATTRIBUTE).strip() or DEFAULT_ATTRIBUTE

    if not tenant_url or not api_token:
        raise ValueError("WATI tenant URL and API token must be configured in Settings.")
    if not label:
        raise ValueError("A remarketing label is required.")
    if not customers:
        raise ValueError("No customers selected.")
    if len(customers) > MAX_CONTACTS_PER_RUN:
        raise ValueError(
            f"{len(customers)} customers selected; the limit is {MAX_CONTACTS_PER_RUN} per run."
        )

    url = f"{tenant_url}/api/ext/v3/contacts"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    http = session or requests.Session()

    tagged, skipped, failed = [], [], []

    for c in customers:
        phone = (c.get("phone") or "").strip()
        email = c.get("email") or ""

        if not is_sendable_phone(phone):
            skipped.append({
                "email": email,
                "phone": phone,
                "reason": "no usable phone number" if not phone else "phone is not valid E.164",
            })
            continue

        params = [{"name": attribute, "value": label}]
        if extra_attributes:
            skus = c.get("last_skus") or []
            if skus:
                params.append({"name": "last_purchase_sku", "value": ", ".join(skus)})
            if c.get("days_since_last_order") is not None:
                params.append({"name": "days_since_last_order",
                               "value": str(c["days_since_last_order"])})
            if email:
                params.append({"name": "email", "value": email})

        payload = {
            "whatsapp_number": _wa_number(phone),
            "name": (c.get("name") or "").strip() or _wa_number(phone),
            "custom_params": params,
        }

        try:
            resp = http.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code < 300:
                tagged.append({"email": email, "phone": phone})
            else:
                # Never log the token; log status and a trimmed body only.
                body = (resp.text or "")[:200]
                logger.warning("WATI contact upsert failed (%s): %s", resp.status_code, body)
                failed.append({"email": email, "phone": phone,
                               "status": resp.status_code, "error": body})
        except requests.RequestException as e:
            logger.warning("WATI contact upsert error for %s: %s", email, e)
            failed.append({"email": email, "phone": phone, "status": None, "error": str(e)})

    return {
        "attribute": attribute,
        "label": label,
        "selected": len(customers),
        "tagged": len(tagged),
        "skipped": len(skipped),
        "failed": len(failed),
        "skipped_detail": skipped[:50],
        "failed_detail": failed[:50],
    }
