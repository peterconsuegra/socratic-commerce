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
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

# Fixed in code on purpose. If operators could type the attribute name too,
# a tenant ends up with "remarketing", "Remarketing" and "remarkting" and no
# segment is ever complete. One fixed name, free-text value.
ATTRIBUTE_NAME = "remarketing"
DEFAULT_ATTRIBUTE = ATTRIBUTE_NAME

MAX_VALUE_CHARS = 60

# Enough to make a full page quick without tripping WATI's rate limits.
CONCURRENCY = 5

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


# Read-only probes for the connection test, tried in order. Tenants differ in
# which API they serve and tokens differ in scope, so a 404 (endpoint absent)
# or 403 (this token lacks that scope) moves on to the next probe.
PROBES = (
    ("/api/ext/v3/contacts?pageSize=1", "v3"),
    ("/api/v1/getContacts?pageSize=1", "v1"),
    ("/api/v1/getMessageTemplates", "v1"),
)
CHANNELS_PATH = "/api/ext/v3/channels"


def _api_message(body: str) -> str:
    """Pull WATI's own error text out of a JSON error body, if present."""
    import json as _json
    try:
        data = _json.loads(body or "")
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("message", "info", "error", "detail", "title"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _describe_failure(status: int, body: str) -> str:
    """Turn an HTTP status into something actionable rather than a bare code."""
    detail = _api_message(body)
    body = (body or "").strip()[:200]
    if status == 401:
        base = "Token rejected (401). The API token is invalid, expired, or belongs to another tenant."
        return f"{base} WATI says: {detail}" if detail else base
    if status == 403:
        base = (
            "Token accepted but not permitted (403) for the endpoints this test reads. "
            "Add a read scope (e.g. contacts:read) to the token in WATI: Connector → API → "
            "your token. Note this may not block tagging, which only writes contacts - "
            "try tagging a single customer to confirm."
        )
        return f"{base} WATI says: {detail}" if detail else base
    if status == 404:
        return ("No known contacts endpoint responded (404) on either the v3 or v1 API. Check the "
                "tenant API URL in WATI - Connector - API; it should look like "
                "https://live-mt-server.wati.io/123456 with no trailing path.")
    if status == 429:
        return "Rate limited by WATI (429). Wait a moment and try again."
    if status >= 500:
        return f"WATI server error ({status}). Not a credential problem; try again shortly."
    return f"Unexpected response ({status}). {body}"


def test_connection(
    *,
    tenant_url: str,
    api_token: str,
    channel_number: str | None = None,
    session: requests.Session | None = None,
) -> dict:
    """
    Verify the stored credentials with a read-only call. Nothing is created,
    updated or sent.

    Returns {"ok": bool, "message": str, ...}. The token is never echoed back
    or logged, only the outcome.
    """
    tenant_url = (tenant_url or "").strip().rstrip("/")
    api_token = (api_token or "").strip()

    if not tenant_url or not api_token:
        return {"ok": False, "message": "WATI is not configured: set the tenant URL and API token first."}
    if not tenant_url.startswith(("http://", "https://")):
        return {"ok": False, "message": f"Tenant URL must start with https:// (got {tenant_url!r})."}

    http = session or requests.Session()
    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}

    last_status, last_body = None, ""
    for path, flavour in PROBES:
        base = _v3_base(tenant_url) if flavour == "v3" else _v1_base(tenant_url)
        url = f"{base}{path}"
        try:
            resp = http.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.Timeout:
            return {"ok": False, "message": f"Timed out after {REQUEST_TIMEOUT}s contacting {tenant_url}."}
        except requests.ConnectionError as e:
            return {"ok": False,
                    "message": f"Could not reach {tenant_url} - check the tenant URL. ({type(e).__name__})"}
        except requests.RequestException as e:
            return {"ok": False, "message": f"Request failed: {e}"}

        if resp.status_code < 300:
            # A 2xx is not proof: the WATI dashboard host returns 200 with the
            # web app for any path, which would otherwise read as success.
            if _looks_like_html(resp):
                return {"ok": False, "message": _DASHBOARD_HINT
                        if _is_dashboard_url(tenant_url)
                        else "The URL returned an HTML page instead of JSON, so it is not the WATI API endpoint."}
            try:
                payload = resp.json()
            except ValueError:
                return {"ok": False,
                        "message": "The URL returned a non-JSON response, so it is not the WATI API endpoint."}
            if not isinstance(payload, dict):
                payload = {}

            result = {
                "ok": True,
                "message": "Credentials are valid.",
                "endpoint": path,
                "api": flavour,
            }

            # Only report a count when it really is one. v1's getContacts
            # returns result:"success" (a string), which must not be shown as
            # if it were a number of contacts.
            raw_count = payload.get("contact_count")
            if isinstance(raw_count, (int, float)):
                result["contact_count"] = int(raw_count)

            # v1 echoes the account's own number, which is useful confirmation
            # that the token belongs to the expected WATI account.
            account_phone = payload.get("login_user_phone")
            if account_phone:
                result["account_phone"] = str(account_phone)

            result.update(_list_channels(http, tenant_url, headers))
            return result

        last_status, last_body = resp.status_code, resp.text
        # 404 = endpoint absent on this tenant, 403 = token lacks THAT scope.
        # Either way another probe may still succeed, so keep going.
        if resp.status_code not in (404, 403):
            break

    return {"ok": False, "message": _describe_failure(last_status or 0, last_body),
            "status": last_status}


def _list_channels(http, tenant_url, headers) -> dict:
    """
    Best-effort extra context: the channels on the account.

    Note this deliberately does NOT try to match the configured sender number.
    GET /api/ext/v3/channels returns only {id, name, channel} - there is no
    phone-number field - so any "number not found" verdict here would be
    meaningless. The sender number is only used when sending messages, which
    this integration does not do.

    Never fails the credential test; on any error it simply adds nothing.
    """
    try:
        resp = http.get(f"{_v3_base(tenant_url)}{CHANNELS_PATH}", headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 300:
            return {}
        payload = resp.json()
        channels = payload.get("channels", payload) if isinstance(payload, dict) else payload
        if not isinstance(channels, list):
            return {}
        names = [str(c.get("name") or c.get("channel") or "").strip()
                 for c in channels if isinstance(c, dict)]
        names = [n for n in names if n]
        return {"channels": names[:10], "channel_count": len(channels)}
    except Exception:
        logger.debug("WATI channel listing skipped", exc_info=True)
        return {}


DASHBOARD_HOSTS = ("live.wati.io", "app.wati.io")

# The v3 "ext" API resolves the tenant from the token, so the numeric tenant id
# from the dashboard URL must NOT appear in the path - with it every v3 endpoint
# 404s. The older v1 API is the opposite and requires it. Both bases are derived
# from the single URL the operator configures.
_TENANT_SUFFIX = re.compile(r"/\d+/?$")

# live.wati.io is the dashboard SPA; the API for the same tenant lives at
# live-mt-server.wati.io with the same tenant id. Verified live: the dashboard
# answers GETs with HTML and writes with an nginx 405, while the API host
# answers 401 without auth. Healing the host here makes a pasted dashboard URL
# work instead of failing a whole run.
_DASHBOARD_HOST = re.compile(r"^(https?)://(?:live|app)\.wati\.io", re.I)


def _normalise_tenant_url(tenant_url: str) -> str:
    url = (tenant_url or "").strip().rstrip("/")
    return _DASHBOARD_HOST.sub(r"\1://live-mt-server.wati.io", url)


def _v3_base(tenant_url: str) -> str:
    return _TENANT_SUFFIX.sub("", _normalise_tenant_url(tenant_url))


def _v1_base(tenant_url: str) -> str:
    return _normalise_tenant_url(tenant_url)

_DASHBOARD_HINT = (
    "That looks like the WATI dashboard URL, not the API endpoint. The dashboard "
    "answers every path with the web app, so requests never reach the API. Copy the "
    "API endpoint from WATI → Connector → API; it usually looks like "
    "https://live-mt-server.wati.io/<tenant id>."
)


def _looks_like_html(resp) -> bool:
    ctype = (resp.headers.get("Content-Type", "") if hasattr(resp, "headers") else "") or ""
    if "html" in ctype.lower():
        return True
    body = (getattr(resp, "text", "") or "")[:200].lstrip().lower()
    return body.startswith("<!doctype html") or body.startswith("<html")


def _is_dashboard_url(tenant_url: str) -> bool:
    return any(h in (tenant_url or "").lower() for h in DASHBOARD_HOSTS)


def _summarise_body(text: str, limit: int = 160) -> str:
    """
    Condense an error body for display. Gateways return full HTML pages (nginx
    405s, Cloudflare blocks); dumping that raw into the UI is unreadable, so
    the title or first text is extracted instead.
    """
    text = (text or "").strip()
    if not text:
        return ""
    if "<html" in text[:200].lower() or "<head" in text[:200].lower():
        m = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
        if m:
            return m.group(1).strip()
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _payload_says_failure(resp) -> str | None:
    """
    WATI v1 reports failures inside an HTTP 200 body as {"result": false}.
    Returns an error string when the body signals failure, else None.
    """
    try:
        data = resp.json()
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("result") is False:
        return (
            data.get("info")
            or data.get("message")
            or data.get("error")
            or "WATI reported result=false"
        )
    return None


def _v3_wrote_nothing(resp, flavour) -> str | None:
    """
    The v3 update returns the contacts it changed in "contact_list" (per the
    documented 200 schema). An empty list is a 200 that changed nothing -
    typically the target did not resolve to a contact - and must not be
    reported as tagged.
    """
    if flavour != "v3":
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or "contact_list" not in data:
        return None
    if not data.get("contact_list"):
        return "no contact matched this number in WATI (nothing was updated)"
    return None


def _put_v3(http, tenant_url, headers, phone, name, params):
    """
    Update attributes via the v3 API.

    Field names are not symmetrical with the read shape: reading returns
    custom_params (snake_case), this write expects customParams (camelCase)
    plus a "target" key that does not appear when reading. Sending the read
    shape here fails in ways that look like a permissions problem.

    It merges - other attributes on the contact are left untouched - so there
    is no need to read-modify-write.
    """
    return http.put(
        f"{_v3_base(tenant_url)}/api/ext/v3/contacts",
        json={"contacts": [{
            "target": _wa_number(phone),
            "customParams": [{"name": str(p["name"]), "value": str(p["value"])} for p in params],
        }]},
        headers=headers, timeout=REQUEST_TIMEOUT,
    )


def _post_v1(http, tenant_url, headers, phone, name, params):
    """v1 addContact: upserts, so it also covers customers not yet in WATI."""
    return http.post(
        f"{_v1_base(tenant_url)}/api/v1/addContact/{_wa_number(phone)}",
        json={"name": name,
              "customParams": [{"name": str(p["name"]), "value": str(p["value"])} for p in params]},
        headers=headers, timeout=REQUEST_TIMEOUT,
    )


def tag_contacts(
    *,
    tenant_url: str,
    api_token: str,
    customers: list[dict],
    label: str,
    attribute: str = DEFAULT_ATTRIBUTE,
    extra_attributes: bool = False,
    session: requests.Session | None = None,
) -> dict:
    """
    Create/update each customer in WATI with <attribute> = <label>.

    customers: dicts with at least "phone". By default exactly ONE custom
        attribute is written - the one being set. Attributes are typed per
        tenant and names that do not exist there can reject the whole write,
        so extras (last SKU, email, ...) are only sent when explicitly asked
        for via extra_attributes=True.

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
    if len(label) > MAX_VALUE_CHARS:
        raise ValueError(f"Label is {len(label)} characters; the limit is {MAX_VALUE_CHARS}.")
    if not customers:
        raise ValueError("No customers selected.")
    if len(customers) > MAX_CONTACTS_PER_RUN:
        raise ValueError(
            f"{len(customers)} customers selected; the limit is {MAX_CONTACTS_PER_RUN} per run."
        )

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    http = session or requests.Session()

    tagged, skipped, failed = [], [], []
    flavour = None  # "v3" or "v1", decided by one probe on the first contact

    def build_params(c, email):
        params = [{"name": attribute, "value": str(label)}]
        if extra_attributes:
            skus = c.get("last_skus") or []
            if skus:
                params.append({"name": "last_purchase_sku", "value": ", ".join(skus)})
            if c.get("days_since_last_order") is not None:
                params.append({"name": "days_since_last_order",
                               "value": str(c["days_since_last_order"])})
            if email:
                params.append({"name": "email", "value": str(email)})
        return params

    # Split out the rows we cannot send before doing any work.
    sendable = []
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
        sendable.append((c, phone, email))

    def send_one(entry, force_flavour=None):
        """One contact per call even though the endpoint takes an array: a bulk
        call returns one outcome for the batch, and an operator needs to know
        *which* rows failed so they can retry those."""
        c, phone, email = entry
        params = build_params(c, email)
        name = (c.get("name") or "").strip() or _wa_number(phone)
        use = force_flavour or flavour
        # On the probe call flavour is still None, which means v3 is attempted.
        effective = "v1" if use == "v1" else "v3"
        attempted = (f"{_v1_base(tenant_url)}/api/v1/addContact/…" if effective == "v1"
                     else f"{_v3_base(tenant_url)}/api/ext/v3/contacts")
        try:
            if use == "v1":
                resp = _post_v1(http, tenant_url, headers, phone, name, params)
            else:
                resp = _put_v3(http, tenant_url, headers, phone, name, params)
                if resp.status_code in (404, 405) and force_flavour is None:
                    return ("retry_v1", {"email": email, "phone": phone})

            if resp.status_code >= 300:
                detail = _api_message(resp.text) or _summarise_body(resp.text)
                if _looks_like_html(resp):
                    detail = f"{detail} — {_DASHBOARD_HINT}" if detail else _DASHBOARD_HINT
                logger.warning("WATI update failed (%s): %s", resp.status_code, detail)
                return ("failed", {"email": email, "phone": phone, "url": attempted,
                                   "status": resp.status_code, "error": detail})

            # A 2xx is not enough. Two documented shapes hide failures:
            #  - v1 returns {"result": false, "info": ...}
            #  - v3 returns {"contact_list": [...]}; an empty list means no
            #    contact matched the target, so nothing was written even
            #    though the call "succeeded".
            body_error = _payload_says_failure(resp)

            if not body_error and _v3_wrote_nothing(resp, effective):
                # The v3 update merges onto existing contacts; it cannot
                # create one. Lapsed customers may not be in WATI at all, so
                # fall through to the v1 create for just this contact.
                attempted = f"{_v1_base(tenant_url)}/api/v1/addContact/…"
                resp = _post_v1(http, tenant_url, headers, phone, name, params)
                if resp.status_code >= 300:
                    detail = _api_message(resp.text) or _summarise_body(resp.text)
                    logger.warning("WATI create failed (%s): %s", resp.status_code, detail)
                    return ("failed", {"email": email, "phone": phone, "url": attempted,
                                       "status": resp.status_code, "error": detail})
                body_error = _payload_says_failure(resp)

            if body_error:
                logger.warning("WATI update rejected for %s: %s", email, body_error)
                return ("failed", {"email": email, "phone": phone, "url": attempted,
                                   "status": resp.status_code, "error": body_error})

            return ("tagged", {"email": email, "phone": phone})

        except requests.RequestException as e:
            # Collected, never raised: one dead number must not abandon the rest.
            logger.warning("WATI update error for %s: %s", email, e)
            return ("failed", {"email": email, "phone": phone, "url": attempted,
                               "status": None, "error": str(e)})

    # Probe with the first contact to learn which API this tenant serves,
    # then run the remainder concurrently using that answer.
    if sendable:
        outcome, detail = send_one(sendable[0])
        if outcome == "retry_v1":
            flavour = "v1"
            outcome, detail = send_one(sendable[0], force_flavour="v1")
        else:
            flavour = flavour or "v3"
        (tagged if outcome == "tagged" else failed).append(detail)

    if len(sendable) > 1:
        with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(sendable) - 1)) as pool:
            for outcome, detail in pool.map(send_one, sendable[1:]):
                if outcome == "retry_v1":
                    outcome, detail = send_one(sendable[0], force_flavour="v1")
                (tagged if outcome == "tagged" else failed).append(detail)

    return {
        "attribute": attribute,
        "label": label,
        "selected": len(customers),
        "tagged": len(tagged),
        "skipped": len(skipped),
        "failed": len(failed),
        "api": flavour,
        # Reported so a failure shows which host was actually contacted, rather
        # than leaving the operator to guess whether the saved URL took effect.
        "tenant_url": tenant_url,
        "v3_base": _v3_base(tenant_url),
        "skipped_detail": skipped[:50],
        "failed_detail": failed[:50],
    }
