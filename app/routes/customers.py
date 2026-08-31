# app/routes/customers.py
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


@main.route("/recurrent-customers")
@login_required
def recurrent_customers():
    """Listing of customers with more than one order, paginated 100 per page."""
    page = request.args.get("page", 1)
    per_page = request.args.get("per_page", DEFAULT_PER_PAGE)
    sort = request.args.get("sort", "spent")
    direction = request.args.get("direction")
    search = request.args.get("q", "")

    error = None
    result = None

    try:
        refresh_all_orders_if_needed()
        result = get_recurrent_customers(
            orders_csv_path=current_app.config["ALL_ORDERS_CSV"],
            page=page,
            per_page=per_page,
            sort=sort,
            direction=direction,
            search=search,
        )
    except Exception as e:
        logger.exception("Failed to build recurrent customers listing")
        error = str(e)

    return render_template(
        "recurrent_customers.html",
        error=error,
        result=result,
        per_page_choices=PER_PAGE_CHOICES,
    )
