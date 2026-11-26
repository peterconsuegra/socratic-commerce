# app/routes/new_customers.py
"""
The sales views for new customers only - each customer's first paid order,
the store's purchase_number 1 (see get_data):

  - /new_customers_monthly: the root view (/, Monthly Sales), through
    render_monthly_by_kind (routes/monthly_by_kind.py);
  - /daily_new_customers: /daily_sales over the page's own date-range file,
    through render_daily_subset (routes/daily_by_subset.py).
"""
from flask_login import login_required

from . import main
from .daily_by_subset import render_daily_subset
from .monthly_by_kind import render_monthly_by_kind

DAILY_FILE = "daily_new_customers_orders.csv"


@main.route("/new_customers_monthly")
@login_required
def new_customers_monthly():
    return render_monthly_by_kind("first")


@main.route("/daily_new_customers")
@login_required
def daily_new_customers():
    return render_daily_subset("first", DAILY_FILE)
