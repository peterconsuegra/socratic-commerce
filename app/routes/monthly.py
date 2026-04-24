# app/routes/monthly.py
import os

import pandas as pd
from flask import current_app, render_template
from flask_login import login_required

from app.services.monthly_repurchases import get_monthly_repurchases_trend
from app.services.monthly_sales import get_monthly_sales_trend

from . import main
from .common import refresh_all_orders_if_needed


@main.route("/")
@login_required
def monthly_sales():
    all_orders = current_app.config["ALL_ORDERS_CSV"]

    monthly_sales_trend = []
    forecast_data = []

    pie_labels = []
    pie_values = []
    channel_charts = []
    other_channels_labels = []
    other_channels_pct = 0.0

    forecast_includes_current_month_mtd = False
    projection_method = "weekday_weighted"
    current_month_label = None
    current_month_mtd_sales = 0.0
    current_month_projected_sales = 0.0
    current_month_remaining_days = 0
    current_month_days_in_month = 0

    error = None

    try:
        refresh_all_orders_if_needed()

        if not os.path.exists(all_orders):
            raise FileNotFoundError(f"{all_orders} not found. Auto-generation failed.")

        monthly_sales_trend, forecast_data, meta = get_monthly_sales_trend(
            output_file="monthly_sales_trend.csv",
            forecast_periods=6,
            return_forecast=True,
            return_meta=True,
            include_current_month_for_forecast=True,
            projection_method="weekday_weighted",
            weekday_history_months=6,
            orders_csv_path=all_orders,
            utm_source_filter=None,
        )

        forecast_includes_current_month_mtd = bool(
            meta.get("forecast_includes_current_month_mtd", False)
        )
        projection_method = meta.get("projection_method", "weekday_weighted")
        current_month_label = meta.get("current_month_label")

        current_month_mtd_sales = float(meta.get("current_month_mtd_sales", 0.0) or 0.0)
        current_month_projected_sales = float(
            meta.get("current_month_projected_sales", 0.0) or 0.0
        )
        current_month_remaining_days = int(meta.get("current_month_remaining_days", 0) or 0)
        current_month_days_in_month = int(meta.get("current_month_days_in_month", 0) or 0)

        df = pd.read_csv(all_orders)

        required_cols = {"order_date", "total_value", "utm_source", "order_id"}
        missing = required_cols - set(df.columns)

        if missing:
            raise ValueError(
                f"Orders file is missing required columns for pie chart: {sorted(missing)}"
            )

        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
        df = df.dropna(subset=["order_date"]).copy()
        df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

        today_bogota = pd.Timestamp.now(tz="America/Bogota").tz_localize(None).normalize()
        start_current_month = today_bogota.replace(day=1)
        end_prev_month = start_current_month - pd.Timedelta(microseconds=1)

        df = df[df["order_date"] <= end_prev_month].copy()

        df["utm_source"] = (
            df["utm_source"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        df.loc[df["utm_source"] == "", "utm_source"] = "unknown"

        grouped_value = (
            df.groupby("utm_source")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )

        pie_labels = [str(k) for k, v in grouped_value.items() if float(v) > 0]
        pie_values = [float(v) for v in grouped_value.values if float(v) > 0]

        total_sales_value = float(sum(pie_values)) if pie_values else 0.0

        grouped_count = (
            df.groupby("utm_source")["order_id"]
            .count()
            .sort_values(ascending=False)
        )

        min_share_percent = 2.0
        included = []
        excluded = []

        if total_sales_value > 0:
            for channel, value in grouped_value.items():
                value = float(value)

                if value <= 0:
                    continue

                pct = (value / total_sales_value) * 100.0
                count = int(grouped_count.get(channel, 0))

                if str(channel) == "unknown":
                    continue

                item = {
                    "key": str(channel),
                    "pct": pct,
                    "value": value,
                    "count": count,
                }

                if pct >= min_share_percent:
                    included.append(item)
                else:
                    excluded.append(item)

        included.sort(key=lambda x: x["count"], reverse=True)
        excluded.sort(key=lambda x: x["pct"], reverse=True)

        other_channels_labels = [x["key"] for x in excluded]
        other_channels_pct = float(sum(x["pct"] for x in excluded)) if excluded else 0.0

        for item in included:
            channel = item["key"]
            pct = item["pct"]

            ch_trend, ch_forecast, ch_meta = get_monthly_sales_trend(
                output_file=f"monthly_sales_trend_{channel}.csv",
                forecast_periods=6,
                return_forecast=True,
                return_meta=True,
                include_current_month_for_forecast=True,
                projection_method="weekday_weighted",
                weekday_history_months=6,
                orders_csv_path=all_orders,
                utm_source_filter=channel,
            )

            if not ch_trend:
                continue

            safe = "".join(c if c.isalnum() else "_" for c in channel)

            channel_charts.append({
                "key": channel,
                "label": f"{channel.upper()} ({pct:.1f}%)",
                "canvas_id": f"monthlySalesChart_{safe}",
                "trend": ch_trend,
                "forecast": ch_forecast,
                "pct": pct,
                "count": int(item["count"]),
                "forecast_includes_current_month_mtd": bool(
                    ch_meta.get("forecast_includes_current_month_mtd", False)
                ),
                "projection_method": ch_meta.get("projection_method", "weekday_weighted"),
                "current_month_label": ch_meta.get("current_month_label"),
                "current_month_mtd_sales": float(
                    ch_meta.get("current_month_mtd_sales", 0.0) or 0.0
                ),
                "current_month_projected_sales": float(
                    ch_meta.get("current_month_projected_sales", 0.0) or 0.0
                ),
                "current_month_remaining_days": int(
                    ch_meta.get("current_month_remaining_days", 0) or 0
                ),
                "current_month_days_in_month": int(
                    ch_meta.get("current_month_days_in_month", 0) or 0
                ),
            })

    except Exception as e:
        error = str(e)
        current_app.logger.exception("monthly_sales view failed")

    return render_template(
        "monthly_sales.html",
        monthly_sales_trend=monthly_sales_trend,
        forecast_data=forecast_data,
        pie_labels=pie_labels,
        pie_values=pie_values,
        channel_charts=channel_charts,
        other_channels_labels=other_channels_labels,
        other_channels_pct=other_channels_pct,
        forecast_includes_current_month_mtd=forecast_includes_current_month_mtd,
        projection_method=projection_method,
        current_month_label=current_month_label,
        current_month_mtd_sales=current_month_mtd_sales,
        current_month_projected_sales=current_month_projected_sales,
        current_month_remaining_days=current_month_remaining_days,
        current_month_days_in_month=current_month_days_in_month,
        error=error,
    )


@main.route("/monthly_repurchases")
@login_required
def monthly_repurchases_by_month():
    try:
        refresh_all_orders_if_needed()
    except Exception:
        current_app.logger.exception("Failed refreshing all_orders cache")

    all_orders = current_app.config["ALL_ORDERS_CSV"]

    monthly_repurchases_trend = []
    forecast_data = []

    pie_labels = []
    pie_values = []
    channel_charts = []
    other_channels_labels = []
    other_channels_pct = 0.0

    forecast_includes_current_month_mtd = False
    projection_method = "weekday_weighted"
    current_month_label = None
    current_month_mtd_sales = 0.0
    current_month_projected_sales = 0.0
    current_month_remaining_days = 0
    current_month_days_in_month = 0

    error = None

    try:
        if not os.path.exists(all_orders):
            raise FileNotFoundError(f"{all_orders} not found. Please generate all orders first.")

        monthly_repurchases_trend, forecast_data, meta = get_monthly_repurchases_trend(
            output_file="monthly_repurchases_trend.csv",
            forecast_periods=12,
            return_forecast=True,
            return_meta=True,
            include_current_month_for_forecast=True,
            projection_method="weekday_weighted",
            weekday_history_months=6,
            orders_csv_path=all_orders,
            utm_source_filter=None,
        )

        forecast_includes_current_month_mtd = bool(
            meta.get("forecast_includes_current_month_mtd", False)
        )
        projection_method = meta.get("projection_method", "weekday_weighted")
        current_month_label = meta.get("current_month_label")

        current_month_mtd_sales = float(meta.get("current_month_mtd_sales", 0.0) or 0.0)
        current_month_projected_sales = float(
            meta.get("current_month_projected_sales", 0.0) or 0.0
        )
        current_month_remaining_days = int(meta.get("current_month_remaining_days", 0) or 0)
        current_month_days_in_month = int(meta.get("current_month_days_in_month", 0) or 0)

        df = pd.read_csv(all_orders)

        required_cols = {"email", "order_date", "total_value", "utm_source", "order_id"}
        missing = required_cols - set(df.columns)

        if missing:
            raise ValueError(
                f"Orders file is missing required columns for pie chart: {sorted(missing)}"
            )

        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
        df = df.dropna(subset=["order_date", "email"]).copy()
        df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

        today_bogota = pd.Timestamp.now(tz="America/Bogota").tz_localize(None).normalize()
        start_current_month = today_bogota.replace(day=1)
        end_prev_month = start_current_month - pd.Timedelta(microseconds=1)

        df = df[df["order_date"] <= end_prev_month].copy()

        email_counts = df["email"].value_counts(dropna=True)
        repeat_emails = set(email_counts[email_counts > 1].index)

        first_order_dt = df.groupby("email")["order_date"].min()
        df = df.join(first_order_dt, on="email", rsuffix="_first")
        df["is_repurchase"] = (
            df["email"].isin(repeat_emails)
            & (df["order_date"] > df["order_date_first"])
        )

        rep = df[df["is_repurchase"]].copy()

        rep["utm_source"] = (
            rep["utm_source"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        rep.loc[rep["utm_source"] == "", "utm_source"] = "unknown"

        grouped_value = (
            rep.groupby("utm_source")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )

        pie_labels = [str(k) for k, v in grouped_value.items() if float(v) > 0]
        pie_values = [float(v) for v in grouped_value.values if float(v) > 0]

        total_sales_value = float(sum(pie_values)) if pie_values else 0.0

        grouped_count = (
            rep.groupby("utm_source")["order_id"]
            .count()
            .sort_values(ascending=False)
        )

        min_share_percent = 2.0
        included = []
        excluded = []

        if total_sales_value > 0:
            for channel, value in grouped_value.items():
                value = float(value)

                if value <= 0:
                    continue

                pct = (value / total_sales_value) * 100.0
                count = int(grouped_count.get(channel, 0))

                item = {
                    "key": str(channel),
                    "pct": pct,
                    "value": value,
                    "count": count,
                }

                if pct >= min_share_percent:
                    included.append(item)
                else:
                    excluded.append(item)

        included.sort(key=lambda x: x["count"], reverse=True)
        excluded.sort(key=lambda x: x["pct"], reverse=True)

        other_channels_labels = [x["key"] for x in excluded]
        other_channels_pct = float(sum(x["pct"] for x in excluded)) if excluded else 0.0

        for item in included:
            channel = item["key"]
            pct = item["pct"]

            ch_trend, ch_forecast, ch_meta = get_monthly_repurchases_trend(
                output_file=f"monthly_repurchases_trend_{channel}.csv",
                forecast_periods=12,
                return_forecast=True,
                return_meta=True,
                include_current_month_for_forecast=True,
                projection_method="weekday_weighted",
                weekday_history_months=6,
                orders_csv_path=all_orders,
                utm_source_filter=channel,
            )

            if not ch_trend:
                continue

            safe = "".join(c if c.isalnum() else "_" for c in channel)

            channel_charts.append({
                "key": channel,
                "label": f"{channel.upper()} ({pct:.1f}%)",
                "canvas_id": f"repurchaseChart_{safe}",
                "trend": ch_trend,
                "forecast": ch_forecast,
                "pct": pct,
                "count": int(item["count"]),
                "forecast_includes_current_month_mtd": bool(
                    ch_meta.get("forecast_includes_current_month_mtd", False)
                ),
                "projection_method": ch_meta.get("projection_method", "weekday_weighted"),
                "current_month_label": ch_meta.get("current_month_label"),
                "current_month_mtd_sales": float(
                    ch_meta.get("current_month_mtd_sales", 0.0) or 0.0
                ),
                "current_month_projected_sales": float(
                    ch_meta.get("current_month_projected_sales", 0.0) or 0.0
                ),
                "current_month_remaining_days": int(
                    ch_meta.get("current_month_remaining_days", 0) or 0
                ),
                "current_month_days_in_month": int(
                    ch_meta.get("current_month_days_in_month", 0) or 0
                ),
            })

    except Exception as e:
        error = str(e)
        current_app.logger.exception("monthly_repurchases_by_month view failed")

    return render_template(
        "monthly_repurchases.html",
        monthly_repurchases_trend=monthly_repurchases_trend,
        forecast_data=forecast_data,
        pie_labels=pie_labels,
        pie_values=pie_values,
        channel_charts=channel_charts,
        other_channels_labels=other_channels_labels,
        other_channels_pct=other_channels_pct,
        forecast_includes_current_month_mtd=forecast_includes_current_month_mtd,
        projection_method=projection_method,
        current_month_label=current_month_label,
        current_month_mtd_sales=current_month_mtd_sales,
        current_month_projected_sales=current_month_projected_sales,
        current_month_remaining_days=current_month_remaining_days,
        current_month_days_in_month=current_month_days_in_month,
        error=error,
    )