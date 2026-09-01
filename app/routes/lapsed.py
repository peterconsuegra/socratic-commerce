# app/routes/lapsed.py
import logging

from flask import current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app.services.recurrent_customers import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    get_recurrent_customers,
)

from app.services.secrets import get_secret
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


@main.route("/lapsed-customers")
@login_required
def lapsed_customers():
    """Customers whose last purchase was a given SKU and who have since lapsed."""
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
        # and attributes come from the same source the table displayed.
        refresh_all_orders_if_needed()
        everyone = get_recurrent_customers(
            orders_csv_path=current_app.config["ALL_ORDERS_CSV"],
            min_orders=1,
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
