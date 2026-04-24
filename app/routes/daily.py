# app/routes/daily.py
import os

import pandas as pd
from flask import current_app, render_template
from flask_login import login_required

from app.models import Option
from app.services.daily_repurchases import get_daily_repurchases_trend
from app.services.daily_sales import build_daily_sales_dashboard_context

from . import main
from .common import refresh_all_orders_if_needed


def build_top_n_city_pie(city_series, top_n=20, other_label="Other cities"):
    city_series = city_series.sort_values(ascending=False)

    top = city_series.head(top_n)
    rest = city_series.iloc[top_n:]

    labels = [str(k) for k, v in top.items() if float(v) > 0]
    values = [float(v) for v in top.values if float(v) > 0]

    other_value = float(rest.sum()) if not rest.empty else 0.0

    if other_value > 0:
        labels.append(other_label)
        values.append(other_value)

    return labels, values


def build_3hour_bucket_pie(df_in, value_col="total_value", dt_col="order_date"):
    if df_in is None or df_in.empty:
        return [], []

    if dt_col not in df_in.columns or value_col not in df_in.columns:
        return [], []

    tmp = df_in[[dt_col, value_col]].copy()
    tmp = tmp.dropna(subset=[dt_col]).copy()

    if tmp.empty:
        return [], []

    tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce").fillna(0.0)
    tmp = tmp[tmp[value_col] > 0].copy()

    if tmp.empty:
        return [], []

    tmp[dt_col] = pd.to_datetime(tmp[dt_col], errors="coerce")
    tmp = tmp.dropna(subset=[dt_col]).copy()

    if tmp.empty:
        return [], []

    hours = tmp[dt_col].dt.hour.fillna(0).astype(int)
    tmp["_bucket_start"] = (hours // 3) * 3

    grouped = tmp.groupby("_bucket_start")[value_col].sum()

    bucket_order = [0, 3, 6, 9, 12, 15, 18, 21]

    labels = []
    values = []

    for bucket in bucket_order:
        value = float(grouped.get(bucket, 0.0))

        if value > 0:
            labels.append(f"{bucket:02d}-{bucket + 3:02d}" if bucket < 21 else "21-24")
            values.append(value)

    return labels, values


@main.route("/daily_sales")
@login_required
def daily_sales():
    try:
        refresh_all_orders_if_needed()
    except Exception:
        current_app.logger.exception("Failed refreshing all_orders cache")

    input_file = "data/daily_sales_orders.csv"

    date_range = None
    start_date = None
    end_date = None

    try:
        opt = Option.query

        rec = opt.filter_by(meta_key="date_range_daily_sales_orders.csv").first()
        date_range = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key="start_date_daily_sales_orders.csv").first()
        start_date = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key="end_date_daily_sales_orders.csv").first()
        end_date = rec.meta_value if rec else None

    except Exception:
        current_app.logger.exception("Reading date range options failed (daily_sales)")

    context = build_daily_sales_dashboard_context(
        input_file=input_file,
        forecast_periods=30,
        min_channel_share_percent=2.0,
    )

    context.update({
        "date_range": date_range,
        "start_date": start_date,
        "end_date": end_date,
    })

    return render_template("daily_sales.html", **context)


@main.route("/daily_repurchases")
@login_required
def daily_repurchases():
    try:
        refresh_all_orders_if_needed()
    except Exception:
        current_app.logger.exception("Failed refreshing all_orders cache")

    input_file = current_app.config["ALL_ORDERS_CSV"]

    daily_repurchases_trend = []
    forecast_data = []

    pie_labels = []
    pie_values = []
    channel_charts = []

    other_channels_labels = []
    other_channels_pct = 0.0

    gender_labels_total = []
    gender_values_total = []
    gender_pies_by_channel = {}

    city_labels_total = []
    city_values_total = []
    city_pies_by_channel = {}

    hour_labels_total = []
    hour_values_total = []
    hour_pies_by_channel = {}

    total_repurchases_sales_cop = 0.0

    error = None
    date_range = start_date = end_date = None

    try:
        opt = Option.query

        rec = opt.filter_by(meta_key="date_range_daily_repurchases_orders.csv").first()
        date_range = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key="start_date_daily_repurchases_orders.csv").first()
        start_date = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key="end_date_daily_repurchases_orders.csv").first()
        end_date = rec.meta_value if rec else None

    except Exception:
        current_app.logger.exception("Reading date range options failed (daily_repurchases)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Please generate all orders first."
        return render_template(
            "daily_repurchases.html",
            daily_repurchases_trend=daily_repurchases_trend,
            forecast_data=forecast_data,
            pie_labels=pie_labels,
            pie_values=pie_values,
            channel_charts=channel_charts,
            other_channels_labels=other_channels_labels,
            other_channels_pct=other_channels_pct,
            gender_labels_total=gender_labels_total,
            gender_values_total=gender_values_total,
            gender_pies_by_channel=gender_pies_by_channel,
            total_repurchases_sales_cop=total_repurchases_sales_cop,
            city_labels_total=city_labels_total,
            city_values_total=city_values_total,
            city_pies_by_channel=city_pies_by_channel,
            hour_labels_total=hour_labels_total,
            hour_values_total=hour_values_total,
            hour_pies_by_channel=hour_pies_by_channel,
            error=error,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
        )

    try:
        daily_repurchases_trend, forecast_data = get_daily_repurchases_trend(
            output_file="repurchases_by_day_trend.csv",
            forecast_periods=30,
            return_forecast=True,
            orders_csv_path=input_file,
            start_date=start_date,
            end_date=end_date,
            utm_source_filter=None,
        )

        df = pd.read_csv(input_file)

        required_cols = {"email", "order_date", "total_value", "utm_source", "order_id"}
        missing = required_cols - set(df.columns)

        if missing:
            raise ValueError(
                f"Orders file is missing required columns for repurchases charts: {sorted(missing)}"
            )

        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
        df = df.dropna(subset=["order_date", "email"]).copy()
        df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

        if "city" in df.columns:
            df["city"] = df["city"].fillna("unknown").astype(str).str.strip()
            df.loc[df["city"] == "", "city"] = "unknown"
        else:
            df["city"] = "unknown"

        if "gender" in df.columns:
            df["gender"] = df["gender"].fillna("unknown").astype(str).str.strip().str.lower()
            df.loc[df["gender"] == "", "gender"] = "unknown"
        else:
            df["gender"] = "unknown"

        today_bogota = pd.Timestamp.now(tz="America/Bogota").tz_localize(None).normalize()
        end_yesterday = today_bogota - pd.Timedelta(microseconds=1)
        df = df[df["order_date"] <= end_yesterday].copy()

        if df.empty:
            return render_template(
                "daily_repurchases.html",
                daily_repurchases_trend=daily_repurchases_trend,
                forecast_data=forecast_data,
                pie_labels=pie_labels,
                pie_values=pie_values,
                channel_charts=channel_charts,
                other_channels_labels=other_channels_labels,
                other_channels_pct=other_channels_pct,
                gender_labels_total=gender_labels_total,
                gender_values_total=gender_values_total,
                gender_pies_by_channel=gender_pies_by_channel,
                total_repurchases_sales_cop=total_repurchases_sales_cop,
                city_labels_total=city_labels_total,
                city_values_total=city_values_total,
                city_pies_by_channel=city_pies_by_channel,
                hour_labels_total=hour_labels_total,
                hour_values_total=hour_values_total,
                hour_pies_by_channel=hour_pies_by_channel,
                error=None,
                date_range=date_range,
                start_date=start_date,
                end_date=end_date,
            )

        email_counts = df["email"].value_counts(dropna=True)
        repeat_emails = set(email_counts[email_counts > 1].index)

        first_order_dt = df.groupby("email")["order_date"].min()
        df = df.join(first_order_dt, on="email", rsuffix="_first")

        df["is_repurchase"] = (
            df["email"].isin(repeat_emails)
            & (df["order_date"] > df["order_date_first"])
        )

        if start_date and end_date:
            try:
                start_dt = pd.to_datetime(start_date, format="%d/%m/%Y", errors="raise")
                end_dt = (
                    pd.to_datetime(end_date, format="%d/%m/%Y", errors="raise")
                    + pd.Timedelta(days=1)
                    - pd.Timedelta(microseconds=1)
                )
                df = df[(df["order_date"] >= start_dt) & (df["order_date"] <= end_dt)].copy()
            except Exception:
                current_app.logger.warning(
                    "Invalid custom date range provided: start_date=%s end_date=%s",
                    start_date,
                    end_date,
                )

        if df.empty:
            return render_template(
                "daily_repurchases.html",
                daily_repurchases_trend=daily_repurchases_trend,
                forecast_data=forecast_data,
                pie_labels=pie_labels,
                pie_values=pie_values,
                channel_charts=channel_charts,
                other_channels_labels=other_channels_labels,
                other_channels_pct=other_channels_pct,
                gender_labels_total=gender_labels_total,
                gender_values_total=gender_values_total,
                gender_pies_by_channel=gender_pies_by_channel,
                total_repurchases_sales_cop=total_repurchases_sales_cop,
                city_labels_total=city_labels_total,
                city_values_total=city_values_total,
                city_pies_by_channel=city_pies_by_channel,
                hour_labels_total=hour_labels_total,
                hour_values_total=hour_values_total,
                hour_pies_by_channel=hour_pies_by_channel,
                error=None,
                date_range=date_range,
                start_date=start_date,
                end_date=end_date,
            )

        norm = df["utm_source"].fillna("").astype(str).str.strip().str.lower()
        norm = norm.replace({"nan": "", "none": ""})
        norm = norm.where(norm != "", "undefined")
        df["_utm_source_norm"] = norm

        rep_df = df[df["is_repurchase"]].copy()

        total_repurchases_sales_cop = (
            float(rep_df["total_value"].sum()) if not rep_df.empty else 0.0
        )

        gender_group_total = (
            rep_df.groupby("gender")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )
        gender_labels_total = [
            str(k) for k, v in gender_group_total.items() if float(v) > 0
        ]
        gender_values_total = [
            float(v) for v in gender_group_total.values if float(v) > 0
        ]

        grouped = (
            rep_df.groupby("_utm_source_norm")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )

        pie_labels = [str(k) for k, v in grouped.items() if float(v) > 0]
        pie_values = [float(v) for v in grouped.values if float(v) > 0]

        total_rep_sales = float(sum(pie_values)) if pie_values else 0.0
        min_share_percent = 2.0

        included = []
        excluded = []

        if total_rep_sales > 0:
            for channel, value in grouped.items():
                value = float(value)

                if value <= 0:
                    continue

                pct = (value / total_rep_sales) * 100.0
                channel_str = str(channel)

                item = {
                    "key": channel_str,
                    "pct": pct,
                    "value": value,
                }

                if pct >= min_share_percent:
                    included.append(item)
                else:
                    excluded.append(item)

        included.sort(key=lambda x: x["pct"], reverse=True)
        excluded.sort(key=lambda x: x["pct"], reverse=True)

        other_channels_labels = [x["key"] for x in excluded]
        other_channels_pct = float(sum(x["pct"] for x in excluded)) if excluded else 0.0

        city_group_total = rep_df.groupby("city")["total_value"].sum()
        city_labels_total, city_values_total = build_top_n_city_pie(
            city_group_total,
            top_n=20,
            other_label="Other cities",
        )

        hour_labels_total, hour_values_total = build_3hour_bucket_pie(rep_df)

        for item in included:
            channel = item["key"]
            pct = item["pct"]

            trend_rows, fc_rows = get_daily_repurchases_trend(
                output_file=f"repurchases_by_day_{channel}_trend.csv",
                forecast_periods=30,
                return_forecast=True,
                orders_csv_path=input_file,
                start_date=start_date,
                end_date=end_date,
                utm_source_filter=channel,
            )

            if not trend_rows:
                continue

            rep_ch = rep_df[rep_df["_utm_source_norm"] == channel].copy()

            gender_group = (
                rep_ch.groupby("gender")["total_value"]
                .sum()
                .sort_values(ascending=False)
            )

            gender_pies_by_channel[channel] = {
                "labels": [str(k) for k, v in gender_group.items() if float(v) > 0],
                "values": [float(v) for v in gender_group.values if float(v) > 0],
            }

            city_group = rep_ch.groupby("city")["total_value"].sum()
            ch_labels, ch_values = build_top_n_city_pie(
                city_group,
                top_n=20,
                other_label="Other cities",
            )
            city_pies_by_channel[channel] = {
                "labels": ch_labels,
                "values": ch_values,
            }

            h_labels, h_values = build_3hour_bucket_pie(rep_ch)
            hour_pies_by_channel[channel] = {
                "labels": h_labels,
                "values": h_values,
            }

            safe = "".join(c if c.isalnum() else "_" for c in channel)

            channel_charts.append({
                "key": channel,
                "label": f"{channel.upper()} ({pct:.1f}%)",
                "canvas_id": f"repurchaseDailyChart_{safe}",
                "gender_canvas_id": f"repurchaseGenderPie_{safe}",
                "city_canvas_id": f"repurchaseCityPie_{safe}",
                "hour_canvas_id": f"repurchaseHourPie_{safe}",
                "trend": trend_rows,
                "forecast": fc_rows,
                "pct": pct,
            })

    except Exception as e:
        error = str(e)
        current_app.logger.exception("daily_repurchases view failed")

    return render_template(
        "daily_repurchases.html",
        daily_repurchases_trend=daily_repurchases_trend,
        forecast_data=forecast_data,
        pie_labels=pie_labels,
        pie_values=pie_values,
        channel_charts=channel_charts,
        other_channels_labels=other_channels_labels,
        other_channels_pct=other_channels_pct,
        gender_labels_total=gender_labels_total,
        gender_values_total=gender_values_total,
        gender_pies_by_channel=gender_pies_by_channel,
        total_repurchases_sales_cop=total_repurchases_sales_cop,
        city_labels_total=city_labels_total,
        city_values_total=city_values_total,
        city_pies_by_channel=city_pies_by_channel,
        hour_labels_total=hour_labels_total,
        hour_values_total=hour_values_total,
        hour_pies_by_channel=hour_pies_by_channel,
        error=error,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
    )