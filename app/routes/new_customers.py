# app/routes/new_customers.py
"""
The sales views for new customers only - each customer's first paid order,
the store's purchase_number 1 (see get_data):

  - /new_customers_monthly: the root view (/, Monthly Sales), through
    render_monthly_by_kind (routes/monthly_by_kind.py);
  - /daily_new_customers: /daily_sales, over the page's own date-range file.
"""
from flask import render_template
from flask_login import login_required

from app.i18n import translate as _
from app.services.daily_sales import build_daily_sales_dashboard_context
from app.services.periods import range_includes_today

from . import main
from .daily import _saved_date_range
from .monthly_by_kind import render_monthly_by_kind

DAILY_FILE = "daily_new_customers_orders.csv"


@main.route("/new_customers_monthly")
@login_required
def new_customers_monthly():
    return render_monthly_by_kind("first")


@main.route("/daily_new_customers")
@login_required
def daily_new_customers():
    """
    /daily_sales for new customers: every chart counts only first purchases
    (purchase_number 1) in the range picked with the date selector, which
    fetches data/daily_new_customers_orders.csv. The page draws no repurchase
    lines; it adds the number of new customers per day instead.
    """
    date_range, start_date, end_date = _saved_date_range(DAILY_FILE)
    orders_file = f"data/{DAILY_FILE}"

    context = build_daily_sales_dashboard_context(
        input_file=orders_file,
        forecast_periods=30,
        min_channel_share_percent=2.0,
        include_today=range_includes_today(end_date),
        first_purchases_only=True,
    )

    # The service's "no file yet" message, in the page's language.
    if context.get("error") == f"{orders_file} not found. Use the date selector above to fetch data first.":
        context["error"] = _("{file} not found. Use the date selector above to fetch data first.").format(file=orders_file)

    context.update(
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        file_name=DAILY_FILE,
        page_title="New Customers by Day",
        new_customers=True,
    )
    return render_template("daily_sales.html", **context)
