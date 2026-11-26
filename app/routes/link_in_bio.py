# app/routes/link_in_bio.py
"""
The sales views for the orders from Instagram's link in bio - utm_content
link_in_bio (trimmed, any case):

  - /link_in_bio_stats_monthly: the root view (/, Monthly Sales), through
    render_monthly_by_kind (routes/monthly_by_kind.py);
  - /link_in_bio_stats_daily: /daily_sales over the page's own date-range
    file, through render_daily_subset (routes/daily_by_subset.py).
"""
from flask_login import login_required

from . import main
from .daily_by_subset import render_daily_subset
from .monthly_by_kind import render_monthly_by_kind

DAILY_FILE = "link_in_bio_daily_orders.csv"


@main.route("/link_in_bio_stats_monthly")
@login_required
def link_in_bio_stats_monthly():
    return render_monthly_by_kind("link_in_bio")


@main.route("/link_in_bio_stats_daily")
@login_required
def link_in_bio_stats_daily():
    return render_daily_subset("link_in_bio", DAILY_FILE)
