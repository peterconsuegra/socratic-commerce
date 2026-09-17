# app/routes/lapsed.py
import csv
import io
import logging
import re
from datetime import datetime

from flask import Response, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.models import CustomerInterview, EmailTemplate
from app.services.recurrent_customers import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    get_recurrent_customers,
)

from app.services.secrets import get_secret
from app.services.sendgrid import MAX_RECIPIENTS_PER_RUN, send_template
from app.services.wati import prepare_target
from app.services.wati import (
    ATTRIBUTE_NAME,
    MAX_CONTACTS_PER_RUN,
    MAX_VALUE_CHARS,
    tag_contacts,
)

from . import main
from .common import get_option_value, refresh_all_orders_if_needed
from .email_templates import hero_missing_error, template_context
from .options import WATI_TENANT_URL_KEY, WATI_TOKEN_KEY, get_sendgrid_config

logger = logging.getLogger(__name__)

PER_PAGE_CHOICES = [50, 100, 250, 500, 1000, 2500, 5000]
# Inactivity window, always expressed in months so the service and the query
# string keep one unit; the multi-year options render as years in the UI.
MONTHS_CHOICES = [2, 3, 6, 9, 12, 24, 36, 48, 60]


def inactivity_label(months: int) -> str:
    """'3 months' for the sub-year options, '2 years' from 24 months up."""
    if months >= 24 and months % 12 == 0:
        return f"{months // 12} years"
    return f"{months} months"

# key -> label and the (min_orders, max_orders) bounds it maps to.
CUSTOMER_TYPES = {
    "all":        {"label": "All customers",        "bounds": (1, None)},
    "first_time": {"label": "First-time customers", "bounds": (1, 1)},
    "repeat":     {"label": "Repeated customers",   "bounds": (2, None)},
}

# Selection is bounded by one page, and pages cap at MAX_PER_PAGE, so a single
# lookup covering that many customers is enough to resolve any selection.
MAX_PER_PAGE_LOOKUP = MAX_PER_PAGE

# The segment this report was built for: customers whose last purchase was one
# of these SKUs and who have not ordered since.
DEFAULT_SKUS = ["una_unidad", "pack_valentin", "pack_favorito"]
DEFAULT_MONTHS = 3

# A win-back list reads best with the most recently lapsed customers first:
# fewest days since the last order at the top.
DEFAULT_SEGMENT_SORT = "days_since"


_GENDER_TO_META = {"female": "F", "male": "M"}


def _clean_city(v) -> str:
    """"BOGOTA (C/MARCA)" -> "Bogota": drop the parenthetical, title-case."""
    v = re.sub(r"\s*\([^)]*\)", " ", str(v or ""))
    v = re.sub(r"\s+", " ", v).strip()
    return v.title()


def _read_segment_filters():
    """Parse the segment filters from the query string. Shared by the list view
    and the CSV export so both always describe the same set of customers."""
    skus = request.args.getlist("sku") or DEFAULT_SKUS

    try:
        months = int(request.args.get("months", DEFAULT_MONTHS))
    except (TypeError, ValueError):
        months = DEFAULT_MONTHS
    if months not in MONTHS_CHOICES:
        months = DEFAULT_MONTHS

    # Customer type collapses the order-count bounds into one choice.
    customer_type = (request.args.get("customer_type", "all") or "all").strip().lower()
    if customer_type not in CUSTOMER_TYPES:
        customer_type = "all"
    min_orders, max_orders = CUSTOMER_TYPES[customer_type]["bounds"]

    return skus, months, customer_type, min_orders, max_orders


def _attach_interviews(rows):
    """
    Attach any saved interview to each page row, in ONE query.

    Rows and interviews meet on the normalized phone (prepare_target), so the
    format the order happened to use never causes a miss. Embedding the data at
    render time means opening the questionnaire modal costs zero requests.
    """
    targets = {}
    for row in rows:
        target, _reason = prepare_target(row.get("phone"))
        row["interview_phone"] = target or ""
        row["interview"] = None
        if target:
            targets.setdefault(target, []).append(row)
    if not targets:
        return
    # Chunked: a 5000-row page would otherwise put thousands of parameters in
    # one IN clause, past what older SQLite builds accept.
    phones = list(targets)
    found = []
    for i in range(0, len(phones), 500):
        found.extend(CustomerInterview.query.filter(
            CustomerInterview.phone.in_(phones[i:i + 500])
        ).all())
    for interview in found:
        for row in targets.get(interview.phone, []):
            row["interview"] = interview.to_dict()


def _segment_page() -> dict:
    """
    Build what every segment page shares: the filtered, paginated customer rows
    and the template context for the filter controls. The page-specific action
    (WATI tag, email send) is layered on by the route.
    """
    skus, months, customer_type, min_orders, max_orders = _read_segment_filters()

    error = None
    result = None

    try:
        refresh_all_orders_if_needed()
        result = get_recurrent_customers(
            orders_csv_path=current_app.config["ALL_ORDERS_CSV"],
            page=request.args.get("page", 1),
            per_page=request.args.get("per_page", DEFAULT_PER_PAGE),
            sort=request.args.get("sort", DEFAULT_SEGMENT_SORT),
            direction=request.args.get("direction"),
            search=request.args.get("q", ""),
            min_orders=min_orders,
            max_orders=max_orders,
            sku_filter=set(skus),
            inactive_months=months,
        )
    except Exception as e:
        logger.exception("Failed to build lapsed customers listing")
        error = str(e)

    if result:
        _attach_interviews(result["rows"])

    return dict(
        error=error,
        result=result,
        per_page_choices=PER_PAGE_CHOICES,
        months_choices=[(m, inactivity_label(m)) for m in MONTHS_CHOICES],
        selected_skus=skus,
        months=months,
        months_label=inactivity_label(months),
        customer_type=customer_type,
        customer_types=CUSTOMER_TYPES,
    )


def _resolve_selected(emails: list[str]) -> tuple[list[dict], list[str]]:
    """
    Resolve a checkbox selection to customer rows, so phone, name and the
    placeholder fields come from the same aggregate the table displayed.

    The emails filter matters: without it this returns the top rows of the
    spend-ranked listing, and any selected customer below that cutoff would
    wrongly resolve as "customer not found". Returns (rows, missing emails).
    """
    refresh_all_orders_if_needed()
    everyone = get_recurrent_customers(
        orders_csv_path=current_app.config["ALL_ORDERS_CSV"],
        min_orders=1,
        emails=emails,
        per_page=MAX_PER_PAGE_LOOKUP,
    )["rows"]
    by_email = {r["email"].lower(): r for r in everyone}
    selected = [by_email[e] for e in emails if e in by_email]
    missing = [e for e in emails if e not in by_email]
    return selected, missing


@main.route("/lapsed-customers")
@login_required
def lapsed_customers():
    """Customers whose last purchase was a given SKU and who have since lapsed."""
    return render_template(
        "lapsed_customers.html",
        page_title="Lapsed Customers",
        page_heading="Lapsed Customers",
        endpoint="main.lapsed_customers",
        wati_attribute=ATTRIBUTE_NAME,
        wati_max_value=MAX_VALUE_CHARS,
        wati_max=MAX_CONTACTS_PER_RUN,
        wati_ready=bool(get_secret("wati_api_token")),
        **_segment_page(),
    )


@main.route("/reconnect-lapsed-customers-by-email")
@login_required
def reconnect_lapsed_customers_by_email():
    """The lapsed segment again, with an email template to send instead of a WATI tag."""
    templates = EmailTemplate.query.order_by(db.func.lower(EmailTemplate.name)).all()
    try:
        selected_template_id = int(request.args.get("template_id") or 0)
    except ValueError:
        selected_template_id = 0

    cfg = get_sendgrid_config()
    return render_template(
        "reconnect_lapsed_customers_by_email.html",
        page_title="Reconnect by Email",
        page_heading="Reconnect Lapsed Customers by Email",
        endpoint="main.reconnect_lapsed_customers_by_email",
        email_templates=templates,
        selected_template_id=selected_template_id,
        sendgrid_ready=bool(cfg["api_key"] and cfg["from_email"]),
        sendgrid_from=cfg["from_email"],
        sendgrid_from_name=cfg["from_name"],
        email_max=MAX_RECIPIENTS_PER_RUN,
        **_segment_page(),
    )


@main.route("/lapsed-customers/export.csv")
@login_required
def lapsed_customers_export():
    """
    The current segment - every matching row, not just the visible page - as a
    CSV shaped for Meta Ads custom audience uploads.

    Columns match the user's working audience file exactly:
        fn,ln,email,phone,country,ct,gen,value
    country is fixed to CO. ln is the billing last name where the API has sent
    one (orders fetched before the plugin exposed last_name leave it blank).
    ct and gen come from the customer's most recent order
    (city cleaned of its "(C/MARCA)"-style suffix; gender mapped to F/M, blank
    when unknown). Phones are E.164 with the leading "+", as in the sample, and
    value is lifetime spend for value-based lookalikes.
    """
    skus, months, customer_type, min_orders, max_orders = _read_segment_filters()

    refresh_all_orders_if_needed()
    result = get_recurrent_customers(
        orders_csv_path=current_app.config["ALL_ORDERS_CSV"],
        sort=request.args.get("sort", DEFAULT_SEGMENT_SORT),
        direction=request.args.get("direction"),
        search=request.args.get("q", ""),
        min_orders=min_orders,
        max_orders=max_orders,
        sku_filter=set(skus),
        inactive_months=months,
        paginate=False,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["fn", "ln", "email", "phone", "country", "ct", "gen", "value"])
    exported = 0
    for row in result["rows"]:
        email = (row.get("email") or "").strip().lower()
        target, _reason = prepare_target(row.get("phone"))
        if not email and not target:
            continue  # nothing for Meta to match on
        writer.writerow([
            (row.get("name") or "").strip(),
            (row.get("last_name") or "").strip(),
            email,
            f"+{target}" if target else "",
            "CO",
            _clean_city(row.get("city")),
            _GENDER_TO_META.get((row.get("gender") or "").strip().lower(), ""),
            int(round(row.get("total_spent") or 0)),
        ])
        exported += 1

    logger.info("%s exported %d customers for a Meta custom audience (label filters: sku=%s months=%s type=%s)",
                getattr(current_user, "username", "unknown"), exported, skus, months, customer_type)

    # Name the file after the segment conditions, so an exported audience is
    # self-describing when it is uploaded to Meta weeks later, e.g.
    # inactive_for_more_than_3_months_repeated_customers_2026_09_01.csv or
    # inactive_for_more_than_2_years_all_customers_2026_09_07.csv
    type_slug = {
        "all": "all_customers",
        "first_time": "first_time_customers",
        "repeat": "repeated_customers",
    }[customer_type]
    window_slug = inactivity_label(months).replace(" ", "_")
    filename = (
        f"inactive_for_more_than_{window_slug}_{type_slug}_"
        f"{datetime.now():%Y_%m_%d}.csv"
    )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@main.route("/lapsed-customers/wati-remarketing", methods=["POST"])
@login_required
def lapsed_customers_wati_remarketing():
    """
    Tag the selected customers in WATI with a remarketing attribute.

    Creates or updates contacts only - no WhatsApp messages are sent.
    Customers without a usable E.164 phone are skipped and reported back.
    """
    data = request.get_json(silent=True) or {}
    emails = [e.strip().lower() for e in (data.get("emails") or []) if str(e).strip()]
    label = (data.get("label") or "").strip()
    # The attribute name is fixed in code: letting operators type it produces
    # "remarketing"/"Remarketing"/"remarkting" in the tenant and no segment is
    # ever complete. Only the value is free text.
    attribute = ATTRIBUTE_NAME

    if not emails:
        return jsonify({"status": "error", "message": "No customers selected."}), 400
    if not label:
        return jsonify({"status": "error", "message": "A remarketing label is required."}), 400
    if len(label) > MAX_VALUE_CHARS:
        return jsonify({"status": "error",
                        "message": f"Label is {len(label)} characters; the limit is {MAX_VALUE_CHARS}."}), 400
    if len(emails) > MAX_CONTACTS_PER_RUN:
        return jsonify({
            "status": "error",
            "message": f"{len(emails)} selected; the limit is {MAX_CONTACTS_PER_RUN} per run.",
        }), 400

    tenant_url = get_option_value(WATI_TENANT_URL_KEY)
    api_token = get_secret(WATI_TOKEN_KEY)
    if not tenant_url or not api_token:
        return jsonify({
            "status": "error",
            "message": "WATI is not configured. Add the tenant URL and API token in Settings.",
        }), 400

    logger.info("%s is setting %s=%r on %d contacts",
                getattr(current_user, "username", "unknown"), attribute, label, len(emails))

    try:
        selected, missing = _resolve_selected(emails)

        result = tag_contacts(
            tenant_url=tenant_url,
            api_token=api_token,
            customers=selected,
            label=label,
            attribute=attribute,
        )
        if missing:
            result["skipped"] += len(missing)
            result["skipped_detail"] += [
                {"email": e, "phone": "", "reason": "customer not found"} for e in missing
            ][:50]

        return jsonify({"status": "success", **result}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.exception("WATI remarketing tagging failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@main.route("/reconnect-lapsed-customers-by-email/send", methods=["POST"])
@login_required
def reconnect_lapsed_customers_send():
    """
    Email the selected customers with one saved template through SendGrid.

    One email per customer, placeholders filled from the same customer
    aggregate the table displayed. Sending is irreversible; the page confirms
    with the operator before calling this.
    """
    data = request.get_json(silent=True) or {}
    emails = [e.strip().lower() for e in (data.get("emails") or []) if str(e).strip()]
    try:
        template_id = int(data.get("template_id") or 0)
    except (TypeError, ValueError):
        template_id = 0

    if not emails:
        return jsonify({"status": "error", "message": "No customers selected."}), 400
    if len(emails) > MAX_RECIPIENTS_PER_RUN:
        return jsonify({
            "status": "error",
            "message": f"{len(emails)} selected; the limit is {MAX_RECIPIENTS_PER_RUN} per run.",
        }), 400
    if not template_id:
        return jsonify({"status": "error", "message": "Choose an email template."}), 400

    template = EmailTemplate.query.get(template_id)
    if not template:
        return jsonify({"status": "error", "message": "That email template no longer exists."}), 404

    cfg = get_sendgrid_config()
    if not cfg["api_key"] or not cfg["from_email"]:
        return jsonify({
            "status": "error",
            "message": "SendGrid is not configured. Add the API key and From email in Settings.",
        }), 400
    missing_hero = hero_missing_error(template)
    if missing_hero:
        return jsonify({"status": "error", "message": missing_hero}), 400

    who = getattr(current_user, "username", "unknown")
    logger.info("%s is emailing template %r to %d customers", who, template.name, len(emails))

    try:
        selected, missing = _resolve_selected(emails)

        result = send_template(
            api_key=cfg["api_key"],
            from_email=cfg["from_email"],
            from_name=cfg["from_name"],
            reply_to=cfg["reply_to"],
            asm_group_id=cfg["asm_group_id"],
            subject=template.subject,
            html_body=template.html_body,
            customers=selected,
            template_name=template.name,
            template_id=template.id,
            extra_context=template_context(template),
        )
        if missing:
            result["skipped"] += len(missing)
            result["skipped_detail"] += [
                {"email": e, "reason": "customer not found"} for e in missing
            ][:50]

        logger.info("%s emailed template %r: sent=%d skipped=%d failed=%d",
                    who, template.name, result["sent"], result["skipped"], result["failed"])
        return jsonify({"status": "success", **result}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.exception("SendGrid campaign send failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@main.route("/lapsed-customers/interview", methods=["POST"])
@login_required
def lapsed_customers_interview_save():
    """
    Create or update a customer's questionnaire answers, keyed by phone.

    Upsert by the normalized E.164 phone: the first save for a line creates
    the row, every later save updates it, so editing needs no separate path.
    Blank answers are allowed - an operator logging "no contesto" has nothing
    else to record yet.
    """
    data = request.get_json(silent=True) or {}

    target, reason = prepare_target(data.get("phone"))
    if not target:
        return jsonify({"status": "error",
                        "message": f"Unusable phone: {reason or 'empty'}."}), 400

    def _pick(field, allowed, required=False):
        value = (data.get(field) or "").strip().lower()
        if not value:
            return None if not required else "pending"
        if value not in allowed:
            raise ValueError(f"Invalid value {value!r} for {field}.")
        return value

    try:
        call_status = _pick("call_status", CustomerInterview.CALL_STATUSES, required=True)
        experience = _pick("experience", CustomerInterview.EXPERIENCES)
        buy_again = _pick("buy_again", CustomerInterview.YES_NO_MAYBE)
        recommend = _pick("recommend", CustomerInterview.YES_NO_MAYBE)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    interview = CustomerInterview.query.filter_by(phone=target).first()
    if not interview:
        interview = CustomerInterview(phone=target)
        db.session.add(interview)

    interview.email = (data.get("email") or "").strip().lower() or interview.email
    interview.call_status = call_status
    interview.experience = experience
    interview.experience_notes = (data.get("experience_notes") or "").strip() or None
    interview.buy_again = buy_again
    interview.buy_again_reason = (data.get("buy_again_reason") or "").strip() or None
    interview.recommend = recommend
    interview.comments = (data.get("comments") or "").strip() or None
    interview.interviewed_by = getattr(current_user, "username", None)

    db.session.commit()
    logger.info("%s saved interview for %s (status=%s)",
                interview.interviewed_by, target, call_status)

    return jsonify({"status": "success", "interview": interview.to_dict()}), 200
