# app/routes/stats.py
"""The /stats view: how customers repurchase, and what the win-back contacts bring back."""
import logging

import pandas as pd
from flask import current_app, render_template, request
from flask_login import login_required

from app import db
from app.models import CustomerContact
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
