# app/routes/options.py
import logging

from datetime import timezone
from zoneinfo import ZoneInfo

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from markupsafe import escape

from app import db
from app.i18n import LANGUAGE_OPTION_KEY, LANGUAGES, site_language, translate as _
from app.models import ApiToken, Option
from app.services.secrets import get_secret, has_secret, mask_secret, set_secret
from app.services.sendgrid import is_sendable_email, test_connection as sendgrid_test_connection
from app.services.wati import _is_dashboard_url, test_connection as wati_test_connection

from . import main
from .common import ORDERS_API_KEY_KEY, ORDERS_URL_KEY

logger = logging.getLogger(__name__)


def _to_bogota(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("America/Bogota"))


@main.route("/settings")
@login_required
def settings():
    return render_template("settings.html")


@main.route("/options")
@login_required
def list_options():
    options = Option.query.all()

    tokens = ApiToken.query.order_by(ApiToken.id.desc()).all()
    for t in tokens:
        t.created_at_bogota = _to_bogota(t.created_at)
        t.last_used_at_bogota = _to_bogota(t.last_used_at)

    wati_token = get_secret(WATI_TOKEN_KEY)

    return render_template(
        "options_list.html",
        options=options,
        secret_keys=SECRET_OPTION_KEYS,
        tokens=tokens,
        orders_api={
            "configured": has_secret(ORDERS_API_KEY_KEY),
            "key_masked": mask_secret(get_secret(ORDERS_API_KEY_KEY)),
            "orders_url": get_option_value_raw(ORDERS_URL_KEY, ""),
        },
        wati={
            "configured": has_secret(WATI_TOKEN_KEY),
            "token_masked": mask_secret(wati_token),
            "tenant_url": get_option_value_raw(WATI_TENANT_URL_KEY, ""),
            "channel_number": get_option_value_raw(WATI_CHANNEL_KEY, ""),
        },
        sendgrid=sendgrid_settings_for_display(),
        roxy={
            "configured": has_secret(ROXY_TOKEN_KEY),
            "token_masked": mask_secret(get_secret(ROXY_TOKEN_KEY)),
            "base_url": get_option_value_raw(ROXY_URL_KEY, ""),
        },
        site_language=site_language(),
    )


@main.route("/options/language", methods=["POST"])
@login_required
def options_language_save():
    """Save the site-wide language: every page and every user reads in it."""
    lang = (request.form.get("language", "") or "").strip().lower()
    if lang not in LANGUAGES:
        flash(_("Pick English or Spanish."), "danger")
        return redirect(url_for("main.list_options"))
    _set_plain_option(LANGUAGE_OPTION_KEY, lang)
    db.session.commit()
    # Confirm in the language just chosen.
    flash(_("Language saved: {language}.", lang).format(language=LANGUAGES[lang]), "success")
    return redirect(url_for("main.list_options"))


WATI_TOKEN_KEY = "wati_api_token"
WATI_TENANT_URL_KEY = "wati_tenant_url"
WATI_CHANNEL_KEY = "wati_channel_number"

# SendGrid: the API key is stored encrypted like the WATI token; the sender
# fields are plain options.
SENDGRID_API_KEY_KEY = "sendgrid_api_key"
SENDGRID_FROM_EMAIL_KEY = "sendgrid_from_email"
SENDGRID_FROM_NAME_KEY = "sendgrid_from_name"
SENDGRID_REPLY_TO_KEY = "sendgrid_reply_to"
SENDGRID_ASM_GROUP_KEY = "sendgrid_unsubscribe_group_id"

# Revive (roxy): the reporting token for /api/new_leads, stored encrypted;
# the base URL is a plain option.
ROXY_TOKEN_KEY = "roxy_api_token"
ROXY_URL_KEY = "roxy_api_url"

# Options holding encrypted secrets. Each is set in its own section of the
# page; the generic options table shows them masked and will not edit them,
# since a value typed there would be stored unencrypted and stop working.
SECRET_OPTION_KEYS = {ORDERS_API_KEY_KEY, WATI_TOKEN_KEY, SENDGRID_API_KEY_KEY, ROXY_TOKEN_KEY}


def get_roxy_config() -> dict:
    """The Revive base URL (default when unset) and the decrypted token, or None."""
    from app.services.roxy_leads import DEFAULT_BASE_URL
    return {
        "base_url": (get_option_value_raw(ROXY_URL_KEY, "") or "").strip() or DEFAULT_BASE_URL,
        "token": get_secret(ROXY_TOKEN_KEY),
    }


@main.route("/options/roxy", methods=["POST"])
@login_required
def options_roxy_save():
    """Save the Revive new-leads API settings. The token is stored encrypted."""
    base_url = (request.form.get("base_url", "") or "").strip()
    if base_url and not base_url.startswith("https://"):
        flash(_("The Revive URL must start with https://."), "danger")
        return redirect(url_for("main.list_options"))
    _set_plain_option(ROXY_URL_KEY, base_url)
    db.session.commit()

    # Empty keeps the stored token, as for WATI; removing it is explicit.
    token = (request.form.get("api_token", "") or "").strip()
    if request.form.get("clear_token"):
        set_secret(ROXY_TOKEN_KEY, "")
        flash(_("Revive API token removed."), "success")
    elif token:
        set_secret(ROXY_TOKEN_KEY, token)
        flash(_("Revive settings saved. The token is stored encrypted."), "success")
    else:
        flash(_("Revive settings saved."), "success")
    return redirect(url_for("main.list_options"))


@main.route("/options/roxy/reveal", methods=["POST"])
@login_required
def options_roxy_reveal():
    """Return the decrypted Revive token so the UI can show it on demand."""
    token = get_secret(ROXY_TOKEN_KEY)
    if not token:
        return jsonify({"status": "error", "message": "No token stored."}), 404
    return jsonify({"status": "success", "token": token}), 200


@main.route("/options/roxy/test", methods=["POST"])
@login_required
def options_roxy_test():
    """Verify the stored Revive token with a read-only call: new leads, last 7 days."""
    from app.services.periods import resolve_windows
    from app.services.roxy_leads import RoxyLeadsError, fetch_new_leads

    cfg = get_roxy_config()
    if not cfg["token"]:
        return jsonify({"ok": False, "message": "No API token stored. Save one first."}), 400

    w = resolve_windows("last_7d")[0].describe()
    try:
        leads = fetch_new_leads(w["start_date"], w["end_date"], cfg["token"], cfg["base_url"], use_cache=False)
    except RoxyLeadsError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    return jsonify({
        "ok": True,
        "message": (f"Connected to {cfg['base_url']}. Last 7 days ({leads['date_from']} – {leads['date_to']}): "
                    f"{leads['new_leads']:,} new leads, {leads['returning_customers']:,} returning customers."),
    }), 200


def get_option_value_raw(meta_key: str, default=""):
    row = Option.query.filter_by(meta_key=meta_key).first()
    return row.meta_value if row and row.meta_value is not None else default


def _set_plain_option(meta_key: str, value: str):
    value = (value or "").strip()
    row = Option.query.filter_by(meta_key=meta_key).first()
    if not value:
        if row:
            db.session.delete(row)
        return
    if row:
        row.meta_value = value
    else:
        db.session.add(Option(meta_key=meta_key, meta_value=value))


@main.route("/options/orders-api", methods=["POST"])
@login_required
def options_orders_api_save():
    """
    Save the store's orders API settings. The key is stored encrypted; an
    empty key field keeps the stored one, so the URL can be edited without
    re-pasting it. There is no remove button: without a key no order syncs.
    """
    orders_url = (request.form.get("orders_url", "") or "").strip()
    if not orders_url.startswith(("https://", "http://")):
        flash(_("The orders API URL must start with https://."), "danger")
        return redirect(url_for("main.list_options"))
    _set_plain_option(ORDERS_URL_KEY, orders_url)
    db.session.commit()

    api_key = (request.form.get("api_key", "") or "").strip()
    if api_key:
        set_secret(ORDERS_API_KEY_KEY, api_key)
        flash(_("Orders API settings saved. The key is stored encrypted."), "success")
    else:
        flash(_("Orders API settings saved."), "success")
    return redirect(url_for("main.list_options"))


@main.route("/options/orders-api/reveal", methods=["POST"])
@login_required
def options_orders_api_reveal():
    """Return the decrypted orders API key so the UI can show it on demand."""
    api_key = get_secret(ORDERS_API_KEY_KEY)
    if not api_key:
        return jsonify({"status": "error", "message": "No API key stored."}), 404
    return jsonify({"status": "success", "api_key": api_key}), 200


@main.route("/options/wati", methods=["POST"])
@login_required
def options_wati_save():
    """Save WATI connection settings. The token is stored encrypted."""
    tenant_url = (request.form.get("tenant_url", "") or "").strip()
    _set_plain_option(WATI_TENANT_URL_KEY, tenant_url)
    _set_plain_option(WATI_CHANNEL_KEY, request.form.get("channel_number", ""))
    db.session.commit()

    # An empty token field means "leave the stored token alone", so the other
    # fields can be edited without re-pasting the secret. Clearing is explicit.
    token = (request.form.get("api_token", "") or "").strip()
    if request.form.get("clear_token"):
        set_secret(WATI_TOKEN_KEY, "")
        flash(_("WATI API token removed."), "success")
    elif token:
        set_secret(WATI_TOKEN_KEY, token)
        flash(_("WATI settings saved. The token is stored encrypted."), "success")
    else:
        flash(_("WATI settings saved."), "success")

    if _is_dashboard_url(tenant_url):
        flash(
            _("Note: that URL is the WATI dashboard, not the API endpoint. Requests "
              "will be sent to live-mt-server.wati.io automatically, but consider "
              "saving the API endpoint from WATI → Connector → API."),
            "success",
        )

    return redirect(url_for("main.list_options"))


@main.route("/options/wati/reveal", methods=["POST"])
@login_required
def options_wati_reveal():
    """Return the decrypted token so the UI can show it on demand."""
    token = get_secret(WATI_TOKEN_KEY)
    if not token:
        return jsonify({"status": "error", "message": "No token stored."}), 404
    return jsonify({"status": "success", "token": token}), 200


@main.route("/options/wati/test", methods=["POST"])
@login_required
def options_wati_test():
    """Verify the stored WATI credentials with a read-only call."""
    tenant_url = get_option_value_raw(WATI_TENANT_URL_KEY, "")
    channel = get_option_value_raw(WATI_CHANNEL_KEY, "")
    token = get_secret(WATI_TOKEN_KEY)

    if not token:
        return jsonify({"ok": False, "message": "No API token stored. Save one first."}), 400

    try:
        result = wati_test_connection(
            tenant_url=tenant_url, api_token=token, channel_number=channel
        )
    except Exception as e:
        logger.exception("WATI connection test failed")
        return jsonify({"ok": False, "message": str(e)}), 500

    return jsonify(result), (200 if result.get("ok") else 400)



# ---- SendGrid ----------------------------------------------------------------

def get_sendgrid_config() -> dict:
    """Everything a send needs, with the key decrypted (None when unset)."""
    asm_raw = (get_option_value_raw(SENDGRID_ASM_GROUP_KEY, "") or "").strip()
    return {
        "api_key": get_secret(SENDGRID_API_KEY_KEY),
        "from_email": (get_option_value_raw(SENDGRID_FROM_EMAIL_KEY, "") or "").strip(),
        "from_name": (get_option_value_raw(SENDGRID_FROM_NAME_KEY, "") or "").strip(),
        "reply_to": (get_option_value_raw(SENDGRID_REPLY_TO_KEY, "") or "").strip(),
        "asm_group_id": int(asm_raw) if asm_raw.isdigit() else None,
    }


def sendgrid_ready() -> bool:
    """A send needs both a key and a From address; anything else is optional."""
    cfg = get_sendgrid_config()
    return bool(cfg["api_key"] and cfg["from_email"])


def sendgrid_settings_for_display() -> dict:
    cfg = get_sendgrid_config()
    return {
        "configured": has_secret(SENDGRID_API_KEY_KEY),
        "ready": bool(cfg["api_key"] and cfg["from_email"]),
        "key_masked": mask_secret(cfg["api_key"]),
        "from_email": cfg["from_email"],
        "from_name": cfg["from_name"],
        "reply_to": cfg["reply_to"],
        "unsubscribe_group_id": get_option_value_raw(SENDGRID_ASM_GROUP_KEY, ""),
    }


@main.route("/options/sendgrid", methods=["POST"])
@login_required
def options_sendgrid_save():
    """Save SendGrid sender settings. The API key is stored encrypted."""
    from_email = (request.form.get("from_email", "") or "").strip()
    reply_to = (request.form.get("reply_to", "") or "").strip()
    group_id = (request.form.get("unsubscribe_group_id", "") or "").strip()

    if from_email and not is_sendable_email(from_email):
        flash(_("'{email}' is not a valid From email address.").format(email=escape(from_email)), "danger")
        return redirect(url_for("main.list_options"))
    if reply_to and not is_sendable_email(reply_to):
        flash(_("'{email}' is not a valid Reply-to address.").format(email=escape(reply_to)), "danger")
        return redirect(url_for("main.list_options"))
    if group_id and not group_id.isdigit():
        flash(_("The unsubscribe group ID must be a number (SendGrid → Marketing → Unsubscribe Groups)."),
              "danger")
        return redirect(url_for("main.list_options"))

    _set_plain_option(SENDGRID_FROM_EMAIL_KEY, from_email)
    _set_plain_option(SENDGRID_FROM_NAME_KEY, request.form.get("from_name", ""))
    _set_plain_option(SENDGRID_REPLY_TO_KEY, reply_to)
    _set_plain_option(SENDGRID_ASM_GROUP_KEY, group_id)
    db.session.commit()

    # As with WATI: an empty key field keeps the stored key, so the sender
    # fields can be edited without re-pasting the secret. Clearing is explicit.
    api_key = (request.form.get("api_key", "") or "").strip()
    if request.form.get("clear_key"):
        set_secret(SENDGRID_API_KEY_KEY, "")
        flash(_("SendGrid API key removed."), "success")
    elif api_key:
        set_secret(SENDGRID_API_KEY_KEY, api_key)
        flash(_("SendGrid settings saved. The API key is stored encrypted."), "success")
    else:
        flash(_("SendGrid settings saved."), "success")

    if not from_email:
        flash(_("Note: no From email is set, so nothing can be sent yet. Use a sender verified in SendGrid."),
              "danger")

    return redirect(url_for("main.list_options"))


@main.route("/options/sendgrid/reveal", methods=["POST"])
@login_required
def options_sendgrid_reveal():
    """Return the decrypted API key so the UI can show it on demand."""
    api_key = get_secret(SENDGRID_API_KEY_KEY)
    if not api_key:
        return jsonify({"status": "error", "message": "No API key stored."}), 404
    return jsonify({"status": "success", "api_key": api_key}), 200


@main.route("/options/sendgrid/test", methods=["POST"])
@login_required
def options_sendgrid_test():
    """Verify the stored SendGrid key with read-only calls. Nothing is sent."""
    cfg = get_sendgrid_config()
    if not cfg["api_key"]:
        return jsonify({"ok": False, "message": "No API key stored. Save one first."}), 400

    try:
        result = sendgrid_test_connection(
            api_key=cfg["api_key"], from_email=cfg["from_email"], asm_group_id=cfg["asm_group_id"]
        )
    except Exception as e:
        logger.exception("SendGrid connection test failed")
        return jsonify({"ok": False, "message": str(e)}), 500

    return jsonify(result), (200 if result.get("ok") else 400)


@main.route("/options/api_tokens/new", methods=["POST"])
@login_required
def options_api_tokens_new():
    name = (request.form.get("token_name") or "").strip()
    if not name:
        flash(_("Token name is required."), "danger")
        return redirect(url_for("main.list_options"))

    token, raw = ApiToken.generate(name)
    db.session.add(token)
    db.session.commit()

    flash(
        _("API token created. Copy it now — it will not be shown again:") + "<br>"
        f"<code style='font-size:1rem; word-break:break-all;'>{raw}</code>",
        "success",
    )
    return redirect(url_for("main.list_options"))


@main.route("/options/api_tokens/<int:token_id>/revoke", methods=["POST"])
@login_required
def options_api_tokens_revoke(token_id):
    token = ApiToken.query.get_or_404(token_id)
    token.revoked = True
    db.session.commit()

    flash(_("Token '{name}' revoked.").format(name=token.name), "success")
    return redirect(url_for("main.list_options"))


@main.route("/options/api_tokens/<int:token_id>/delete", methods=["POST"])
@login_required
def options_api_tokens_delete(token_id):
    token = ApiToken.query.get_or_404(token_id)
    db.session.delete(token)
    db.session.commit()

    flash(_("Token '{name}' deleted.").format(name=token.name), "success")
    return redirect(url_for("main.list_options"))


@main.route("/options/new", methods=["GET", "POST"])
@login_required
def create_option():
    if request.method == "POST":
        meta_key = request.form.get("meta_key")
        meta_value = request.form.get("meta_value")

        if not meta_key or not meta_value:
            flash(_("Both meta key and meta value are required."), "danger")
            return redirect(url_for("main.create_option"))

        if meta_key in SECRET_OPTION_KEYS:
            flash(_("'{key}' is stored encrypted: set it in its section of this page.").format(key=escape(meta_key)), "danger")
            return redirect(url_for("main.list_options"))

        existing_option = Option.query.filter_by(meta_key=meta_key).first()

        if existing_option:
            flash(_("An option with this meta key already exists."), "danger")
            return redirect(url_for("main.create_option"))

        new_option = Option(meta_key=meta_key, meta_value=meta_value)

        try:
            db.session.add(new_option)
            db.session.commit()
            flash(_("Option added successfully!"), "success")

        except Exception as e:
            db.session.rollback()
            flash(_("Error adding option: {error}").format(error=str(e)), "danger")

        return redirect(url_for("main.list_options"))

    return render_template("options_create.html")


@main.route("/options/<int:option_id>/edit", methods=["GET", "POST"])
@login_required
def edit_option(option_id):
    option = Option.query.get_or_404(option_id)

    if option.meta_key in SECRET_OPTION_KEYS:
        flash(_("'{key}' is stored encrypted: change it in its section of this page.").format(key=escape(option.meta_key)), "danger")
        return redirect(url_for("main.list_options"))

    if request.method == "POST":
        meta_value = request.form.get("meta_value")

        if not meta_value:
            flash(_("Meta value cannot be empty."), "danger")
            return redirect(url_for("main.edit_option", option_id=option_id))

        option.meta_value = meta_value

        try:
            db.session.commit()
            flash(_("Option updated successfully!"), "success")

        except Exception as e:
            db.session.rollback()
            flash(_("Error updating option: {error}").format(error=str(e)), "danger")

        return redirect(url_for("main.list_options"))

    return render_template("options_edit.html", option=option)


@main.route("/options/<int:option_id>/delete", methods=["POST"])
@login_required
def delete_option(option_id):
    option = Option.query.get_or_404(option_id)

    try:
        db.session.delete(option)
        db.session.commit()
        flash(_("Option deleted successfully!"), "success")

    except Exception as e:
        db.session.rollback()
        flash(_("Error deleting option: {error}").format(error=str(e)), "danger")

    return redirect(url_for("main.list_options"))