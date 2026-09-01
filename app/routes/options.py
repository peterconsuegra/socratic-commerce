# app/routes/options.py
import logging

from datetime import timezone
from zoneinfo import ZoneInfo

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app import db
from app.models import ApiToken, Option
from app.services.secrets import get_secret, has_secret, mask_secret, set_secret
from app.services.wati import _is_dashboard_url, test_connection as wati_test_connection

from . import main

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
        tokens=tokens,
        wati={
            "configured": has_secret(WATI_TOKEN_KEY),
            "token_masked": mask_secret(wati_token),
            "tenant_url": get_option_value_raw(WATI_TENANT_URL_KEY, ""),
            "channel_number": get_option_value_raw(WATI_CHANNEL_KEY, ""),
        },
    )


WATI_TOKEN_KEY = "wati_api_token"
WATI_TENANT_URL_KEY = "wati_tenant_url"
WATI_CHANNEL_KEY = "wati_channel_number"


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
        flash("WATI API token removed.", "success")
    elif token:
        set_secret(WATI_TOKEN_KEY, token)
        flash("WATI settings saved. The token is stored encrypted.", "success")
    else:
        flash("WATI settings saved.", "success")

    if _is_dashboard_url(tenant_url):
        flash(
            "Note: that URL is the WATI dashboard, not the API endpoint. Requests "
            "will be sent to live-mt-server.wati.io automatically, but consider "
            "saving the API endpoint from WATI → Connector → API.",
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


@main.route("/options/api_tokens/new", methods=["POST"])
@login_required
def options_api_tokens_new():
    name = (request.form.get("token_name") or "").strip()
    if not name:
        flash("Token name is required.", "danger")
        return redirect(url_for("main.list_options"))

    token, raw = ApiToken.generate(name)
    db.session.add(token)
    db.session.commit()

    flash(
        "API token created. Copy it now — it will not be shown again:<br>"
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

    flash(f"Token '{token.name}' revoked.", "success")
    return redirect(url_for("main.list_options"))


@main.route("/options/api_tokens/<int:token_id>/delete", methods=["POST"])
@login_required
def options_api_tokens_delete(token_id):
    token = ApiToken.query.get_or_404(token_id)
    db.session.delete(token)
    db.session.commit()

    flash(f"Token '{token.name}' deleted.", "success")
    return redirect(url_for("main.list_options"))


@main.route("/options/new", methods=["GET", "POST"])
@login_required
def create_option():
    if request.method == "POST":
        meta_key = request.form.get("meta_key")
        meta_value = request.form.get("meta_value")

        if not meta_key or not meta_value:
            flash("Both meta key and meta value are required.", "danger")
            return redirect(url_for("main.create_option"))

        existing_option = Option.query.filter_by(meta_key=meta_key).first()

        if existing_option:
            flash("An option with this meta key already exists.", "danger")
            return redirect(url_for("main.create_option"))

        new_option = Option(meta_key=meta_key, meta_value=meta_value)

        try:
            db.session.add(new_option)
            db.session.commit()
            flash("Option added successfully!", "success")

        except Exception as e:
            db.session.rollback()
            flash(f"Error adding option: {str(e)}", "danger")

        return redirect(url_for("main.list_options"))

    return render_template("options_create.html")


@main.route("/options/<int:option_id>/edit", methods=["GET", "POST"])
@login_required
def edit_option(option_id):
    option = Option.query.get_or_404(option_id)

    if request.method == "POST":
        meta_value = request.form.get("meta_value")

        if not meta_value:
            flash("Meta value cannot be empty.", "danger")
            return redirect(url_for("main.edit_option", option_id=option_id))

        option.meta_value = meta_value

        try:
            db.session.commit()
            flash("Option updated successfully!", "success")

        except Exception as e:
            db.session.rollback()
            flash(f"Error updating option: {str(e)}", "danger")

        return redirect(url_for("main.list_options"))

    return render_template("options_edit.html", option=option)


@main.route("/options/<int:option_id>/delete", methods=["POST"])
@login_required
def delete_option(option_id):
    option = Option.query.get_or_404(option_id)

    try:
        db.session.delete(option)
        db.session.commit()
        flash("Option deleted successfully!", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting option: {str(e)}", "danger")

    return redirect(url_for("main.list_options"))