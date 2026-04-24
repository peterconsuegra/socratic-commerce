# app/routes/options.py
from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from app import db
from app.models import Option

from . import main


@main.route("/settings")
@login_required
def settings():
    return render_template("settings.html")


@main.route("/options")
@login_required
def list_options():
    options = Option.query.all()
    return render_template("options_list.html", options=options)


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