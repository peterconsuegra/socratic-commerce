# app/routes/stats.py
"""
The /stats view: how customers repurchase, and what the win-back contacts bring
back. And /first-time-orders and /repurchase-orders: where each customer's
first purchase, and each repurchase, came from.
"""
import logging

import pandas as pd
from flask import current_app, render_template, request
from flask_login import login_required

from app import db
from app.models import CustomerContact
from app.services.orders_by_kind import NO_UTM_SERIES, OTHER_SERIES, compare_periods, orders_report
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

# Orders by source: one fixed colour per series, so a source keeps
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
# The city, product and time bars: one hue, and gray for the bar gathering the
# cities or products beyond the top ones (context, as period B is above).
BAR_COLORS = {"bar": "#0F8B7C", "rest": "#A3ACB5"}
# The day-by-day chart shades weekends and holidays behind its bars rather
# than recolouring them: a gray wash, and an amber one for holidays (checked:
# the two washes and the card separate, and the bars keep 3:1 on both).
DAY_BANDS = {"weekend": "rgba(21, 24, 29, 0.08)", "holiday": "rgba(237, 161, 0, 0.30)"}
# The comparison's query parameters, kept when the month selector changes.
COMPARE_PARAMS = ("vs", "vs_by", "vs_week", "vs_month", "a_start", "a_end", "b_start", "b_end")
# The pages on one kind of order (see orders_by_kind.KINDS): the same views,
# each told in its own words.
ORDER_PAGES = {
    "first": {
        "endpoint": "main.first_time_orders", "title": "First-Time Orders",
        "intro": "Where each customer's first purchase came from, month by month: "
                 "utm_source › utm_campaign › utm_content.",
        "noun": "first-time orders", "revenue": "First-order revenue", "average": "Average first order",
        "customers": "new customers",
        "definition": "A first-time order is a customer's first paid purchase, as the store numbers them.",
    },
    "repeat": {
        "endpoint": "main.repurchase_orders", "title": "Repurchase Orders",
        "intro": "Where each repurchase came from, month by month: utm_source › utm_campaign › utm_content.",
        "noun": "repurchases", "revenue": "Repurchase revenue", "average": "Average repurchase",
        "customers": "returning customers",
        "definition": "A repurchase is a paid order from a customer who had bought before: the store's "
                      "purchase number 2 and up.",
    },
}


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
    utm_campaign > utm_content), its days with weekends and Colombian holidays
    set apart, where those new customers live, what they bought and when; and
    two periods side by side.
    """
    return _orders_by_kind_page("first")


@main.route("/repurchase-orders")
@login_required
def repurchase_orders():
    """The same views for repurchases: every paid order after a customer's first."""
    return _orders_by_kind_page("repeat")


def _orders_by_kind_page(kind: str):
    page = ORDER_PAGES[kind]
    error = None
    report = None
    compare = None
    args = request.args
    try:
        refresh_all_orders_if_needed()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        report = orders_report(orders_csv, kind, args.get("month", ""))
        compare = compare_periods(
            orders_csv,
            kind,
            mode=args.get("vs", "month"),
            by=args.get("vs_by", "source"),
            week=args.get("vs_week"),
            month=args.get("vs_month"),
            custom={k: args.get(k, "") for k in ("a_start", "a_end", "b_start", "b_end")},
        )
    except Exception as e:
        logger.exception("Failed to build the %s report", page["noun"])
        error = str(e)

    return render_template(
        "orders_by_kind.html", page=page, error=error, report=report, compare=compare, colors=SOURCE_COLORS,
        compare_colors=COMPARE_COLORS, bar_colors=BAR_COLORS, day_bands=DAY_BANDS,
        compare_params={k: args[k] for k in COMPARE_PARAMS if args.get(k)},
    )
