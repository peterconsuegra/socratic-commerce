# app/routes/wati_customers.py
"""
Customers who bought through WATI (any order with utm_campaign=wati), listed
with the usual per-customer columns and exportable as a Meta Ads custom
audience CSV, in the same shape as /export-lapsed-customers.
"""
import logging
from datetime import datetime

from flask import Response, current_app, render_template, request
from flask_login import current_user, login_required

from app.services.recurrent_customers import DEFAULT_PER_PAGE, customers_by_utm_campaign, get_recurrent_customers

from . import main
from .common import refresh_all_orders_if_needed
from .lapsed import CUSTOMER_TYPES, PER_PAGE_CHOICES, meta_audience_csv

logger = logging.getLogger(__name__)

WATI_CAMPAIGN = "wati"
# Most recent buyers first.
DEFAULT_SORT = "last_order_utc"


def _read_filters():
    customer_type = (request.args.get("customer_type", "all") or "all").strip().lower()
    if customer_type not in CUSTOMER_TYPES:
        customer_type = "all"
    return customer_type, CUSTOMER_TYPES[customer_type]["bounds"]


def _wati_customers(paginate: bool = True):
    """The WATI customers matching the query string, plus each one's WATI order stats."""
    customer_type, (min_orders, max_orders) = _read_filters()
    refresh_all_orders_if_needed()
    path = current_app.config["ALL_ORDERS_CSV"]
    stats = customers_by_utm_campaign(path, WATI_CAMPAIGN)
    result = get_recurrent_customers(
        orders_csv_path=path,
        page=request.args.get("page", 1),
        per_page=request.args.get("per_page", DEFAULT_PER_PAGE),
        sort=request.args.get("sort", DEFAULT_SORT),
        direction=request.args.get("direction"),
        search=request.args.get("q", ""),
        min_orders=min_orders,
        max_orders=max_orders,
        emails=list(stats.index),
        paginate=paginate,
    )
    for row in result["rows"]:
        key = (row.get("email") or "").strip().lower()
        s = stats.loc[key] if key in stats.index else None
        row["wati_orders"] = int(s["campaign_orders"]) if s is not None else 0
        row["wati_spent"] = float(s["campaign_spent"]) if s is not None else 0.0
        row["last_wati_order_utc"] = s["last_campaign_order_utc"] if s is not None else ""
    return result, customer_type


@main.route("/wati-customers")
@login_required
def wati_customers():
    """Every customer with at least one utm_campaign=wati order."""
    error = None
    result, customer_type = None, "all"
    try:
        result, customer_type = _wati_customers()
    except Exception as e:
        logger.exception("Failed to build WATI customers listing")
        error = str(e)
    return render_template(
        "wati_customers.html",
        error=error,
        result=result,
        customer_type=customer_type,
        customer_types=CUSTOMER_TYPES,
        per_page_choices=PER_PAGE_CHOICES,
    )


@main.route("/wati-customers/export.csv")
@login_required
def wati_customers_export():
    """The current WATI segment - all pages - as a Meta Ads custom audience CSV."""
    result, customer_type = _wati_customers(paginate=False)
    body, exported = meta_audience_csv(result["rows"])
    logger.info("%s exported %d WATI customers for a Meta custom audience (type=%s)",
                getattr(current_user, "username", "unknown"), exported, customer_type)
    # e.g. wati_customers_repeated_customers_2026_10_27.csv
    type_slug = {
        "all": "all_customers",
        "first_time": "first_time_customers",
        "repeat": "repeated_customers",
    }[customer_type]
    filename = f"wati_customers_{type_slug}_{datetime.now():%Y_%m_%d}.csv"
    return Response(
        body,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
