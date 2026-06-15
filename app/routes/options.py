# app/routes/options.py
from datetime import timezone
from zoneinfo import ZoneInfo

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from app import db
from app.models import ApiToken, Option

from . import main


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

    return render_template("options_list.html", options=options, tokens=tokens)


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