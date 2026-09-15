# app/routes/email_templates.py
"""
CRUD for the HTML emails sent through SendGrid, plus a preview and a test send.

Templates are plain rows: a name to pick them by, a subject and an HTML body.
Placeholders ({{name}}, ...) are documented from services.sendgrid.PLACEHOLDERS
so the editor and the sender can never disagree on what is available.
"""
import logging

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import escape

from app import db
from app.models import EmailTemplate
from app.services.sendgrid import (
    PLACEHOLDERS,
    SAMPLE_CONTEXT,
    is_sendable_email,
    render_email,
    send_template,
    unknown_placeholders,
)

from . import main
from .options import _to_bogota, get_sendgrid_config, sendgrid_ready

logger = logging.getLogger(__name__)

MAX_NAME_CHARS = 120
MAX_SUBJECT_CHARS = 255
# Well under SendGrid's 30 MB message cap; large enough for any inlined design.
MAX_HTML_CHARS = 500_000

# A customer row shaped like get_recurrent_customers output, so a test send
# renders the same placeholders a real recipient would see.
_SAMPLE_ROW = {
    "name": SAMPLE_CONTEXT["name"],
    "last_name": SAMPLE_CONTEXT["last_name"],
    "last_skus": [SAMPLE_CONTEXT["last_sku"]],
    "days_since_last_order": int(SAMPLE_CONTEXT["days_since_last_order"]),
    "orders_count": int(SAMPLE_CONTEXT["orders_count"]),
    "total_spent": 180000.0,
    "last_order": SAMPLE_CONTEXT["last_order_date"],
}


def _form_values() -> dict:
    return {
        "name": (request.form.get("name") or "").strip(),
        "subject": (request.form.get("subject") or "").strip(),
        # Leading/trailing whitespace only; the markup itself is kept verbatim.
        "html_body": (request.form.get("html_body") or "").strip(),
    }


def _validate(values: dict, exclude_id: int | None = None) -> list[str]:
    errors = []
    if not values["name"]:
        errors.append("A template name is required.")
    elif len(values["name"]) > MAX_NAME_CHARS:
        errors.append(f"The name is {len(values['name'])} characters; the limit is {MAX_NAME_CHARS}.")
    else:
        clash = EmailTemplate.query.filter(
            db.func.lower(EmailTemplate.name) == values["name"].lower()
        )
        if exclude_id is not None:
            clash = clash.filter(EmailTemplate.id != exclude_id)
        if clash.first():
            errors.append("Another template already uses that name.")

    if not values["subject"]:
        errors.append("A subject is required.")
    elif len(values["subject"]) > MAX_SUBJECT_CHARS:
        errors.append(f"The subject is {len(values['subject'])} characters; the limit is {MAX_SUBJECT_CHARS}.")

    if not values["html_body"]:
        errors.append("The HTML body is required.")
    elif len(values["html_body"]) > MAX_HTML_CHARS:
        errors.append(f"The HTML body is {len(values['html_body']):,} characters; the limit is {MAX_HTML_CHARS:,}.")
    return errors


def _render_form(template, values, **extra):
    return render_template(
        "email_templates/form.html",
        template=template,
        values=values,
        placeholders=PLACEHOLDERS,
        sendgrid_ready=sendgrid_ready(),
        max_name=MAX_NAME_CHARS,
        max_subject=MAX_SUBJECT_CHARS,
        **extra,
    )


@main.route("/email-templates")
@login_required
def email_templates_index():
    templates = EmailTemplate.query.order_by(db.func.lower(EmailTemplate.name)).all()
    for t in templates:
        t.updated_at_bogota = _to_bogota(t.updated_at)
    return render_template(
        "email_templates/index.html",
        templates=templates,
        placeholders=PLACEHOLDERS,
        sendgrid_ready=sendgrid_ready(),
    )


@main.route("/email-templates/new", methods=["GET", "POST"])
@login_required
def email_templates_new():
    values = {"name": "", "subject": "", "html_body": ""}
    if request.method == "POST":
        values = _form_values()
        errors = _validate(values)
        if errors:
            for msg in errors:
                flash(escape(msg), "danger")
            return _render_form(None, values)

        template = EmailTemplate(**values, updated_by=getattr(current_user, "username", None))
        db.session.add(template)
        db.session.commit()
        logger.info("%s created email template %r", template.updated_by, template.name)
        flash(f"Template '{escape(template.name)}' created.", "success")
        return redirect(url_for("main.email_templates_edit", template_id=template.id))

    return _render_form(None, values)


@main.route("/email-templates/<int:template_id>/edit", methods=["GET", "POST"])
@login_required
def email_templates_edit(template_id):
    template = EmailTemplate.query.get_or_404(template_id)
    values = {"name": template.name, "subject": template.subject, "html_body": template.html_body}
    if request.method == "POST":
        values = _form_values()
        errors = _validate(values, exclude_id=template.id)
        if errors:
            for msg in errors:
                flash(escape(msg), "danger")
            return _render_form(template, values)

        template.name = values["name"]
        template.subject = values["subject"]
        template.html_body = values["html_body"]
        template.updated_by = getattr(current_user, "username", None)
        db.session.commit()
        logger.info("%s updated email template %r", template.updated_by, template.name)
        flash(f"Template '{escape(template.name)}' saved.", "success")
        return redirect(url_for("main.email_templates_edit", template_id=template.id))

    return _render_form(template, values)


@main.route("/email-templates/<int:template_id>/delete", methods=["POST"])
@login_required
def email_templates_delete(template_id):
    template = EmailTemplate.query.get_or_404(template_id)
    name = template.name
    db.session.delete(template)
    db.session.commit()
    logger.info("%s deleted email template %r", getattr(current_user, "username", "unknown"), name)
    flash(f"Template '{escape(name)}' deleted.", "success")
    return redirect(url_for("main.email_templates_index"))


@main.route("/email-templates/preview", methods=["POST"])
@login_required
def email_templates_preview():
    """
    Render an unsaved subject/body with sample customer data, for the editor's
    live preview. Uses the exact renderer the sender uses, so what is shown is
    what goes out.
    """
    data = request.get_json(silent=True) or {}
    subject = str(data.get("subject") or "")
    html_body = str(data.get("html_body") or "")
    rendered = render_email(subject, html_body, SAMPLE_CONTEXT)
    return jsonify({
        "status": "success",
        "subject": rendered["subject"],
        "html": rendered["html"],
        "text": rendered["text"],
        "unknown_placeholders": unknown_placeholders(subject, html_body),
    }), 200


@main.route("/email-templates/<int:template_id>/send-test", methods=["POST"])
@login_required
def email_templates_send_test(template_id):
    """Send the saved template to one address, filled with sample data."""
    template = EmailTemplate.query.get_or_404(template_id)
    data = request.get_json(silent=True) or {}
    to = (data.get("to") or "").strip()
    if not is_sendable_email(to):
        return jsonify({"status": "error", "message": "Enter a valid email address to send the test to."}), 400

    cfg = get_sendgrid_config()
    if not cfg["api_key"] or not cfg["from_email"]:
        return jsonify({"status": "error",
                        "message": "SendGrid is not configured. Add the API key and From email in Settings."}), 400

    logger.info("%s is sending a test of email template %r to %s",
                getattr(current_user, "username", "unknown"), template.name, to)
    try:
        result = send_template(
            api_key=cfg["api_key"],
            from_email=cfg["from_email"],
            from_name=cfg["from_name"],
            reply_to=cfg["reply_to"],
            asm_group_id=cfg["asm_group_id"],
            subject=f"[Test] {template.subject}",
            html_body=template.html_body,
            customers=[{**_SAMPLE_ROW, "email": to}],
            template_name=template.name,
            template_id=template.id,
        )
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.exception("SendGrid test send failed")
        return jsonify({"status": "error", "message": str(e)}), 500

    if result["sent"] != 1:
        detail = (result["failed_detail"] or [{}])[0].get("error") or "SendGrid did not accept the message."
        return jsonify({"status": "error", "message": detail, **result}), 502
    return jsonify({"status": "success", "message": f"Test email sent to {to}.", **result}), 200
