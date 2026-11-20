# app/services/roxy_leads.py
"""
New leads from Revive (roxy), for the WhatsApp conversion rate on /wati_stats.

Revive's GET /api/new_leads counts contacts first seen in a range of Bogotá
days: new_leads (had not bought before), returning_customers (already
customers) and all_arrivals (both). We always ask with period=custom and the
exact dates of our own window, so both sides of a rate cover the same days.

The token is a Revive reporting token, stored encrypted in Settings. It goes
in the Authorization header only, never the URL, and never into logs or error
messages.
"""
import logging
import time

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://roxy.saveaplaya.org"
TIMEOUT_SECONDS = 10
# Lead counts move slowly; one call per window per quarter hour is plenty.
CACHE_TTL_SECONDS = 15 * 60

_cache: dict[tuple, tuple[float, dict]] = {}


class RoxyLeadsError(RuntimeError):
    """Revive could not answer; the message is safe to show on the page."""


def clear_cache() -> None:
    _cache.clear()


def fetch_new_leads(since: str, until: str, token: str, base_url: str | None = None,
                    use_cache: bool = True) -> dict:
    """
    {"new_leads", "returning_customers", "all_arrivals", "date_from", "date_to"}
    for the Bogotá days since..until (YYYY-MM-DD, both included).
    use_cache=False always asks Revive (the Settings connection test).
    Raises RoxyLeadsError when the call fails or the answer does not add up.
    """
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    key = (base, since, until, token)
    hit = _cache.get(key)
    if use_cache and hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    try:
        response = requests.get(
            f"{base}/api/new_leads",
            params={"period": "custom", "since": since, "until": until},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        logger.warning("Revive new_leads request failed: %s", type(e).__name__)
        raise RoxyLeadsError("Could not reach Revive for new leads.") from None

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code == 401:
        raise RoxyLeadsError("Revive rejected the API token (401). Check it in Settings.")
    if response.status_code != 200 or body.get("status") != "ok":
        message = body.get("message") if isinstance(body, dict) else None
        logger.warning("Revive new_leads answered %s: %s", response.status_code, message)
        raise RoxyLeadsError(f"Revive answered {response.status_code}" + (f": {message}" if message else "."))

    try:
        result = {
            "new_leads": int(body["new_leads"]),
            "returning_customers": int(body["returning_customers"]),
            "all_arrivals": int(body["all_arrivals"]),
            "date_from": str(body["date_from"]),
            "date_to": str(body["date_to"]),
        }
    except (KeyError, TypeError, ValueError):
        raise RoxyLeadsError("Revive's new_leads answer is missing fields.") from None

    # The windows must match, or a rate would divide different days.
    if (result["date_from"], result["date_to"]) != (since, until):
        raise RoxyLeadsError(
            f"Revive counted {result['date_from']} – {result['date_to']}, not {since} – {until}."
        )
    if result["new_leads"] > result["all_arrivals"]:
        raise RoxyLeadsError("Revive's figures don't add up (new leads above all arrivals).")

    _cache[key] = (time.monotonic(), result)
    return result
