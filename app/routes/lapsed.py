# app/routes/lapsed.py
import logging

from flask import current_app, render_template, request
from flask_login import login_required

from app.services.recurrent_customers import (
    DEFAULT_PER_PAGE,
    get_recurrent_customers,
)

from . import main
from .common import refresh_all_orders_if_needed

logger = logging.getLogger(__name__)

PER_PAGE_CHOICES = [50, 100, 250, 500]
MONTHS_CHOICES = [3, 6, 9, 12]

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

    # 1 = every customer including one-time buyers; 2 = repeat buyers only.
    try:
        min_orders = int(request.args.get("min_orders", 1))
    except (TypeError, ValueError):
        min_orders = 1
    min_orders = 2 if min_orders == 2 else 1

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
        months_choices=MONTHS_CHOICES,
        selected_skus=skus,
        months=months,
        min_orders=min_orders,
    )
