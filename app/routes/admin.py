# app/routes/admin.py
from datetime import timezone
from zoneinfo import ZoneInfo

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import ApiToken, User

from . import main
from .common import admin_required


def _to_bogota(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("America/Bogota"))


@main.route("/admin/users")
@login_required
@admin_required
def admin_users_index():
    users = User.query.order_by(User.id.desc()).all()

    bogota = ZoneInfo("America/Bogota")

    for user in users:
        dt = user.last_login

        if not dt:
            user.last_login_bogota = None
            continue

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        user.last_login_bogota = dt.astimezone(bogota)

    return render_template("admin/users_index.html", users=users)


@main.route("/admin/users/new", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users_new():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        role = (request.form.get("role") or "user").strip().lower()

        if role not in {"admin", "user"}:
            role = "user"

        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("main.admin_users_new"))

        existing = User.query.filter_by(username=username).first()

        if existing:
            flash("That username already exists.", "danger")
            return redirect(url_for("main.admin_users_new"))

        new_user = User(username=username, role=role)
        new_user.set_password(password)

        db.session.add(new_user)
        db.session.commit()

        flash("User created successfully.", "success")
        return redirect(url_for("main.admin_users_index"))

    return render_template("admin/users_new.html")


@main.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users_edit(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        role = (request.form.get("role") or user.role or "user").strip().lower()
        password = request.form.get("password") or ""

        if role not in {"admin", "user"}:
            role = "user"

        if not username:
            flash("Username is required.", "danger")
            return redirect(url_for("main.admin_users_edit", user_id=user_id))

        existing = User.query.filter(
            User.username == username,
            User.id != user.id,
        ).first()

        if existing:
            flash("That username is already taken by another user.", "danger")
            return redirect(url_for("main.admin_users_edit", user_id=user_id))

        if user.role == "admin" and role != "admin":
            admins_count = User.query.filter_by(role="admin").count()

            if admins_count <= 1:
                flash("You cannot remove admin role from the last admin user.", "danger")
                return redirect(url_for("main.admin_users_edit", user_id=user_id))

        user.username = username
        user.role = role

        if password.strip():
            user.set_password(password)

        db.session.commit()

        flash("User updated successfully.", "success")
        return redirect(url_for("main.admin_users_index"))

    return render_template("admin/users_edit.html", user=user)


@main.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_users_delete(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own user while logged in.", "danger")
        return redirect(url_for("main.admin_users_index"))

    if user.role == "admin":
        admins_count = User.query.filter_by(role="admin").count()

        if admins_count <= 1:
            flash("You cannot delete the last admin user.", "danger")
            return redirect(url_for("main.admin_users_index"))

    db.session.delete(user)
    db.session.commit()

    flash("User deleted successfully.", "success")
    return redirect(url_for("main.admin_users_index"))


# ---- API tokens (for ROAS Link / MCP integrations) ---------------------------

@main.route("/admin/api_tokens")
@login_required
@admin_required
def admin_api_tokens_index():
    tokens = ApiToken.query.order_by(ApiToken.id.desc()).all()
    for t in tokens:
        t.created_at_bogota = _to_bogota(t.created_at)
        t.last_used_at_bogota = _to_bogota(t.last_used_at)

    # A freshly created token is passed once via flash session (shown, not stored).
    new_token = request.args.get("new_token")
    return render_template("admin/api_tokens.html", tokens=tokens, new_token=new_token)


@main.route("/admin/api_tokens/new", methods=["POST"])
@login_required
@admin_required
def admin_api_tokens_new():
    name = (request.form.get("name") or "").strip()

    if not name:
        flash("Token name is required.", "danger")
        return redirect(url_for("main.admin_api_tokens_index"))

    token, raw = ApiToken.generate(name)
    db.session.add(token)
    db.session.commit()

    flash(
        "API token created. Copy it now — it will not be shown again:<br>"
        f"<code style='font-size:1rem;'>{raw}</code>",
        "success",
    )
    return redirect(url_for("main.admin_api_tokens_index"))


@main.route("/admin/api_tokens/<int:token_id>/revoke", methods=["POST"])
@login_required
@admin_required
def admin_api_tokens_revoke(token_id):
    token = ApiToken.query.get_or_404(token_id)
    token.revoked = True
    db.session.commit()

    flash(f"Token '{token.name}' revoked.", "success")
    return redirect(url_for("main.admin_api_tokens_index"))


@main.route("/admin/api_tokens/<int:token_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_api_tokens_delete(token_id):
    token = ApiToken.query.get_or_404(token_id)
    db.session.delete(token)
    db.session.commit()

    flash(f"Token '{token.name}' deleted.", "success")
    return redirect(url_for("main.admin_api_tokens_index"))