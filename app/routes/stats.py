# app/routes/stats.py
"""
The /stats view: how customers repurchase, and what the win-back contacts bring
back. And /first-time-orders: where each customer's first purchase came from.
"""
import logging

import pandas as pd
from flask import current_app, render_template, request
from flask_login import login_required

from app import db
from app.models import CustomerContact
from app.services.first_orders import NO_UTM_SERIES, OTHER_SERIES, compare_periods, first_orders_report
from app.services.stats import orders_stats, winback_stats

from . import main
from .common import refresh_all_orders_if_needed

logger = logging.getLogger(__name__)

# Which contacts the win-back section evaluates. Attempts are always whole-log.
PERIOD_CHOICES = [(30, "Last 30 days"), (90, "Last 90 days"), (180, "Last 180 days"),
                  (365, "Last 12 months"), (0, "All time")]
DEFAULT_PERIOD = 90

# Validated categorical slots for the charts (see the dataviz method):
# teal, blue, orange - fixed order, never cycled.
CHART_SERIES = ["#0F8B7C", "#2F62C9", "#C8621B"]

# First-time orders by source: one fixed colour per series, so a source keeps
# its colour as shares move. Categorical slots in stacking order (validated:
# adjacent CVD and normal-vision separation pass; magenta and yellow sit under
# 3:1 contrast, so the chart ships a table view), and a neutral for no UTM.
SOURCE_COLORS = {
    "wati": "#0F8B7C", "facebook": "#2F62C9", "google": "#C8621B", "ig": "#E87BA4",
    OTHER_SERIES: "#EDA100", NO_UTM_SERIES: "#A3ACB5",
}
# Period A against period B: the hue for the period looked at, gray for the
# one it is compared with (emphasis - B is context). The pair separates for
# colour-blind readers; gray sits under 3:1, so the chart has a table view.
COMPARE_COLORS = {"a": "#0F8B7C", "b": "#A3ACB5"}
# The comparison's query parameters, kept when the month selector changes.
COMPARE_PARAMS = ("vs", "vs_by", "vs_week", "vs_month", "a_start", "a_end", "b_start", "b_end")


def _contacts_frame() -> pd.DataFrame:
    rows = db.session.query(
        CustomerContact.id, CustomerContact.email, CustomerContact.channel,
        CustomerContact.label, CustomerContact.sent_at,
    ).all()
    return pd.DataFrame(rows, columns=["id", "email", "channel", "label", "sent_at"])


@main.route("/stats")
@login_required
def stats():
    try:
        period = int(request.args.get("period", DEFAULT_PERIOD))
    except (TypeError, ValueError):
        period = DEFAULT_PERIOD
    if period not in {p for p, _ in PERIOD_CHOICES}:
        period = DEFAULT_PERIOD

    error = None
    repurchase = None
    winback = None
    try:
        refresh_all_orders_if_needed()
        repurchase, orders = orders_stats(current_app.config["ALL_ORDERS_CSV"])
        winback = winback_stats(orders, _contacts_frame(), period)
    except Exception as e:
        logger.exception("Failed to build repurchase stats")
        error = str(e)

    return render_template(
        "stats.html",
        error=error,
        repurchase=repurchase,
        winback=winback,
        period=period,
        period_choices=PERIOD_CHOICES,
        series=CHART_SERIES,
    )


@main.route("/first-time-orders")
@login_required
def first_time_orders():
    """
    Where the first-time orders of a month came from (utm_source >
    utm_campaign > utm_content), and two periods side by side.
    """
    error = None
    report = None
    compare = None
    args = request.args
    try:
        refresh_all_orders_if_needed()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        report = first_orders_report(orders_csv, args.get("month", ""))
        compare = compare_periods(
            orders_csv,
            mode=args.get("vs", "month"),
            by=args.get("vs_by", "source"),
            week=args.get("vs_week"),
            month=args.get("vs_month"),
            custom={k: args.get(k, "") for k in ("a_start", "a_end", "b_start", "b_end")},
        )
    except Exception as e:
        logger.exception("Failed to build the first-time orders report")
        error = str(e)

    return render_template(
        "first_time_orders.html", error=error, report=report, compare=compare, colors=SOURCE_COLORS,
        compare_colors=COMPARE_COLORS, compare_params={k: args[k] for k in COMPARE_PARAMS if args.get(k)},
    )
