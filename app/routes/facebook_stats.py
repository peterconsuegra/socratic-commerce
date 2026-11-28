# app/routes/facebook_stats.py
"""
/facebook_stats: the daily sales trend of the utm_source=facebook orders, all
together and per utm_campaign, with a warning for each campaign whose sales
fell more than DECLINE_WARNING_DAYS days in a row (services/facebook_stats.py).

Reads the full order history (all_orders.csv, load_orders), not a date-range
file: ?days= picks the window, one of WINDOW_CHOICES complete days.
"""
import logging

from flask import current_app, render_template, request
from flask_login import login_required

from app.services.facebook_stats import DEFAULT_WINDOW_DAYS, WINDOW_CHOICES, build_facebook_stats
from app.services.get_data import load_orders, orders_as_of

from . import main
from .common import refresh_all_orders_if_needed

logger = logging.getLogger(__name__)

# Facebook's slot in the app's validated series palette (as on /stats), and
# the brand's warning step for a run of falling days, which also gets an icon
# and a label: never colour alone.
COLORS = {"series": "#2F62C9", "average": "#4A515C", "warning": "#B7791F"}


def _window_days() -> int:
    try:
        days = int(request.args.get("days", DEFAULT_WINDOW_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return days if days in WINDOW_CHOICES else DEFAULT_WINDOW_DAYS


@main.route("/facebook_stats")
@login_required
def facebook_stats():
    """Daily utm_source=facebook sales, total and per campaign, and falling-campaign warnings."""
    days = _window_days()
    error, stats, as_of = None, None, None
    try:
        refresh_all_orders_if_needed()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        stats = build_facebook_stats(load_orders(orders_csv), days)
        as_of = orders_as_of(orders_csv)
    except Exception as e:
        logger.exception("Failed to build Facebook stats")
        error = str(e)

    return render_template(
        "facebook_stats.html",
        error=error,
        stats=stats,
        orders_as_of=as_of,
        days=days,
        window_choices=WINDOW_CHOICES,
        colors=COLORS,
    )
