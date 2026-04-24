# app/routes/auth.py
from datetime import datetime, timezone

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.models import User

from . import main


@main.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.monthly_sales"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        if not username or not password:
            flash("Please enter both username and password.", "danger")
            return redirect(url_for("main.login"))

        user = User.query.filter_by(username=username).first()

        if not user or not user.check_password(password):
            flash("Invalid username or password.", "danger")
            return redirect(url_for("main.login"))

        login_user(user)

        user.last_login = datetime.now(timezone.utc)
        db.session.commit()

        flash("Logged in successfully.", "success")
        return redirect(url_for("main.monthly_sales"))

    return render_template("login.html")


@main.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("main.login"))


@main.route("/register", methods=["GET", "POST"])
def register():
    existing_any_user = User.query.first()

    if existing_any_user:
        flash("There is an admin user already created. Please log in.", "warning")
        return redirect(url_for("main.login"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("main.register"))

        new_user = User(username=username)
        new_user.set_password(password)

        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("main.login"))

    return render_template("register.html")