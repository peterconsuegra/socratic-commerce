# app/routes/lapsed.py
import csv
import io
import logging
import re
from datetime import datetime

from flask import Response, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app.services.recurrent_customers import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    get_recurrent_customers,
)

from app.services.secrets import get_secret
from app.services.wati import prepare_target
from app.services.wati import (
    ATTRIBUTE_NAME,
    MAX_CONTACTS_PER_RUN,
    MAX_VALUE_CHARS,
    tag_contacts,
)

from . import main
from .common import get_option_value, refresh_all_orders_if_needed
from .options import WATI_TENANT_URL_KEY, WATI_TOKEN_KEY

logger = logging.getLogger(__name__)

PER_PAGE_CHOICES = [50, 100, 250, 500]
MONTHS_CHOICES = [3, 6, 9, 12]

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


@main.route("/lapsed-customers")
@login_required
def lapsed_customers():
    """Customers whose last purchase was a given SKU and who have since lapsed."""
    skus, months, customer_type, min_orders, max_orders = _read_segment_filters()

    error = None
    result = None

    try:
        refresh_all_orders_if_needed()
        result = get_recurrent_customers(
            orders_csv_path=current_app.config["ALL_ORDERS_CSV"],
            page=request.args.get("page", 1),
            per_page=request.args.get("per_page", DEFAULT_PER_PAGE),
            sort=request.args.get("sort", "spent"),
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

    return render_template(
        "lapsed_customers.html",
        error=error,
        result=result,
        per_page_choices=PER_PAGE_CHOICES,
        wati_attribute=ATTRIBUTE_NAME,
        wati_max_value=MAX_VALUE_CHARS,
        wati_max=MAX_CONTACTS_PER_RUN,
        wati_ready=bool(get_secret("wati_api_token")),
        months_choices=MONTHS_CHOICES,
        selected_skus=skus,
        months=months,
        customer_type=customer_type,
        customer_types=CUSTOMER_TYPES,
    )


@main.route("/lapsed-customers/export.csv")
@login_required
def lapsed_customers_export():
    """
    The current segment - every matching row, not just the visible page - as a
    CSV shaped for Meta Ads custom audience uploads.

    Columns match the user's working audience file exactly:
        fn,ln,email,phone,country,ct,gen,value
    country is fixed to CO. ln is always blank - the orders source carries no
    last name anywhere. ct and gen come from the customer's most recent order
    (city cleaned of its "(C/MARCA)"-style suffix; gender mapped to F/M, blank
    when unknown). Phones are E.164 with the leading "+", as in the sample, and
    value is lifetime spend for value-based lookalikes.
    """
    skus, months, customer_type, min_orders, max_orders = _read_segment_filters()

    refresh_all_orders_if_needed()
    result = get_recurrent_customers(
        orders_csv_path=current_app.config["ALL_ORDERS_CSV"],
        sort=request.args.get("sort", "spent"),
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
            "",  # no last name exists anywhere in the orders source
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
    # inactive_for_more_than_3_months_repeated_customers_2026_09_01.csv
    type_slug = {
        "all": "all_customers",
        "first_time": "first_time_customers",
        "repeat": "repeated_customers",
    }[customer_type]
    filename = (
        f"inactive_for_more_than_{months}_months_{type_slug}_"
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
        # Resolve the selected emails against the customer aggregate, so phone
        # and attributes come from the same source the table displayed. The
        # emails filter matters: without it this returns the top rows of the
        # spend-ranked listing, and any selected customer below that cutoff
        # would wrongly resolve as "customer not found".
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
