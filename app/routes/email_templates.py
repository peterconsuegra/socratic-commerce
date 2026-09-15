# app/routes/email_templates.py
"""
CRUD for the HTML emails sent through SendGrid, plus a preview and a test send.

Templates are plain rows: a name to pick them by, a subject and an HTML body.
Placeholders ({{name}}, ...) are documented from services.sendgrid.PLACEHOLDERS
so the editor and the sender can never disagree on what is available.
"""
import logging
from urllib.parse import quote

from flask import Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import escape

from app import db
from app.models import EmailAsset, EmailTemplate
from app.services.email_assets import (
    MAX_BYTES as HERO_MAX_BYTES,
    RECOMMENDED_HEIGHT,
    RECOMMENDED_WIDTH,
    validate_upload,
)
from app.services.sendgrid import (
    PLACEHOLDERS,
    SAMPLE_CONTEXT,
    is_sendable_email,
    placeholders_used,
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


# Shown in previews when a template uses {{hero_image_url}} but has no image yet.
_HERO_PLACEHOLDER_URL = "data:image/svg+xml;utf8," + quote(
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{RECOMMENDED_WIDTH}" height="{RECOMMENDED_HEIGHT}" '
    f'viewBox="0 0 {RECOMMENDED_WIDTH} {RECOMMENDED_HEIGHT}">'
    f'<rect width="{RECOMMENDED_WIDTH}" height="{RECOMMENDED_HEIGHT}" fill="#e6f2f1"/>'
    '<text x="600" y="290" font-family="Arial, sans-serif" font-size="44" fill="#3a7b78" '
    'text-anchor="middle">Hero image</text>'
    f'<text x="600" y="345" font-family="Arial, sans-serif" font-size="28" fill="#6b7883" '
    f'text-anchor="middle">Upload one on the template (recommended {RECOMMENDED_WIDTH} x {RECOMMENDED_HEIGHT} px)</text>'
    '</svg>'
)


def hero_url(template, external: bool) -> str | None:
    """
    Public URL of the template's hero image, or None without one.

    external=True builds the absolute URL that goes into sent emails. It is
    forced to https except on localhost, because the app sits behind a proxy
    that terminates TLS and would otherwise report plain http.
    """
    hero = template.hero
    if not hero:
        return None
    kwargs = {"token": hero.token, "filename": hero.filename}
    if not external:
        return url_for("main.email_asset", **kwargs)
    host = request.host.split(":")[0]
    scheme = request.scheme if host in ("localhost", "127.0.0.1") else "https"
    return url_for("main.email_asset", _external=True, _scheme=scheme, **kwargs)


def template_context(template) -> dict:
    """Template-level placeholder values merged into every recipient's context."""
    url = hero_url(template, external=True)
    return {"hero_image_url": url} if url else {}


def hero_missing_error(template) -> str | None:
    """A template that references the hero but has none would send a broken image."""
    if "hero_image_url" in placeholders_used(template.subject, template.html_body) and not template.hero:
        return ("The template uses {{hero_image_url}} but has no hero image. Upload one in the "
                "editor or remove the placeholder from the HTML.")
    return None


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
    hero = template.hero if template else None
    return render_template(
        "email_templates/form.html",
        template=template,
        values=values,
        placeholders=PLACEHOLDERS,
        sendgrid_ready=sendgrid_ready(),
        max_name=MAX_NAME_CHARS,
        max_subject=MAX_SUBJECT_CHARS,
        hero=hero,
        hero_url=hero_url(template, external=False) if template else None,
        hero_recommended=(RECOMMENDED_WIDTH, RECOMMENDED_HEIGHT),
        hero_max_mb=HERO_MAX_BYTES // 1024 // 1024,
        uses_hero="hero_image_url" in placeholders_used(values["subject"], values["html_body"]),
        **extra,
    )


def _unique_copy_name(name: str) -> str:
    """'<name> (copia)', then '(copia 2)', ... within the name length limit."""
    def taken(candidate):
        return EmailTemplate.query.filter(
            db.func.lower(EmailTemplate.name) == candidate.lower()
        ).first() is not None

    n = 1
    while True:
        suffix = " (copia)" if n == 1 else f" (copia {n})"
        candidate = name[:MAX_NAME_CHARS - len(suffix)].rstrip() + suffix
        if not taken(candidate):
            return candidate
        n += 1


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

    # The hero is the template's own image when it has one, otherwise a
    # labelled placeholder so the layout can still be judged.
    context = {**SAMPLE_CONTEXT, "hero_image_url": _HERO_PLACEHOLDER_URL}
    try:
        template_id = int(data.get("template_id") or 0)
    except (TypeError, ValueError):
        template_id = 0
    template = EmailTemplate.query.get(template_id) if template_id else None
    if template and template.hero:
        context["hero_image_url"] = hero_url(template, external=False)

    rendered = render_email(subject, html_body, context)
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
    missing = hero_missing_error(template)
    if missing:
        return jsonify({"status": "error", "message": missing}), 400

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
            extra_context=template_context(template),
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


@main.route("/email-templates/<int:template_id>/clone", methods=["POST"])
@login_required
def email_templates_clone(template_id):
    """Duplicate a template, hero image included, and open the copy for editing."""
    source = EmailTemplate.query.get_or_404(template_id)
    who = getattr(current_user, "username", None)
    clone = EmailTemplate(
        name=_unique_copy_name(source.name),
        subject=source.subject,
        html_body=source.html_body,
        updated_by=who,
    )
    db.session.add(clone)
    for asset in source.assets:
        db.session.add(asset.copy_for(clone))
    db.session.commit()
    logger.info("%s cloned email template %r as %r", who, source.name, clone.name)
    flash(f"Template '{escape(source.name)}' cloned as '{escape(clone.name)}'.", "success")
    return redirect(url_for("main.email_templates_edit", template_id=clone.id))


@main.route("/email-templates/<int:template_id>/hero", methods=["POST"])
@login_required
def email_templates_hero_upload(template_id):
    """Attach (or replace) the hero image. The bytes are stored in the database."""
    template = EmailTemplate.query.get_or_404(template_id)
    upload = request.files.get("hero")
    data = upload.read(HERO_MAX_BYTES + 1) if upload else b""
    info, error = validate_upload(data)
    if error:
        flash(escape(error), "danger")
        return redirect(url_for("main.email_templates_edit", template_id=template.id))

    who = getattr(current_user, "username", None)
    for old in [a for a in template.assets if a.kind == EmailAsset.KIND_HERO]:
        db.session.delete(old)
    db.session.add(EmailAsset(
        template=template,
        kind=EmailAsset.KIND_HERO,
        token=EmailAsset.new_token(),
        filename=f"hero.{info['extension']}",
        content_type=info["content_type"],
        size_bytes=info["size_bytes"],
        width=info["width"] or None,
        height=info["height"] or None,
        data=data,
        uploaded_by=who,
    ))
    template.updated_by = who
    db.session.commit()

    dims = f"{info['width']} × {info['height']} px" if info["width"] else "size unknown"
    note = ("" if info["recommended"]
            else f" Note: the recommended size is {RECOMMENDED_WIDTH} × {RECOMMENDED_HEIGHT} px; "
                 "the template scales it, but a 2:1 image keeps the headline above the fold.")
    logger.info("%s uploaded a hero image for template %r (%s, %d bytes)",
                who, template.name, dims, info["size_bytes"])
    flash(f"Hero image uploaded ({dims}, {info['size_bytes'] // 1024} KB).{note}", "success")
    return redirect(url_for("main.email_templates_edit", template_id=template.id))


@main.route("/email-templates/<int:template_id>/hero/delete", methods=["POST"])
@login_required
def email_templates_hero_delete(template_id):
    template = EmailTemplate.query.get_or_404(template_id)
    removed = False
    for asset in [a for a in template.assets if a.kind == EmailAsset.KIND_HERO]:
        db.session.delete(asset)
        removed = True
    if removed:
        template.updated_by = getattr(current_user, "username", None)
        db.session.commit()
        flash("Hero image removed.", "success")
    return redirect(url_for("main.email_templates_edit", template_id=template.id))


@main.route("/email-assets/<token>/<filename>")
def email_asset(token, filename):
    """
    Serve an uploaded image. Deliberately public: mail clients fetch it
    without a session. The token is random per upload, and the URL is
    immutable, so a year-long cache is safe.
    """
    if len(token) != 2 * EmailAsset.TOKEN_BYTES or not all(c in "0123456789abcdef" for c in token):
        abort(404)
    asset = EmailAsset.query.filter_by(token=token).first_or_404()
    resp = Response(asset.data, mimetype=asset.content_type)
    resp.headers["Content-Length"] = str(asset.size_bytes)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp
