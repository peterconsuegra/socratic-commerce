# app/routes/new_customers.py
"""
/new_customers_monthly: the root view (/, Monthly Sales) for new customers only.

A new customer is an order the store numbers purchase_number 1 (see get_data);
every customer counts once, in the month of their first paid order. The page
shows, like / and /monthly_repurchases:

  - a pie of first-purchase sales by utm_source, up to the end of last month;
  - the monthly first-purchase sales trend with its forecast (the current
    month projected weekday-weighted), plus the number of new customers;
  - the same trend for each channel with at least 2% of those sales.

The data comes from get_monthly_new_customers_trend (monthly_repurchases.py).
"""
import os

import pandas as pd
from flask import current_app, render_template
from flask_login import login_required

from app.i18n import translate as _
from app.services.get_data import load_orders
from app.services.monthly_repurchases import ORDER_KINDS, get_monthly_new_customers_trend

from . import main
from .common import refresh_all_orders_if_needed

FORECAST_MONTHS = 12
MIN_CHANNEL_SHARE_PERCENT = 2.0


def _trend(orders_csv_path: str, utm_source_filter: str | None = None):
    return get_monthly_new_customers_trend(
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


@main.route("/new_customers_monthly")
@login_required
def new_customers_monthly():
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

        trend, forecast_data, meta = _trend(all_orders)
        projection = _projection_fields(meta)

        df = load_orders(all_orders)

        today_bogota = pd.Timestamp.now(tz="America/Bogota").tz_localize(None).normalize()
        end_prev_month = today_bogota.replace(day=1) - pd.Timedelta(microseconds=1)

        first = df[(df["order_date"] <= end_prev_month) & ORDER_KINDS["first"](df)].copy()
        first["utm_source"] = first["utm_source"].str.strip().str.lower()
        first.loc[first["utm_source"] == "", "utm_source"] = "unknown"

        by_source = (
            first.groupby("utm_source")
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
                ch_trend, ch_forecast, ch_meta = _trend(all_orders, utm_source_filter=channel)
                if not ch_trend:
                    continue

                safe = "".join(c if c.isalnum() else "_" for c in channel)
                channel_charts.append({
                    "key": channel,
                    "label": f"{channel.upper()} ({row['pct']:.1f}%)",
                    "canvas_id": f"newCustomersChart_{safe}",
                    "trend": ch_trend,
                    "forecast": ch_forecast,
                    "pct": float(row["pct"]),
                    "count": int(row["count"]),
                    **_projection_fields(ch_meta),
                })

    except Exception as e:
        error = str(e)
        current_app.logger.exception("new_customers_monthly view failed")

    return render_template(
        "new_customers_monthly.html",
        new_customers_trend=trend,
        forecast_data=forecast_data,
        pie_labels=pie_labels,
        pie_values=pie_values,
        channel_charts=channel_charts,
        other_channels_labels=other_channels_labels,
        other_channels_pct=other_channels_pct,
        error=error,
        **projection,
    )
