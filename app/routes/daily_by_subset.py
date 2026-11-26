# app/routes/daily_by_subset.py
"""
/daily_sales for a subset of orders, shared by /daily_new_customers (first
purchases) and /link_in_bio_stats_daily (utm_content link_in_bio). Not a route
itself: render_daily_subset is called by those routes.

Each page reads its own date-range file (picked with the date selector, like
/daily_sales) and limits every chart to its subset (services/daily_sales.py,
SUBSETS). templates/daily_sales.html draws no repurchase lines for a subset;
it adds the number of the subset's orders per day instead. The English texts
of each page are SUBSET_TEXT below (Spanish in app/i18n.py).
"""
from flask import render_template

from app.i18n import translate as _
from app.services.daily_sales import build_daily_sales_dashboard_context
from app.services.periods import range_includes_today

from .daily import _saved_date_range

SUBSET_TEXT = {
    "first": {
        "title": "New Customers by Day",
        "heading": "Daily New Customer Sales Trend and Forecast",
        "note": "Only each customer's first paid order counts, so every chart on this page is about new customers.",
        "count_heading": "New Customers per Day",
        "count_label": "New Customers",
        "sales_label": "New Customer Sales",
        "by_channel": "Daily New Customer Sales Trend and Forecast by Channel",
        "channel_heading": "{channel} New Customer Sales Trend and Forecast",
        "channel_sales": "{channel} New Customer Sales",
    },
    "link_in_bio": {
        "title": "Link in Bio Stats by Day",
        "heading": "Daily Link in Bio Sales Trend and Forecast",
        "note": "Only orders with utm_content link_in_bio (the Instagram link in bio) count, "
                "so every chart on this page is about them.",
        "count_heading": "Link in Bio Orders per Day",
        "count_label": "Link in Bio Orders",
        "sales_label": "Link in Bio Sales",
        "by_channel": "Daily Link in Bio Sales Trend and Forecast by Channel",
        "channel_heading": "{channel} Link in Bio Sales Trend and Forecast",
        "channel_sales": "{channel} Link in Bio Sales",
    },
}


def render_daily_subset(only: str, file_name: str):
    """The daily dashboard over data/<file_name>, limited to the SUBSETS key `only`."""
    text = SUBSET_TEXT[only]
    date_range, start_date, end_date = _saved_date_range(file_name)
    orders_file = f"data/{file_name}"

    context = build_daily_sales_dashboard_context(
        input_file=orders_file,
        forecast_periods=30,
        min_channel_share_percent=2.0,
        include_today=range_includes_today(end_date),
        only=only,
    )

    # The service's "no file yet" message, in the page's language.
    if context.get("error") == f"{orders_file} not found. Use the date selector above to fetch data first.":
        context["error"] = _("{file} not found. Use the date selector above to fetch data first.").format(file=orders_file)

    context.update(
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        file_name=file_name,
        page_title=text["title"],
        subset=text,
    )
    return render_template("daily_sales.html", **context)
