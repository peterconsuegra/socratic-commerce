# app/routes/monthly_by_kind.py
"""
The root view (/, Monthly Sales) for one kind of order, shared by
/new_customers_monthly (first purchases) and /male_sales_monthly (orders the
store marks male). Not a route itself: render_monthly_by_kind is called by
those routes.

The page (templates/monthly_by_kind.html) shows, like / and /monthly_repurchases:

  - a pie of the kind's sales by utm_source, up to the end of last month;
  - the kind's monthly sales trend with its forecast (the current month
    projected weekday-weighted), plus the number of those orders per month;
  - the same trend for each channel with at least 2% of those sales.

Which orders a kind counts is ORDER_KINDS in services/monthly_repurchases.py;
its English texts are KIND_TEXT below (Spanish in app/i18n.py).
"""
import os

import pandas as pd
from flask import current_app, render_template

from app.i18n import translate as _
from app.services.get_data import load_orders
from app.services.monthly_repurchases import ORDER_KINDS, ROW_KEYS, get_monthly_repurchases_trend

from .common import refresh_all_orders_if_needed

FORECAST_MONTHS = 12
MIN_CHANNEL_SHARE_PERCENT = 2.0

# Per kind: the canvas id prefix and the page's texts, in English.
KIND_TEXT = {
    "first": {
        "chart_prefix": "newCustomers",
        "title": "New Customers by Month",
        "heading": "Monthly New Customers Trend & Forecast",
        "pie": "New Customer Sales by Channel (utm_source) up to end of previous month",
        "total": "Total New Customer Sales",
        "count_heading": "New Customers per Month",
        "count_note": "Customers whose first paid order was that month (complete months, Bogota time).",
        "count_label": "New Customers",
        "by_channel": "New Customers by Channel (ranked by total sales)",
        "channel_heading": "{channel} New Customers",
        "ranked": "Ranked by number of new customers: {count}",
        "historical": "Historical New Customer Sales",
        "forecasted": "Forecasted New Customer Sales",
        "channel_historical": "Historical {channel} New Customer Sales",
        "channel_forecasted": "Forecasted {channel} New Customer Sales",
    },
    "male": {
        "chart_prefix": "maleSales",
        "title": "Male Sales by Month",
        "heading": "Monthly Male Sales Trend & Forecast",
        "pie": "Male Sales by Channel (utm_source) up to end of previous month",
        "total": "Total Male Sales",
        "count_heading": "Male Orders per Month",
        "count_note": "Paid orders the store marks male (complete months, Bogota time).",
        "count_label": "Male Orders",
        "by_channel": "Male Sales by Channel (ranked by total sales)",
        "channel_heading": "{channel} Male Sales",
        "ranked": "Ranked by number of male orders: {count}",
        "historical": "Historical Male Sales",
        "forecasted": "Forecasted Male Sales",
        "channel_historical": "Historical {channel} Male Sales",
        "channel_forecasted": "Forecasted {channel} Male Sales",
    },
}


def _trend(kind: str, orders_csv_path: str, utm_source_filter: str | None = None):
    return get_monthly_repurchases_trend(
        kind=kind,
        forecast_periods=FORECAST_MONTHS,
        return_forecast=True,
        return_meta=True,
        include_current_month_for_forecast=True,
        projection_method="weekday_weighted",
        weekday_history_months=6,
        orders_csv_path=orders_csv_path,
        utm_source_filter=utm_source_filter,
    )


def _projection_fields(meta: dict) -> dict:
    """The current-month projection figures the template prints under a chart."""
    return {
        "forecast_includes_current_month_mtd": bool(meta.get("forecast_includes_current_month_mtd", False)),
        "projection_method": meta.get("projection_method", "weekday_weighted"),
        "current_month_label": meta.get("current_month_label"),
        "current_month_mtd_sales": float(meta.get("current_month_mtd_sales", 0.0) or 0.0),
        "current_month_projected_sales": float(meta.get("current_month_projected_sales", 0.0) or 0.0),
        "current_month_remaining_days": int(meta.get("current_month_remaining_days", 0) or 0),
        "current_month_days_in_month": int(meta.get("current_month_days_in_month", 0) or 0),
    }


def render_monthly_by_kind(kind: str):
    """The monthly page for one ORDER_KINDS kind ("first" or "male")."""
    text = KIND_TEXT[kind]
    value_key, forecast_key = ROW_KEYS[kind]

    try:
        refresh_all_orders_if_needed()
    except Exception:
        current_app.logger.exception("Failed refreshing all_orders cache")

    all_orders = current_app.config["ALL_ORDERS_CSV"]

    trend = []
    forecast_data = []
    projection = _projection_fields({})
    pie_labels = []
    pie_values = []
    channel_charts = []
    other_channels_labels = []
    other_channels_pct = 0.0
    error = None

    try:
        if not os.path.exists(all_orders):
            raise FileNotFoundError(_("{file} not found. Please generate all orders first.").format(file=all_orders))

        trend, forecast_data, meta = _trend(kind, all_orders)
        projection = _projection_fields(meta)

        df = load_orders(all_orders)

        today_bogota = pd.Timestamp.now(tz="America/Bogota").tz_localize(None).normalize()
        end_prev_month = today_bogota.replace(day=1) - pd.Timedelta(microseconds=1)

        orders = df[(df["order_date"] <= end_prev_month) & ORDER_KINDS[kind](df)].copy()
        orders["utm_source"] = orders["utm_source"].str.strip().str.lower()
        orders.loc[orders["utm_source"] == "", "utm_source"] = "unknown"

        by_source = (
            orders.groupby("utm_source")
            .agg(value=("total_value", "sum"), count=("total_value", "size"))
            .sort_values("value", ascending=False)
        )
        by_source = by_source[by_source["value"] > 0]

        pie_labels = [str(k) for k in by_source.index]
        pie_values = [float(v) for v in by_source["value"]]

        total_value = float(by_source["value"].sum())
        if total_value > 0:
            by_source["pct"] = by_source["value"] / total_value * 100.0
            included = by_source[by_source["pct"] >= MIN_CHANNEL_SHARE_PERCENT].sort_values("count", ascending=False)
            excluded = by_source[by_source["pct"] < MIN_CHANNEL_SHARE_PERCENT].sort_values("pct", ascending=False)

            other_channels_labels = [str(k) for k in excluded.index]
            other_channels_pct = float(excluded["pct"].sum())

            for channel, row in included.iterrows():
                if channel == "unknown":  # as on /: no trend for orders without a source
                    continue
                ch_trend, ch_forecast, ch_meta = _trend(kind, all_orders, utm_source_filter=channel)
                if not ch_trend:
                    continue

                safe = "".join(c if c.isalnum() else "_" for c in channel)
                channel_charts.append({
                    "key": channel,
                    "label": f"{channel.upper()} ({row['pct']:.1f}%)",
                    "canvas_id": f"{text['chart_prefix']}Chart_{safe}",
                    "trend": ch_trend,
                    "forecast": ch_forecast,
                    "pct": float(row["pct"]),
                    "count": int(row["count"]),
                    **_projection_fields(ch_meta),
                })

    except Exception as e:
        error = str(e)
        current_app.logger.exception("monthly %s view failed", kind)

    return render_template(
        "monthly_by_kind.html",
        text=text,
        chart_prefix=text["chart_prefix"],
        value_key=value_key,
        forecast_key=forecast_key,
        monthly_trend=trend,
        forecast_data=forecast_data,
        pie_labels=pie_labels,
        pie_values=pie_values,
        channel_charts=channel_charts,
        other_channels_labels=other_channels_labels,
        other_channels_pct=other_channels_pct,
        error=error,
        **projection,
    )
