#routes.py
from flask import Blueprint, request, jsonify, render_template, flash, redirect, url_for, current_app 
import os
import requests
import csv  # Ensure the csv module is imported
import traceback

import time

from .models import User, Option
from . import db
from .sqlite_db import get_db_connection

from datetime import date
from app.services.daily import ( 
    get_daily_sales_trend,
    get_future_daily_sales_projections  # <-- new import
)
from app.services.monthly import (
    merge_tables_by_percentage, 
    get_monthly_sales_repurchases_trend,
    consolidate_monthly_sales, 
    get_monthly_sales_undefined_trend, 
    get_monthly_sales_forecast, 
    get_consolidated_monthly_sales, 
    recalculate_data, 
    get_monthly_sales_paid_trend,
)

from app.services.get_data import fetch_orders_and_write_csv
from app.services.ads import get_daily_conversions,get_daily_conversions_by_campaign,forecast_campaigns, get_wp_campaign_trend, forecast_ads
import logging
from datetime import datetime
from app.services.repurchases import (
    find_customers_with_multiple_purchases,
    process_repeated_orders,
    get_monthly_repurchases_trend,
    print_customers_with_multiple_purchases,
    get_daily_repurchases,
    get_monthly_repurchases_forecast
)
from app.services.rankings import (
    get_top_cities_by_gender,
    get_top_hours_by_gender,
    get_top_days_of_the_week,
    get_top_days_of_month_by_gender,
    get_top_10_months_by_sales,
    get_top_twenty_days_by_sales,
    get_top_ten_mondays_by_sales,
    get_top_ten_tuesdays_by_sales,
    get_top_ten_wednesdays_by_sales,
    get_top_ten_thursdays_by_sales,
    get_top_ten_fridays_by_sales,
    get_top_ten_saturdays_by_sales,
    get_top_ten_sundays_by_sales,
    get_top_ten_utm_campaigns,
    get_utm_answer_ranking,
    get_top_twenty_days_by_undefined_campaign,
    get_top_ten_utm_content_by_sales,
    get_top_ten_utm_source_by_sales,
    get_top_ten_utm_medium_by_sales,
    get_top_ten_utm_term_by_sales,
    get_utm_content_ranking_by_gender,
    get_order_percentage_by_city
)
from flask_login import login_user, logout_user, login_required, current_user



from app.services.performance import (
    get_weekly_order_stats
)

from app.services.repurchases import (
    find_customers_with_multiple_purchases,
    process_repeated_orders,
    print_customers_with_multiple_purchases,
    get_daily_repurchases,
    get_monthly_repurchases_forecast,
)

from app.services.top_cities import get_top_cities_daily_trend_with_forecast

from app.services.monthly_sales import get_monthly_sales_trend  

from app.services.monthly_repurchases import get_monthly_repurchases_trend

from app.services.daily_sales import get_daily_sales_trend as get_daily_sales_trend_simple

from app.services.daily_repurchases import get_daily_repurchases_trend

import os
from flask import current_app, render_template, request, send_from_directory, abort
from werkzeug.utils import secure_filename


CACHE_TTL_SECONDS = 60 * 60 * 24  # 1 day
ALL_ORDERS_CACHE_FILE = "data/.all_orders_cache_ts"

main = Blueprint(
    "main",
    __name__,
    template_folder="templates",
    static_folder="../static"  # Adjust path relative to routes.py
)

@main.app_errorhandler(500)
def internal_error(error):
    return render_template(
        "error.html",
        error=str(error),
        traceback=traceback.format_exc()
    ), 500


@main.route("/generate_all_orders", methods=["POST"])
@login_required
def get_all_orders():
    try:
        output_csv = generate_all_orders_csv()

        return jsonify({
            "status": "success",
            "message": "all_orders.csv generated successfully",
            "output": output_csv,
        }), 200

    except Exception as e:
        current_app.logger.exception("Failed to generate all_orders.csv")

        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


def should_refresh_all_orders() -> bool:
    """
    Returns True if all_orders.csv should be refreshed:
    - file missing
    - file empty
    - cache timestamp missing/corrupt
    - cache expired
    """
    csv_path = current_app.config["ALL_ORDERS_CSV"]
    cache_file = current_app.config["ALL_ORDERS_CACHE_FILE"]

    if not os.path.exists(csv_path):
        current_app.logger.info("all_orders.csv missing; refresh required")
        return True

    try:
        if os.path.getsize(csv_path) == 0:
            current_app.logger.info("all_orders.csv is empty; refresh required")
            return True
    except OSError:
        current_app.logger.info("Could not stat all_orders.csv; refresh required")
        return True

    if not os.path.exists(cache_file):
        current_app.logger.info("all_orders cache file missing; refresh required")
        return True

    try:
        with open(cache_file, "r") as f:
            last_ts = float(f.read().strip())
    except Exception:
        current_app.logger.info("all_orders cache timestamp invalid; refresh required")
        return True

    expired = (time.time() - last_ts) > CACHE_TTL_SECONDS
    if expired:
        current_app.logger.info("all_orders cache expired; refresh required")

    return expired


def touch_all_orders_cache():
    """
    Updates the cache timestamp file.
    """
    cache_file = current_app.config["ALL_ORDERS_CACHE_FILE"]
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)

    with open(cache_file, "w") as f:
        f.write(str(time.time()))

def build_orders_csv(
    *,
    file_name: str,
    send_date_params: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """
    Shared helper used by working data routes and all_orders generation.
    Returns the absolute path to the generated CSV.
    """
    project_root = current_app.config["PROJECT_ROOT"]
    output_csv = os.path.join(current_app.config["DATA_DIR"], file_name)

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    orders_url = get_option_value("orders_url")
    api_key = get_option_value("api_key")

    if not orders_url:
        raise ValueError("Missing 'orders_url' in options table")

    if not api_key:
        raise ValueError("Missing 'api_key' in options table")

    current_app.logger.info("Generating %s at %s", file_name, output_csv)
    current_app.logger.info("Using orders API: %s", orders_url)

    kwargs = {
        "orders_url": orders_url,
        "api_key": api_key,
        "file_name": file_name,
        "cwd": project_root,
        "timeout": 120,
    }

    if send_date_params and start_date and end_date:
        kwargs["start_date"] = start_date
        kwargs["end_date"] = end_date

    ok, payload = fetch_orders_and_write_csv(**kwargs)

    if not ok:
        raise RuntimeError(payload.get("message", f"Unknown error generating {file_name}"))

    if not os.path.exists(output_csv):
        raise FileNotFoundError(f"CSV was not created: {output_csv}")

    if os.path.getsize(output_csv) == 0:
        raise ValueError(f"CSV was created but is empty: {output_csv}")

    current_app.logger.info("Successfully generated %s", file_name)
    return output_csv

def get_option_value(meta_key: str, default=None):
    row = Option.query.filter_by(meta_key=meta_key).first()
    return row.meta_value if row and row.meta_value is not None else default


def generate_all_orders_csv() -> str:
    """
    Generates all_orders.csv using the same API flow as the working /get_data route.
    """
    output_csv = build_orders_csv(
        file_name="all_orders.csv",
        send_date_params=False,
    )

    touch_all_orders_cache()
    return output_csv

def refresh_all_orders_if_needed():
    """
    Generates all_orders.csv only if cache is expired or file is missing.
    Raises FileNotFoundError if generation fails.
    """
    csv_path = current_app.config["ALL_ORDERS_CSV"]

    if not should_refresh_all_orders():
        current_app.logger.info("Using cached all_orders.csv")
        return

    current_app.logger.info("Refreshing all_orders.csv from API")

    try:
        generate_all_orders_csv()
    except Exception as e:
        current_app.logger.exception("Failed to refresh all_orders.csv")
        raise FileNotFoundError(
            f"Could not generate required file: {csv_path}. Reason: {e}"
        ) from e

    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        current_app.logger.info("all_orders.csv refreshed and cached")
        return

    current_app.logger.error("Failed to refresh all_orders.csv")
    raise FileNotFoundError(f"Could not generate required file: {csv_path}")



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

        # Total trend + forecast with weekday-weighted projection
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

        forecast_includes_current_month_mtd = bool(meta.get("forecast_includes_current_month_mtd", False))
        projection_method = meta.get("projection_method", "weekday_weighted")
        current_month_label = meta.get("current_month_label")

        current_month_mtd_sales = float(meta.get("current_month_mtd_sales", 0.0) or 0.0)
        current_month_projected_sales = float(meta.get("current_month_projected_sales", 0.0) or 0.0)
        current_month_remaining_days = int(meta.get("current_month_remaining_days", 0) or 0)
        current_month_days_in_month = int(meta.get("current_month_days_in_month", 0) or 0)

        import pandas as pd

        df = pd.read_csv(all_orders)
        required_cols = {"order_date", "total_value", "utm_source", "order_id"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Orders file is missing required columns for pie chart: {sorted(missing)}")

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

        MIN_SHARE_PERCENT = 2.0
        included = []
        excluded = []

        if total_sales_value > 0:
            for ch, val in grouped_value.items():
                val = float(val)
                if val <= 0:
                    continue

                pct = (val / total_sales_value) * 100.0
                cnt = int(grouped_count.get(ch, 0))

                if str(ch) == "unknown":
                    continue

                item = {"key": str(ch), "pct": pct, "value": val, "count": cnt}
                if pct >= MIN_SHARE_PERCENT:
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
                "forecast_includes_current_month_mtd": bool(ch_meta.get("forecast_includes_current_month_mtd", False)),
                "projection_method": ch_meta.get("projection_method", "weekday_weighted"),
                "current_month_label": ch_meta.get("current_month_label"),
                "current_month_mtd_sales": float(ch_meta.get("current_month_mtd_sales", 0.0) or 0.0),
                "current_month_projected_sales": float(ch_meta.get("current_month_projected_sales", 0.0) or 0.0),
                "current_month_remaining_days": int(ch_meta.get("current_month_remaining_days", 0) or 0),
                "current_month_days_in_month": int(ch_meta.get("current_month_days_in_month", 0) or 0),
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

@main.route("/daily_sales")
@login_required
def daily_sales():
    try:
        refresh_all_orders_if_needed()
    except Exception:
        current_app.logger.exception("Failed refreshing all_orders cache")

    input_file = "data/daily_sales_orders.csv"

    daily_sales_trend = []
    forecast_data = []
    pie_labels = []
    pie_values = []
    channel_charts = []

    # Excluded small channels summary
    other_channels_labels = []
    other_channels_pct = 0.0

    # Gender pies
    gender_labels_total = []
    gender_values_total = []
    gender_pies_by_channel = {}  # channel -> {"labels":[...], "values":[...]}

    # City pies (Top 20 + Other)
    city_labels_total = []
    city_values_total = []
    city_pies_by_channel = {}  # channel -> {"labels":[...], "values":[...]}

    # Time pies (3-hour buckets)
    time_labels_total = []
    time_values_total = []
    time_pies_by_channel = {}  # channel -> {"labels":[...], "values":[...]}

    error = None
    date_range = start_date = end_date = None

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

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Use the date selector above to fetch data first."
        return render_template(
            "daily_sales.html",
            daily_sales_trend=daily_sales_trend,
            forecast_data=forecast_data,
            pie_labels=pie_labels,
            pie_values=pie_values,
            channel_charts=channel_charts,
            other_channels_labels=other_channels_labels,
            other_channels_pct=other_channels_pct,
            gender_labels_total=gender_labels_total,
            gender_values_total=gender_values_total,
            gender_pies_by_channel=gender_pies_by_channel,
            city_labels_total=city_labels_total,
            city_values_total=city_values_total,
            city_pies_by_channel=city_pies_by_channel,
            time_labels_total=time_labels_total,
            time_values_total=time_values_total,
            time_pies_by_channel=time_pies_by_channel,
            error=error,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
        )

    def _top_n_with_other(series, n=20, other_label="Other"):
        """
        series: pandas Series indexed by label, values numeric (sales).
        Returns (labels, values) for top N labels plus an aggregated 'Other'.
        """
        if series is None or len(series) == 0:
            return [], []

        s = series.copy()
        s = s[s > 0].sort_values(ascending=False)

        if len(s) == 0:
            return [], []

        top = s.head(n)
        rest_sum = float(s.iloc[n:].sum()) if len(s) > n else 0.0

        labels = [str(x) for x in top.index.tolist()]
        values = [float(x) for x in top.values.tolist()]

        if rest_sum > 0:
            labels.append(other_label)
            values.append(float(rest_sum))

        return labels, values

    def _time_bucket_label(hour: int) -> str:
        """
        Returns 3-hour bucket label for a given hour [0..23].
        """
        h = int(hour) if hour is not None else 0
        start = (h // 3) * 3
        end = start + 3
        return f"{start:02d}-{end:02d}" if end < 24 else "21-24"

    def _time_pie_from_df(df_in):
        """
        Builds labels/values for 3-hour buckets across the day in fixed order.

        IMPORTANT:
        - order_date in data/daily_sales_orders.csv is already GMT-5 Bogota local time.
        - We treat it as naive local time and DO NOT tz_localize/tz_convert anything.
        - If it ever arrives tz-aware (unexpected), we strip tz to avoid shifting hours.
        """
        if df_in is None or df_in.empty:
            return [], []

        df_tmp = df_in.copy()
        if "order_date" not in df_tmp.columns:
            return [], []

        df_tmp["order_date"] = pd.to_datetime(df_tmp["order_date"], errors="coerce")
        df_tmp = df_tmp.dropna(subset=["order_date"]).copy()

        # Safety: if tz-aware sneaks in, strip tz without shifting (keeps wall-clock time)
        try:
            if getattr(df_tmp["order_date"].dt, "tz", None) is not None:
                df_tmp["order_date"] = df_tmp["order_date"].dt.tz_localize(None)
        except Exception:
            pass

        df_tmp["hour"] = df_tmp["order_date"].dt.hour
        df_tmp["time_bucket"] = df_tmp["hour"].apply(_time_bucket_label)

        grouped = df_tmp.groupby("time_bucket")["total_value"].sum()

        bucket_order = ["00-03", "03-06", "06-09", "09-12", "12-15", "15-18", "18-21", "21-24"]

        labels = []
        values = []
        for b in bucket_order:
            v = float(grouped.get(b, 0.0))
            if v > 0:
                labels.append(b)
                values.append(v)

        return labels, values

    try:
        import pandas as pd

        # Total sales chart (always)
        daily_sales_trend, forecast_data = get_daily_sales_trend_simple(
            output_file="daily_sales_trend.csv",
            forecast_periods=30,
            return_forecast=True,
            orders_csv_path=input_file,
            utm_source_filter=None,
        )

        df = pd.read_csv(input_file)

        required_cols = {"order_date", "total_value", "utm_source"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Orders file is missing required columns for charts: {sorted(missing)}")

        # order_date is already Bogota local time in CSV (naive). Keep it naive.
        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
        df = df.dropna(subset=["order_date"]).copy()
        df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

        # Normalize utm_source
        df["utm_source"] = (
            df["utm_source"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        df.loc[df["utm_source"] == "", "utm_source"] = "unknown"

        # Normalize gender
        if "gender" in df.columns:
            df["gender"] = (
                df["gender"]
                .fillna("unknown")
                .astype(str)
                .str.strip()
                .str.lower()
            )
            df.loc[df["gender"] == "", "gender"] = "unknown"
        else:
            df["gender"] = "unknown"

        # Normalize city
        if "city" in df.columns:
            df["city"] = (
                df["city"]
                .fillna("unknown")
                .astype(str)
                .str.strip()
            )
            df.loc[df["city"] == "", "city"] = "unknown"
        else:
            df["city"] = "unknown"

        # ----------------------
        # UTM SOURCE PIE (sales)
        # ----------------------
        grouped = (
            df.groupby("utm_source")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )

        pie_labels = [str(k) for k, v in grouped.items() if float(v) > 0]
        pie_values = [float(v) for v in grouped.values if float(v) > 0]

        total_sales = float(grouped.sum()) if len(grouped) else 0.0
        MIN_SHARE_PERCENT = 2.0

        included = []
        excluded = []

        if total_sales > 0:
            for ch, val in grouped.items():
                val = float(val)
                if val <= 0:
                    continue

                pct = (val / total_sales) * 100.0
                ch_str = str(ch)

                # we do not chart unknown, and we do not count it in "Other channels (<2%)"
                if ch_str == "unknown":
                    continue

                if pct >= MIN_SHARE_PERCENT:
                    included.append({"key": ch_str, "pct": pct, "value": val})
                else:
                    excluded.append({"key": ch_str, "pct": pct, "value": val})

        included.sort(key=lambda x: x["pct"], reverse=True)
        excluded.sort(key=lambda x: x["pct"], reverse=True)

        other_channels_labels = [x["key"] for x in excluded]
        other_channels_pct = float(sum(x["pct"] for x in excluded)) if excluded else 0.0

        # -----------------------------
        # TOTAL GENDER PIE (sales share)
        # -----------------------------
        gender_group_total = (
            df.groupby("gender")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )
        gender_labels_total = [str(k) for k, v in gender_group_total.items() if float(v) > 0]
        gender_values_total = [float(v) for v in gender_group_total.values if float(v) > 0]

        # ---------------------------
        # TOTAL CITY PIE (Top 20 + Other)
        # ---------------------------
        city_group_total = (
            df.groupby("city")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )
        city_labels_total, city_values_total = _top_n_with_other(city_group_total, n=20, other_label="Other")

        # ---------------------------
        # TOTAL TIME PIE (3-hour buckets)
        # ---------------------------
        time_labels_total, time_values_total = _time_pie_from_df(df)

        # -----------------------------------------
        # CHANNEL CHARTS + GENDER/CITY/TIME PIE PER CH
        # -----------------------------------------
        for item in included:
            channel = item["key"]
            pct = item["pct"]

            trend_rows, forecast_rows = get_daily_sales_trend_simple(
                output_file=f"daily_sales_trend_{channel}.csv",
                forecast_periods=30,
                return_forecast=True,
                orders_csv_path=input_file,
                utm_source_filter=channel,
            )

            if not trend_rows:
                continue

            df_ch = df[df["utm_source"] == channel].copy()

            # Gender pie per channel
            gender_group_ch = (
                df_ch.groupby("gender")["total_value"]
                .sum()
                .sort_values(ascending=False)
            )
            g_labels = [str(k) for k, v in gender_group_ch.items() if float(v) > 0]
            g_values = [float(v) for v in gender_group_ch.values if float(v) > 0]
            gender_pies_by_channel[channel] = {"labels": g_labels, "values": g_values}

            # City pie per channel (Top 20 + Other)
            city_group_ch = (
                df_ch.groupby("city")["total_value"]
                .sum()
                .sort_values(ascending=False)
            )
            c_labels, c_values = _top_n_with_other(city_group_ch, n=20, other_label="Other")
            city_pies_by_channel[channel] = {"labels": c_labels, "values": c_values}

            # Time pie per channel (3-hour buckets)
            t_labels, t_values = _time_pie_from_df(df_ch)
            time_pies_by_channel[channel] = {"labels": t_labels, "values": t_values}

            safe = "".join(c if c.isalnum() else "_" for c in channel)
            channel_charts.append({
                "key": channel,
                "label": f"{channel.upper()} ({pct:.1f}%)",
                "canvas_id": f"dailySalesChart_{safe}",
                "gender_canvas_id": f"genderPie_{safe}",
                "city_canvas_id": f"cityPie_{safe}",
                "time_canvas_id": f"timePie_{safe}",
                "trend": trend_rows,
                "forecast": forecast_rows,
                "pct": pct,
            })

    except Exception as e:
        error = str(e)
        current_app.logger.exception("daily_sales view failed")

    return render_template(
        "daily_sales.html",
        daily_sales_trend=daily_sales_trend,
        forecast_data=forecast_data,
        pie_labels=pie_labels,
        pie_values=pie_values,
        channel_charts=channel_charts,
        other_channels_labels=other_channels_labels,
        other_channels_pct=other_channels_pct,
        gender_labels_total=gender_labels_total,
        gender_values_total=gender_values_total,
        gender_pies_by_channel=gender_pies_by_channel,
        city_labels_total=city_labels_total,
        city_values_total=city_values_total,
        city_pies_by_channel=city_pies_by_channel,
        time_labels_total=time_labels_total,
        time_values_total=time_values_total,
        time_pies_by_channel=time_pies_by_channel,
        error=error,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
    )


@main.route("/top_cities")
@login_required
def top_cities():
    input_file = "data/top_cities.csv"

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    date_range = start_date = end_date = None
    error = None

    # --- NEW: read top_number from querystring, default 10 ---
    allowed_top = {10, 20, 30, 50}
    try:
        top_n = int(request.args.get("top_number", 10))
    except (TypeError, ValueError):
        top_n = 10
    if top_n not in allowed_top:
        top_n = 10

    try:
        date_range_opt = Option.query.filter_by(meta_key="date_range_top_cities.csv").first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(meta_key="start_date_top_cities.csv").first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(meta_key="end_date_top_cities.csv").first()
        end_date = end_date_opt.meta_value if end_date_opt else None
    except Exception:
        current_app.logger.exception("Reading date range options failed")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)
        return render_template(
            "top_cities.html",
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            error=error,
            top_number=top_n,  # NEW
        )

    try:
        top_cities_list, cities_daily_trend, cities_forecast_trend, city_totals_rows = (
            get_top_cities_daily_trend_with_forecast(
                input_file,
                top_n=top_n,           # NEW: use query value
                forecast_periods=14,
            )
        )

        # Build pie: Top N + Others
        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(c, 0.0) for c in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(c, 0.0) for c in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities view failed")

    return render_template(
        "top_cities.html",
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,  # NEW
    )

# app/routes.py

# app/routes.py (only the /top_cities_female route)

@main.route("/top_cities_female")
@login_required
def top_cities_female():
    import os
    from flask import current_app, render_template, request

    from .models import Option
    from .services.top_cities_gender import (
        get_top_cities_gender_daily_trend_with_forecast,
    )

    input_file = "data/top_cities_female.csv"
    gender = "female"

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    city_campaign_pies = {}
    city_content_pies = {}
    city_hour_pies = {}

    date_range = start_date = end_date = None
    error = None

    allowed_top = {10, 20, 30, 50}
    try:
        top_n = int(request.args.get("top_number", 10))
    except (TypeError, ValueError):
        top_n = 10
    if top_n not in allowed_top:
        top_n = 10

    try:
        date_range_opt = Option.query.filter_by(
            meta_key="date_range_top_cities_female.csv"
        ).first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(
            meta_key="start_date_top_cities_female.csv"
        ).first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(
            meta_key="end_date_top_cities_female.csv"
        ).first()
        end_date = end_date_opt.meta_value if end_date_opt else None
    except Exception:
        current_app.logger.exception(
            "Reading date range options failed (top_cities_female)"
        )

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)
        return render_template(
            "top_cities_female.html",
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
            city_campaign_pies=city_campaign_pies,
            city_content_pies=city_content_pies,
            city_hour_pies=city_hour_pies,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            error=error,
            top_number=top_n,
        )

    try:
        (
            top_cities_list,
            cities_daily_trend,
            cities_forecast_trend,
            city_totals_rows,
            city_campaign_pies,
            city_content_pies,
            city_hour_pies,
        ) = get_top_cities_gender_daily_trend_with_forecast(
            input_file=input_file,
            gender=gender,
            top_n=top_n,
            forecast_periods=14,
            campaign_top_k=8,
            content_top_k=8,
        )

        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(c, 0.0) for c in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(c, 0.0) for c in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities female view failed")

    return render_template(
        "top_cities_female.html",
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        city_campaign_pies=city_campaign_pies,
        city_content_pies=city_content_pies,
        city_hour_pies=city_hour_pies,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,
    )


@main.route("/download/top_cities_female")
@login_required
def download_top_cities_female_city_csv():
    import io
    import os
    from datetime import datetime

    import pandas as pd
    from flask import abort, current_app, request, send_file

    # Use polygon lookup + hardcoded estratos dict
    from .services.localidades import find_localidad, LOCALIDADES_ESTRATOS

    # Query param
    city = (request.args.get("city") or "").strip()
    if not city:
        abort(400)  # missing ?city=

    # Master CSV used by /top_cities_female
    master_path = os.path.join(current_app.root_path, "..", "data", "top_cities_female.csv")
    if not os.path.exists(master_path):
        current_app.logger.warning("Master file not found: %s", master_path)
        abort(404)

    try:
        df = pd.read_csv(master_path)

        if "city" not in df.columns:
            current_app.logger.warning("Master CSV missing required column: city")
            abort(500)

        # Normalize city matching
        df["city"] = df["city"].astype(str).fillna("").str.strip()
        requested_city = city.upper()
        df_city = df[df["city"].str.upper() == requested_city].copy()

        # Enforce female-only rows if gender column exists
        if "gender" in df_city.columns:
            df_city["gender"] = df_city["gender"].astype(str).fillna("").str.strip().str.lower()
            df_city = df_city[df_city["gender"] == "female"].copy()

        if df_city.empty:
            abort(404)

        # Ensure these columns exist in the download
        for col in ["order_lat", "order_lng"]:
            if col not in df_city.columns:
                df_city[col] = ""

        # If Bogota, compute localidad + Estrato + nivel_socioeconomico from lat/lng
        if requested_city == "BOGOTA (C/MARCA)":

            def _to_float(x):
                try:
                    if x is None:
                        return None
                    s = str(x).strip()
                    if not s or s.lower() in {"nan", "none", "null", "undefined"}:
                        return None
                    return float(s)
                except Exception:
                    return None

            def _calc_fields(row):
                lat = _to_float(row.get("order_lat"))
                lng = _to_float(row.get("order_lng"))
                if lat is None or lng is None:
                    return ("", "", "")

                try:
                    loc = find_localidad(lat, lng) or ""
                    loc_key = str(loc).strip().upper()

                    meta = LOCALIDADES_ESTRATOS.get(loc_key, {}) if loc_key else {}
                    estrato = str(meta.get("estrato") or "").strip()
                    nivel = str(meta.get("nivel_socioeconomico") or "").strip()

                    return (loc_key, estrato, nivel)
                except Exception:
                    # Never fail the whole download because of a single row
                    return ("", "", "")

            df_city[["localidad", "Estrato", "nivel_socioeconomico"]] = df_city.apply(
                lambda r: pd.Series(_calc_fields(r)),
                axis=1,
            )
        else:
            # For non-Bogota cities, ensure the columns exist (empty)
            for col in ["localidad", "Estrato", "nivel_socioeconomico"]:
                if col not in df_city.columns:
                    df_city[col] = ""

        # Keep computed columns at the end
        tail_cols = ["order_lat", "order_lng"]
        for c in ["localidad", "Estrato", "nivel_socioeconomico"]:
            if c in df_city.columns:
                tail_cols.append(c)

        base_cols = [c for c in df_city.columns if c not in set(tail_cols)]
        df_city = df_city[base_cols + tail_cols]

        # Nice filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        safe_city = "".join(
            ch for ch in requested_city if ch.isalnum() or ch in ("_", "-", " ", "(", ")")
        ).strip() or "CITY"
        safe_city = safe_city.replace(" ", "_")

        download_name = f"top_cities_female_{safe_city}_{timestamp}.csv"

        buf = io.BytesIO()
        df_city.to_csv(buf, index=False, encoding="utf-8-sig")
        buf.seek(0)

        return send_file(
            buf,
            as_attachment=True,
            download_name=download_name,
            mimetype="text/csv",
        )

    except Exception:
        current_app.logger.exception(
            "Failed preparing CSV download from master (top_cities_female)"
        )
        abort(500)


@main.route("/top_cities_male")
@login_required
def top_cities_male():
    import os
    from flask import current_app, render_template, request

    from .models import Option
    from .services.top_cities_gender import (
        get_top_cities_gender_daily_trend_with_forecast,
    )

    input_file = "data/top_cities_male.csv"
    gender = "male"

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    city_campaign_pies = {}
    city_content_pies = {}
    city_hour_pies = {}

    date_range = start_date = end_date = None
    error = None

    allowed_top = {10, 20, 30, 50}
    try:
        top_n = int(request.args.get("top_number", 10))
    except (TypeError, ValueError):
        top_n = 10
    if top_n not in allowed_top:
        top_n = 10

    try:
        date_range_opt = Option.query.filter_by(
            meta_key="date_range_top_cities_male.csv"
        ).first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(
            meta_key="start_date_top_cities_male.csv"
        ).first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(
            meta_key="end_date_top_cities_male.csv"
        ).first()
        end_date = end_date_opt.meta_value if end_date_opt else None
    except Exception:
        current_app.logger.exception("Reading date range options failed (top_cities_male)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)
        return render_template(
            "top_cities_male.html",
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
            city_campaign_pies=city_campaign_pies,
            city_content_pies=city_content_pies,
            city_hour_pies=city_hour_pies,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            error=error,
            top_number=top_n,
        )

    try:
        (
            top_cities_list,
            cities_daily_trend,
            cities_forecast_trend,
            city_totals_rows,
            city_campaign_pies,
            city_content_pies,
            city_hour_pies,
        ) = get_top_cities_gender_daily_trend_with_forecast(
            input_file=input_file,
            gender=gender,
            top_n=top_n,
            forecast_periods=14,
            campaign_top_k=8,
            content_top_k=8,
        )

        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(c, 0.0) for c in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(c, 0.0) for c in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities male view failed")

    return render_template(
        "top_cities_male.html",
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        city_campaign_pies=city_campaign_pies,
        city_content_pies=city_content_pies,
        city_hour_pies=city_hour_pies,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,
    )



@main.route("/download/top_cities_male")
@login_required
def download_top_cities_male_city_csv():
    import io
    import os
    from datetime import datetime

    import pandas as pd
    from flask import abort, current_app, request, send_file

    # Use the hardcoded mapping inside app/services/localidades.py
    from .services.localidades import find_localidad, LOCALIDADES_ESTRATOS

    city = (request.args.get("city") or "").strip()
    if not city:
        abort(400)  # missing ?city=

    master_path = os.path.join(current_app.root_path, "..", "data", "top_cities_male.csv")
    if not os.path.exists(master_path):
        current_app.logger.warning("Master file not found: %s", master_path)
        abort(404)

    try:
        df = pd.read_csv(master_path)

        if "city" not in df.columns:
            current_app.logger.warning("Master CSV missing required column: city")
            abort(500)

        # Normalize city matching
        df["city"] = df["city"].astype(str).fillna("").str.strip()
        requested_city = city.upper()
        df_city = df[df["city"].str.upper() == requested_city].copy()

        # Enforce male-only rows if gender column exists
        if "gender" in df_city.columns:
            df_city["gender"] = df_city["gender"].astype(str).fillna("").str.strip().str.lower()
            df_city = df_city[df_city["gender"] == "male"].copy()

        if df_city.empty:
            abort(404)

        # Ensure these columns exist in the download
        for col in ["order_lat", "order_lng"]:
            if col not in df_city.columns:
                df_city[col] = ""

        # If Bogota, compute localidad + Estrato + nivel_socioeconomico from lat/lng
        if requested_city == "BOGOTA (C/MARCA)":

            def _to_float(x):
                try:
                    if x is None:
                        return None
                    s = str(x).strip()
                    if not s or s.lower() in {"nan", "none", "null", "undefined"}:
                        return None
                    return float(s)
                except Exception:
                    return None

            def _calc_fields(row):
                lat = _to_float(row.get("order_lat"))
                lng = _to_float(row.get("order_lng"))
                if lat is None or lng is None:
                    return ("", "", "")

                try:
                    loc = find_localidad(lat, lng) or ""
                    loc_key = str(loc).strip().upper()

                    meta = LOCALIDADES_ESTRATOS.get(loc_key, {}) if loc_key else {}
                    estrato = str(meta.get("estrato") or "").strip()
                    nivel = str(meta.get("nivel_socioeconomico") or "").strip()

                    return (loc_key, estrato, nivel)
                except Exception:
                    return ("", "", "")

            df_city[["localidad", "Estrato", "nivel_socioeconomico"]] = df_city.apply(
                lambda r: pd.Series(_calc_fields(r)),
                axis=1,
            )
        else:
            # For non-Bogota cities, keep columns present (empty) for consistent schema
            for col in ["localidad", "Estrato", "nivel_socioeconomico"]:
                if col not in df_city.columns:
                    df_city[col] = ""

        # Keep computed columns at the end
        tail_cols = ["order_lat", "order_lng"]
        for c in ["localidad", "Estrato", "nivel_socioeconomico"]:
            if c in df_city.columns:
                tail_cols.append(c)

        base_cols = [c for c in df_city.columns if c not in set(tail_cols)]
        df_city = df_city[base_cols + tail_cols]

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        safe_city = "".join(
            ch for ch in requested_city if ch.isalnum() or ch in ("_", "-", " ", "(", ")")
        ).strip() or "CITY"
        safe_city = safe_city.replace(" ", "_")

        download_name = f"top_cities_male_{safe_city}_{timestamp}.csv"

        buf = io.BytesIO()
        df_city.to_csv(buf, index=False, encoding="utf-8-sig")
        buf.seek(0)

        return send_file(
            buf,
            as_attachment=True,
            download_name=download_name,
            mimetype="text/csv",
        )

    except Exception:
        current_app.logger.exception(
            "Failed preparing CSV download from master (top_cities_male)"
        )
        abort(500)


@main.route("/top_cities_wati")
@login_required
def top_cities_wati():
    input_file = "data/top_cities_wati.csv"

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    date_range = start_date = end_date = None
    error = None

    # NEW: read top_number from querystring, default 10
    allowed_top = {10, 20, 30, 50}
    try:
        top_n = int(request.args.get("top_number", 10))
    except (TypeError, ValueError):
        top_n = 10
    if top_n not in allowed_top:
        top_n = 10

    try:
        date_range_opt = Option.query.filter_by(meta_key="date_range_top_cities_wati.csv").first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(meta_key="start_date_top_cities_wati.csv").first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(meta_key="end_date_top_cities_wati.csv").first()
        end_date = end_date_opt.meta_value if end_date_opt else None
    except Exception:
        current_app.logger.exception("Reading date range options failed (wati)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)
        return render_template(
            "top_cities_wati.html",
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            error=error,
            top_number=top_n,  # NEW
        )

    try:
        top_cities_list, cities_daily_trend, cities_forecast_trend, city_totals_rows = (
            get_top_cities_daily_trend_with_forecast(
                input_file,
                top_n=top_n,                # NEW
                forecast_periods=14,
                utm_campaign_filter="wati",
            )
        )

        # Build pie: Top N + Others
        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(c, 0.0) for c in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(c, 0.0) for c in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

        if grand_total == 0:
            error = "No orders found for utm_campaign = wati in the selected date range."

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities wati view failed")

    return render_template(
        "top_cities_wati.html",
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,  # NEW
    )

@main.route("/top_cities_facebook")
@login_required
def top_cities_facebook():
    input_file = "data/top_cities_facebook.csv"

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    date_range = start_date = end_date = None
    error = None

    # --- NEW: read top_number from querystring, default 10 ---
    allowed_top = {10, 20, 30, 50}
    try:
        top_n = int(request.args.get("top_number", 10))
    except (TypeError, ValueError):
        top_n = 10
    if top_n not in allowed_top:
        top_n = 10

    # Option keys based on file_name used in the daterange selector
    try:
        date_range_opt = Option.query.filter_by(meta_key="date_range_top_cities_facebook.csv").first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(meta_key="start_date_top_cities_facebook.csv").first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(meta_key="end_date_top_cities_facebook.csv").first()
        end_date = end_date_opt.meta_value if end_date_opt else None
    except Exception:
        current_app.logger.exception("Reading date range options failed (facebook)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)
        return render_template(
            "top_cities_facebook.html",
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            error=error,
            top_number=top_n,  # NEW
        )

    try:
        top_cities_list, cities_daily_trend, cities_forecast_trend, city_totals_rows = (
            get_top_cities_daily_trend_with_forecast(
                input_file,
                top_n=top_n,              # NEW
                forecast_periods=14,
                utm_source_filter="facebook",
            )
        )

        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(c, 0.0) for c in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(c, 0.0) for c in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

        if grand_total == 0:
            error = "No orders found for utm_source = facebook in the selected date range."

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities facebook view failed")

    return render_template(
        "top_cities_facebook.html",
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,  # NEW
    )

@main.route("/top_cities_google")
@login_required
def top_cities_google():
    input_file = "data/top_cities_google.csv"

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    date_range = start_date = end_date = None
    error = None

    # NEW: read top_number from querystring, default 10
    allowed_top = {10, 20, 30, 50}
    try:
        top_n = int(request.args.get("top_number", 10))
    except (TypeError, ValueError):
        top_n = 10
    if top_n not in allowed_top:
        top_n = 10

    try:
        date_range_opt = Option.query.filter_by(meta_key="date_range_top_cities_google.csv").first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(meta_key="start_date_top_cities_google.csv").first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(meta_key="end_date_top_cities_google.csv").first()
        end_date = end_date_opt.meta_value if end_date_opt else None
    except Exception:
        current_app.logger.exception("Reading date range options failed (google)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)
        return render_template(
            "top_cities_google.html",
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            error=error,
            top_number=top_n,  # NEW
        )

    try:
        top_cities_list, cities_daily_trend, cities_forecast_trend, city_totals_rows = (
            get_top_cities_daily_trend_with_forecast(
                input_file,
                top_n=top_n,            # NEW
                forecast_periods=14,
                utm_source_filter="google",
            )
        )

        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(c, 0.0) for c in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(c, 0.0) for c in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

        if grand_total == 0:
            error = "No orders found for utm_source = google in the selected date range."

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities google view failed")

    return render_template(
        "top_cities_google.html",
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,  # NEW
    )

@main.route("/top_cities_tiktok")
@login_required
def top_cities_tiktok():
    input_file = "data/top_cities_tiktok.csv"

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    date_range = start_date = end_date = None
    error = None

    # --- NEW: read top_number from querystring, default 10 ---
    allowed_top = {10, 20, 30, 50}
    try:
        top_n = int(request.args.get("top_number", 10))
    except (TypeError, ValueError):
        top_n = 10
    if top_n not in allowed_top:
        top_n = 10

    try:
        date_range_opt = Option.query.filter_by(meta_key="date_range_top_cities_tiktok.csv").first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(meta_key="start_date_top_cities_tiktok.csv").first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(meta_key="end_date_top_cities_tiktok.csv").first()
        end_date = end_date_opt.meta_value if end_date_opt else None
    except Exception:
        current_app.logger.exception("Reading date range options failed (tiktok)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)
        return render_template(
            "top_cities_tiktok.html",
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            error=error,
            top_number=top_n,  # NEW
        )

    try:
        top_cities_list, cities_daily_trend, cities_forecast_trend, city_totals_rows = (
            get_top_cities_daily_trend_with_forecast(
                input_file,
                top_n=top_n,                # NEW
                forecast_periods=14,
                utm_source_filter="tiktok",
            )
        )

        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(c, 0.0) for c in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(c, 0.0) for c in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

        if grand_total == 0:
            error = "No orders found for utm_source = tiktok in the selected date range."

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities tiktok view failed")

    return render_template(
        "top_cities_tiktok.html",
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,  # NEW
    )


@main.route("/top_cities_ecostand")
@login_required
def top_cities_ecostand():
    input_file = "data/top_cities_ecostand.csv"

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    date_range = start_date = end_date = None
    error = None

    # --- NEW: read top_number from querystring, default 10 ---
    allowed_top = {10, 20, 30, 50}
    try:
        top_n = int(request.args.get("top_number", 10))
    except (TypeError, ValueError):
        top_n = 10
    if top_n not in allowed_top:
        top_n = 10

    try:
        date_range_opt = Option.query.filter_by(meta_key="date_range_top_cities_ecostand.csv").first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(meta_key="start_date_top_cities_ecostand.csv").first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(meta_key="end_date_top_cities_ecostand.csv").first()
        end_date = end_date_opt.meta_value if end_date_opt else None
    except Exception:
        current_app.logger.exception("Reading date range options failed (ecostand)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)
        return render_template(
            "top_cities_ecostand.html",
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            error=error,
            top_number=top_n,  # NEW
        )

    PRODUCT_NAME = "Promoción Empresa: (24 unidades + ecostand)"

    try:
        top_cities_list, cities_daily_trend, cities_forecast_trend, city_totals_rows = (
            get_top_cities_daily_trend_with_forecast(
                input_file,
                top_n=top_n,            # NEW: use query value
                forecast_periods=14,
                product_filter=PRODUCT_NAME,
            )
        )

        # Build pie: Top N + Others
        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(c, 0.0) for c in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(c, 0.0) for c in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

        if grand_total == 0:
            error = f'No orders found for product = "{PRODUCT_NAME}" in the selected date range.'

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities ecostand view failed")

    return render_template(
        "top_cities_ecostand.html",
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,  # NEW
    )

@main.route("/top_cities_ecohotel")
@login_required
def top_cities_ecohotel():
    input_file = "data/top_cities_ecohotel.csv"

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    date_range = start_date = end_date = None
    error = None

    # --- NEW: read top_number from querystring, default 10 ---
    allowed_top = {10, 20, 30, 50}
    try:
        top_n = int(request.args.get("top_number", 10))
    except (TypeError, ValueError):
        top_n = 10
    if top_n not in allowed_top:
        top_n = 10

    try:
        date_range_opt = Option.query.filter_by(meta_key="date_range_top_cities_ecohotel.csv").first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(meta_key="start_date_top_cities_ecohotel.csv").first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(meta_key="end_date_top_cities_ecohotel.csv").first()
        end_date = end_date_opt.meta_value if end_date_opt else None
    except Exception:
        current_app.logger.exception("Reading date range options failed (ecohotel)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)
        return render_template(
            "top_cities_ecohotel.html",
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            error=error,
            top_number=top_n,  # NEW
        )

    PRODUCT_NAME = "Promoción Eco-Hotel: (24 unidades + 24 etiquetas con precio)"

    try:
        top_cities_list, cities_daily_trend, cities_forecast_trend, city_totals_rows = (
            get_top_cities_daily_trend_with_forecast(
                input_file,
                top_n=top_n,  # NEW: use query value
                forecast_periods=14,
                product_filter=PRODUCT_NAME,
            )
        )

        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(c, 0.0) for c in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(c, 0.0) for c in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

        if grand_total == 0:
            error = f'No orders found for product = "{PRODUCT_NAME}" in the selected date range.'

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities ecohotel view failed")

    return render_template(
        "top_cities_ecohotel.html",
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,  # NEW
    )

@main.route("/wati_insights")
@login_required
def wati_insights():
    import os
    import logging
    import pandas as pd
    from .models import Option
    from app.services.wati_insights import get_wati_sales_trend

    logger = logging.getLogger(__name__)

    error = None
    wati_sales_trend = []
    forecast_data = []
    kpis = {
        "mtd_sales": 0.0,
        "avg_per_day_mtd": 0.0,
        "last_7_days_sales": 0.0,
        "days_elapsed_mtd": 0,
    }

    input_file = "data/wati_sales_orders.csv"

    date_range = start_date = end_date = None
    try:
        opt = Option.query

        rec = opt.filter_by(meta_key="date_range_wati_sales_orders.csv").first()
        date_range = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key="start_date_wati_sales_orders.csv").first()
        start_date = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key="end_date_wati_sales_orders.csv").first()
        end_date = rec.meta_value if rec else None
    except Exception:
        logger.exception("Reading date range options failed (wati_insights)")

    wati_city_labels = []
    wati_city_values = []

    wati_time_labels = []
    wati_time_values = []

    wati_product_labels = []
    wati_product_values = []

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Use the date selector above to fetch data first."
        return render_template(
            "wati_insights.html",
            error=error,
            wati_sales_trend=[],
            forecast_data=[],
            kpis=kpis,
            date_range=date_range,
            start_date=start_date or "",
            end_date=end_date or "",
            wati_city_labels=wati_city_labels,
            wati_city_values=wati_city_values,
            wati_time_labels=wati_time_labels,
            wati_time_values=wati_time_values,
            wati_product_labels=wati_product_labels,
            wati_product_values=wati_product_values,
        )

    try:
        wati_sales_trend, forecast_data, kpis = get_wati_sales_trend(
            orders_csv_path=input_file,
            forecast_periods=14,
        )

        df = pd.read_csv(input_file)

        required_cols = {"order_date", "total_value", "utm_source", "city", "product"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Wati insights file is missing required columns: {sorted(missing)}")

        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
        df = df.dropna(subset=["order_date"]).copy()
        df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

        df["utm_source"] = (
            df["utm_source"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        df = df[df["utm_source"] == "wati"].copy()

        df["city"] = (
            df["city"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
        )
        df.loc[df["city"] == "", "city"] = "unknown"

        df["product"] = (
            df["product"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
        )
        df.loc[df["product"] == "", "product"] = "unknown"

        df["hour"] = df["order_date"].dt.hour

        def hour_bucket_3h(h: int) -> str:
            start = (int(h) // 3) * 3
            end = start + 2
            return f"{start:02d}-{end:02d}"

        df["hour_bucket_3h"] = df["hour"].apply(hour_bucket_3h)
        bucket_order = ["00-02", "03-05", "06-08", "09-11", "12-14", "15-17", "18-20", "21-23"]

        # Pie 1: Sales by City (Top N + Other)
        top_n_city = 8
        city_sales = df.groupby("city")["total_value"].sum().sort_values(ascending=False)
        city_top = city_sales.head(top_n_city)
        city_other = float(city_sales.iloc[top_n_city:].sum()) if len(city_sales) > top_n_city else 0.0

        wati_city_labels = [str(x) for x in city_top.index]
        wati_city_values = [float(v) for v in city_top.values]
        if city_other > 0:
            wati_city_labels.append("Other cities")
            wati_city_values.append(city_other)

        # Pie 2: Orders by Purchase Time (counts)
        time_counts = (
            df["hour_bucket_3h"]
            .value_counts()
            .reindex(bucket_order)
            .fillna(0)
        )
        wati_time_labels = [str(x) for x in time_counts.index]
        wati_time_values = [int(v) for v in time_counts.values]

        # Pie 3: Sales by Product (Top N + Other)
        top_n_product = 8
        product_sales = df.groupby("product")["total_value"].sum().sort_values(ascending=False)
        prod_top = product_sales.head(top_n_product)
        prod_other = float(product_sales.iloc[top_n_product:].sum()) if len(product_sales) > top_n_product else 0.0

        wati_product_labels = [str(x) for x in prod_top.index]
        wati_product_values = [float(v) for v in prod_top.values]
        if prod_other > 0:
            wati_product_labels.append("Other products")
            wati_product_values.append(prod_other)

    except Exception as e:
        logger.exception("Failed to build wati insights")
        error = str(e)

    return render_template(
        "wati_insights.html",
        error=error,
        wati_sales_trend=wati_sales_trend,
        forecast_data=forecast_data,
        kpis=kpis,
        date_range=date_range,
        start_date=start_date or "",
        end_date=end_date or "",
        wati_city_labels=wati_city_labels,
        wati_city_values=wati_city_values,
        wati_time_labels=wati_time_labels,
        wati_time_values=wati_time_values,
        wati_product_labels=wati_product_labels,
        wati_product_values=wati_product_values,
    )

# app/routes.py

# app/routes.py

@main.route("/checkout_insights")
@login_required
def checkout_insights():
    import os
    import logging
    from flask import current_app, render_template, request
    from .models import Option
    from app.services.checkout_insights import get_checkout_insights_daily

    logger = logging.getLogger(__name__)

    input_file = "data/checkout_insights_orders.csv"
    option_file_name = "checkout_insights_orders.csv"

    # Template vars
    error = None

    # Defaults from DB (saved preferences)
    date_range = None
    start_date = None
    end_date = None

    try:
        opt = Option.query

        rec = opt.filter_by(meta_key=f"start_date_{option_file_name}").first()
        start_date = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key=f"end_date_{option_file_name}").first()
        end_date = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key=f"date_range_{option_file_name}").first()
        date_range = (rec.meta_value if rec else None)
        if date_range:
            date_range = date_range.strip().lower() or None

    except Exception:
        logger.exception("Reading date range options failed (checkout_insights)")

    # Override with user-provided query params (editable any range)
    # Example: /checkout_insights?start_date=01/01/2026&end_date=24/01/2026
    req_start = (request.args.get("start_date") or "").strip()
    req_end = (request.args.get("end_date") or "").strip()
    req_range = (request.args.get("date_range") or "").strip()

    # If user provided custom dates, use them and ignore date_range for filtering
    if req_start and req_end:
        start_date = req_start
        end_date = req_end
        date_range = ""  # keep dropdown on "--Select--"
    elif req_range:
        # If user selected a predefined range, prefer it (and clear custom fields)
        date_range = req_range
        start_date = ""
        end_date = ""

    # Guard: file must exist
    if not os.path.exists(input_file):
        error = f"{input_file} not found. Use the date selector above to fetch data first."
        return render_template(
            "checkout_insights.html",
            error=error,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            total_daily_trend=[],
            forecast_data=[],
            pie_labels=[],
            pie_values=[],
            answer_charts=[],
            other_answers_labels=[],
            other_answers_pct=0.0,
            gender_labels_total=[],
            gender_values_total=[],
            city_labels_total=[],
            city_values_total=[],
            city_other_label_total=None,
            hour_labels_total=[],
            hour_values_total=[],
        )

    # Build insights (service already supports custom window via start_date/end_date)
    total_daily_trend = []
    forecast_data = []
    pie_labels = []
    pie_values = []
    answer_charts = []
    other_answers_labels = []
    other_answers_pct = 0.0
    gender_labels_total = []
    gender_values_total = []
    city_labels_total = []
    city_values_total = []
    city_other_label_total = None
    hour_labels_total = []
    hour_values_total = []

    try:
        result = get_checkout_insights_daily(
            orders_csv_path=input_file,
            forecast_periods=30,
            min_share_percent=2.0,
            start_date=start_date if (start_date and end_date) else None,
            end_date=end_date if (start_date and end_date) else None,
            top_cities=20,
        )

        total_daily_trend = result["total_daily_trend"]
        forecast_data = result["forecast_data"]
        pie_labels = result["pie_labels"]
        pie_values = result["pie_values"]
        answer_charts = result["answer_charts"]
        other_answers_labels = result["other_answers_labels"]
        other_answers_pct = result["other_answers_pct"]
        gender_labels_total = result["gender_labels_total"]
        gender_values_total = result["gender_values_total"]
        city_labels_total = result["city_labels_total"]
        city_values_total = result["city_values_total"]
        city_other_label_total = result.get("city_other_label_total")
        hour_labels_total = result["hour_labels_total"]
        hour_values_total = result["hour_values_total"]

    except Exception as e:
        logger.exception("checkout_insights view failed")
        error = str(e)
        current_app.logger.exception("checkout_insights view failed")

    return render_template(
        "checkout_insights.html",
        error=error,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        total_daily_trend=total_daily_trend,
        forecast_data=forecast_data,
        pie_labels=pie_labels,
        pie_values=pie_values,
        answer_charts=answer_charts,
        other_answers_labels=other_answers_labels,
        other_answers_pct=other_answers_pct,
        gender_labels_total=gender_labels_total,
        gender_values_total=gender_values_total,
        city_labels_total=city_labels_total,
        city_values_total=city_values_total,
        city_other_label_total=city_other_label_total,
        hour_labels_total=hour_labels_total,
        hour_values_total=hour_values_total,
    )

# app/routes.py

@main.route("/bogota_insights")
@login_required
def bogota_insights():
    import logging
    from flask import render_template
    from .models import Option
    from app.services.bogota_insights import get_bogota_insights_view_data

    logger = logging.getLogger(__name__)

    SOURCE_FILE = "data/bogota_sales_orders.csv"
    FILTERED_FILE = "data/bogota_sales_orders_bogota_only.csv"
    BOGOTA_CITY_VALUE = "BOGOTA (C/MARCA)"

    try:
        ctx = get_bogota_insights_view_data(
            OptionModel=Option,
            source_file=SOURCE_FILE,
            filtered_file=FILTERED_FILE,
            city_value=BOGOTA_CITY_VALUE,
            forecast_periods=30,
            logger=logger,
        )
    except Exception as e:
        logger.exception("bogota_insights view failed")
        ctx = {
            "error": str(e),
            "date_range": None,
            "start_date": None,
            "end_date": None,
            "total_daily_trend": [],
            "forecast_data": [],
        }

    return render_template("bogota_insights.html", **ctx)


# app/routes.py

@main.route("/bogota_insights_female")
@login_required
def bogota_insights_female():
    import os
    import logging
    from flask import render_template
    from .models import Option
    from app.services.bogota_insights import (
        get_bogota_insights_view_data,
        build_city_filtered_csv,
    )

    logger = logging.getLogger(__name__)

    SOURCE_FILE = "data/bogota_insights_female.csv"
    FILTERED_FILE = "data/bogota_insights_female_filtered.csv"
    BOGOTA_CITY_VALUE = "BOGOTA (C/MARCA)"

    try:
        if not os.path.exists(SOURCE_FILE):
            return render_template(
                "bogota_insights_female.html",
                error=f"{SOURCE_FILE} not found. Use the date selector above to fetch data first.",
                date_range=None,
                start_date=None,
                end_date=None,
                total_daily_trend=[],
                forecast_data=[],
                campaign_groups=[],
                gender="female",
            )

        if not os.path.exists(FILTERED_FILE):
            stats = build_city_filtered_csv(
                src_path=SOURCE_FILE,
                dst_path=FILTERED_FILE,
                target_city=BOGOTA_CITY_VALUE,
                gender="female",
            )

            logger.info(
                "Filtered CSV created (first time): %s (kept=%s removed=%s bad_rows=%s)",
                stats.get("output_file"),
                stats.get("kept"),
                stats.get("removed"),
                stats.get("bad_rows"),
            )
        else:
            logger.debug("Filtered CSV already exists. Skipping regeneration.")

        ctx = get_bogota_insights_view_data(
            OptionModel=Option,
            source_file=SOURCE_FILE,
            filtered_file=FILTERED_FILE,
            city_value=BOGOTA_CITY_VALUE,
            forecast_periods=7,
            gender="female",
            logger=logger,
        )

    except Exception as e:
        logger.exception("bogota_insights_female view failed")
        ctx = {
            "error": str(e),
            "date_range": None,
            "start_date": None,
            "end_date": None,
            "total_daily_trend": [],
            "forecast_data": [],
            "campaign_groups": [],
            "gender": "female",
        }

    return render_template("bogota_insights_female.html", **ctx)





# app/routes.py

@main.route("/bogota_insights_male")
@login_required
def bogota_insights_male():
    import os
    import logging
    from flask import render_template
    from .models import Option
    from app.services.bogota_insights import (
        get_bogota_insights_view_data,
        build_city_filtered_csv,
    )

    logger = logging.getLogger(__name__)

    SOURCE_FILE = "data/bogota_insights_male.csv"
    FILTERED_FILE = "data/bogota_insights_male_filtered.csv"
    BOGOTA_CITY_VALUE = "BOGOTA (C/MARCA)"

    try:
        if not os.path.exists(SOURCE_FILE):
            return render_template(
                "bogota_insights_male.html",
                error=f"{SOURCE_FILE} not found. Use the date selector above to fetch data first.",
                date_range=None,
                start_date=None,
                end_date=None,
                total_daily_trend=[],
                forecast_data=[],
                campaign_groups=[],
                gender="male",
            )

        if not os.path.exists(FILTERED_FILE):
            stats = build_city_filtered_csv(
                src_path=SOURCE_FILE,
                dst_path=FILTERED_FILE,
                target_city=BOGOTA_CITY_VALUE,
                gender="male",
            )

            logger.info(
                "Filtered CSV created (first time): %s (kept=%s removed=%s bad_rows=%s)",
                stats.get("output_file"),
                stats.get("kept"),
                stats.get("removed"),
                stats.get("bad_rows"),
            )
        else:
            logger.debug("Filtered CSV already exists. Skipping regeneration.")

        ctx = get_bogota_insights_view_data(
            OptionModel=Option,
            source_file=SOURCE_FILE,
            filtered_file=FILTERED_FILE,
            city_value=BOGOTA_CITY_VALUE,
            forecast_periods=30,
            gender="male",
            logger=logger,
        )

    except Exception as e:
        logger.exception("bogota_insights_male view failed")
        ctx = {
            "error": str(e),
            "date_range": None,
            "start_date": None,
            "end_date": None,
            "total_daily_trend": [],
            "forecast_data": [],
            "campaign_groups": [],
            "gender": "male",
        }

    return render_template("bogota_insights_male.html", **ctx)

from flask import send_file, abort, current_app
from flask_login import login_required
import os


@main.route("/downloads/bogota_insights/<path:filename>")
@login_required
def download_bogota_insights_file(filename):
    """
    Secure download for Bogota insights filtered CSVs.
    """

    allowed_files = {
        "bogota_insights_female_filtered.csv",
        "bogota_insights_male_filtered.csv",
    }

    filename = (filename or "").strip()

    if filename not in allowed_files:
        abort(404)

    # Get project root (one level above app/)
    project_root = os.path.abspath(
        os.path.join(current_app.root_path, "..")
    )

    file_path = os.path.join(project_root, "data", filename)

    if not os.path.exists(file_path):
        abort(404)

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv",
    )

@main.route("/facebook_insights")
@login_required
def facebook_insights():
    import os
    import logging
    from flask import render_template
    from .models import Option
    from app.services.facebook_insights import get_facebook_insights_daily

    logger = logging.getLogger(__name__)

    input_file = "data/facebook_sales_orders.csv"

    error = None
    date_range = start_date = end_date = None

    total_daily_trend = []
    forecast_data = []

    pie_labels = []
    pie_keys = []
    pie_values = []
    campaign_charts = []

    other_campaigns_labels = []
    other_campaigns_pct = 0.0

    total_hour_pie_labels = []
    total_hour_pie_values = []

    total_gender_pie_labels = []
    total_gender_pie_values = []

    total_city_pie_labels = []
    total_city_pie_values = []

    # NEW: total utm_content pie
    total_content_pie_labels = []
    total_content_pie_values = []

    try:
        opt = Option.query

        rec = opt.filter_by(meta_key="date_range_facebook_sales_orders.csv").first()
        date_range = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key="start_date_facebook_sales_orders.csv").first()
        start_date = rec.meta_value if rec else None

        rec = opt.filter_by(meta_key="end_date_facebook_sales_orders.csv").first()
        end_date = rec.meta_value if rec else None
    except Exception:
        logger.exception("Reading date range options failed (facebook_insights)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Use the date selector above to fetch data first."
        return render_template(
            "facebook_insights.html",
            error=error,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            total_daily_trend=[],
            forecast_data=[],
            pie_labels=[],
            pie_keys=[],
            pie_values=[],
            campaign_charts=[],
            other_campaigns_labels=[],
            other_campaigns_pct=0.0,
            total_hour_pie_labels=[],
            total_hour_pie_values=[],
            total_gender_pie_labels=[],
            total_gender_pie_values=[],
            total_city_pie_labels=[],
            total_city_pie_values=[],
            total_content_pie_labels=[],
            total_content_pie_values=[],
        )

    try:
        result = get_facebook_insights_daily(
            orders_csv_path=input_file,
            forecast_periods=30,
            min_share_percent=2.0,
        )

        total_daily_trend = result["total_daily_trend"]
        forecast_data = result["forecast_data"]

        pie_labels = result["pie_labels"]
        pie_keys = result.get("pie_keys", [])
        pie_values = result["pie_values"]

        campaign_charts = result["campaign_charts"]

        other_campaigns_labels = result["other_campaigns_labels"]
        other_campaigns_pct = result["other_campaigns_pct"]

        total_hour_pie_labels = result.get("total_hour_pie_labels", [])
        total_hour_pie_values = result.get("total_hour_pie_values", [])
        total_gender_pie_labels = result.get("total_gender_pie_labels", [])
        total_gender_pie_values = result.get("total_gender_pie_values", [])

        total_city_pie_labels = result.get("total_city_pie_labels", [])
        total_city_pie_values = result.get("total_city_pie_values", [])

        # NEW
        total_content_pie_labels = result.get("total_content_pie_labels", [])
        total_content_pie_values = result.get("total_content_pie_values", [])

    except Exception as e:
        logger.exception("facebook_insights view failed")
        error = str(e)

    return render_template(
        "facebook_insights.html",
        error=error,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        total_daily_trend=total_daily_trend,
        forecast_data=forecast_data,
        pie_labels=pie_labels,
        pie_keys=pie_keys,
        pie_values=pie_values,
        campaign_charts=campaign_charts,
        other_campaigns_labels=other_campaigns_labels,
        other_campaigns_pct=other_campaigns_pct,
        total_hour_pie_labels=total_hour_pie_labels,
        total_hour_pie_values=total_hour_pie_values,
        total_gender_pie_labels=total_gender_pie_labels,
        total_gender_pie_values=total_gender_pie_values,
        total_city_pie_labels=total_city_pie_labels,
        total_city_pie_values=total_city_pie_values,
        total_content_pie_labels=total_content_pie_labels,
        total_content_pie_values=total_content_pie_values,
    )

@main.route("/debug/barrio_test")
@login_required
def debug_barrio_test():
    import os
    import logging
    from flask import jsonify, current_app
    from app.services.barrioResult import (
        barrio_from_address,
        barrio_legalizado_from_point,
    )

    logger = logging.getLogger(__name__)

    sector_shp = os.path.abspath(os.path.join(current_app.root_path, "..", "barrios", "bogota", "SECTOR.shp"))
    barrio_shp = os.path.abspath(os.path.join(current_app.root_path, "..", "barrios", "bogota", "BarrioLegalizado.shp"))

    if not os.path.exists(sector_shp):
        return jsonify({"ok": False, "error": f"Sector shapefile not found at {sector_shp}"}), 404

    if not os.path.exists(barrio_shp):
        return jsonify({"ok": False, "error": f"Barrio legalizado shapefile not found at {barrio_shp}"}), 404

    try:
        # 1) Geocode once
        sector_result = barrio_from_address("Diagonal 51a #60f-53 sur", sector_shp)

        # 2) Barrio lookup (now will correctly fallback if within gives NaN index_right)
        barrio_result = barrio_legalizado_from_point(
            lat=sector_result.lat,
            lon=sector_result.lon,
            barrio_legalizado_shp_path=barrio_shp,
        )

        payload = {
            "ok": True,
            "lat": sector_result.lat,
            "lon": sector_result.lon,
            "sector_raw": sector_result.raw_fields,
            "sector_debug": sector_result.debug,
            "barrio": barrio_result.barrio,
            "barrio_raw": barrio_result.raw_fields,
            "barrio_debug": barrio_result.debug,
        }

        if not barrio_result.barrio:
            payload["hint"] = (
                "No barrio name found. Check barrio_debug.matched_by and barrio_debug.nearest_distance_m. "
                "If matched_by=nearest and nearest_distance_m is large, CRS is wrong. "
                "If nearest_distance_m is small, polygon gaps exist and buffer_intersects should match."
            )

        return jsonify(payload)

    except Exception as e:
        logger.exception("debug_barrio_test failed")
        return jsonify({"ok": False, "error": str(e)}), 500





# app/routes.py (add this view)
from app.services.google_insights import get_google_insights_daily

# app/routes.py

# app/routes.py

@main.route("/google_insights")
@login_required
def google_insights():
    import os
    import logging
    from flask import render_template
    from .models import Option
    from app.services.google_insights import get_google_insights_daily

    logger = logging.getLogger(__name__)
    input_file = "data/google_sales_orders.csv"

    error = None
    date_range = start_date = end_date = None

    total_daily_trend = []
    forecast_data = []

    pie_labels = []
    pie_keys = []
    pie_values = []
    campaign_charts = []

    other_campaigns_labels = []
    other_campaigns_pct = 0.0

    total_hour_pie_labels = []
    total_hour_pie_values = []
    total_gender_pie_labels = []
    total_gender_pie_values = []

    total_city_pie_labels = []
    total_city_pie_values = []

    # NEW: total utm_content pie
    total_content_pie_labels = []
    total_content_pie_values = []

    try:
        rec = Option.query.filter_by(meta_key="date_range_google_sales_orders.csv").first()
        date_range = rec.meta_value if rec else None

        rec = Option.query.filter_by(meta_key="start_date_google_sales_orders.csv").first()
        start_date = rec.meta_value if rec else None

        rec = Option.query.filter_by(meta_key="end_date_google_sales_orders.csv").first()
        end_date = rec.meta_value if rec else None
    except Exception:
        logger.exception("Reading date range options failed (google_insights)")

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Use the date selector above to fetch data first."
        return render_template(
            "google_insights.html",
            error=error,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
            total_daily_trend=[],
            forecast_data=[],
            pie_labels=[],
            pie_keys=[],
            pie_values=[],
            campaign_charts=[],
            other_campaigns_labels=[],
            other_campaigns_pct=0.0,
            total_hour_pie_labels=[],
            total_hour_pie_values=[],
            total_gender_pie_labels=[],
            total_gender_pie_values=[],
            total_city_pie_labels=[],
            total_city_pie_values=[],
            total_content_pie_labels=[],
            total_content_pie_values=[],
        )

    try:
        result = get_google_insights_daily(
            orders_csv_path=input_file,
            forecast_periods=30,
            min_share_percent=2.0,
        )

        total_daily_trend = result["total_daily_trend"]
        forecast_data = result["forecast_data"]

        pie_labels = result["pie_labels"]
        pie_keys = result.get("pie_keys", [])
        pie_values = result["pie_values"]

        campaign_charts = result["campaign_charts"]

        other_campaigns_labels = result["other_campaigns_labels"]
        other_campaigns_pct = result["other_campaigns_pct"]

        total_hour_pie_labels = result.get("total_hour_pie_labels", [])
        total_hour_pie_values = result.get("total_hour_pie_values", [])
        total_gender_pie_labels = result.get("total_gender_pie_labels", [])
        total_gender_pie_values = result.get("total_gender_pie_values", [])

        total_city_pie_labels = result.get("total_city_pie_labels", [])
        total_city_pie_values = result.get("total_city_pie_values", [])

        # NEW
        total_content_pie_labels = result.get("total_content_pie_labels", [])
        total_content_pie_values = result.get("total_content_pie_values", [])

    except Exception as e:
        logger.exception("google_insights view failed")
        error = str(e)

    return render_template(
        "google_insights.html",
        error=error,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        total_daily_trend=total_daily_trend,
        forecast_data=forecast_data,
        pie_labels=pie_labels,
        pie_keys=pie_keys,
        pie_values=pie_values,
        campaign_charts=campaign_charts,
        other_campaigns_labels=other_campaigns_labels,
        other_campaigns_pct=other_campaigns_pct,
        total_hour_pie_labels=total_hour_pie_labels,
        total_hour_pie_values=total_hour_pie_values,
        total_gender_pie_labels=total_gender_pie_labels,
        total_gender_pie_values=total_gender_pie_values,
        total_city_pie_labels=total_city_pie_labels,
        total_city_pie_values=total_city_pie_values,
        total_content_pie_labels=total_content_pie_labels,
        total_content_pie_values=total_content_pie_values,
    )


@main.route('/performance')
@login_required
def performance():

    date_range_opt = Option.query.filter_by(meta_key="date_range_performance_orders.csv").first()
    date_range = date_range_opt.meta_value if date_range_opt else None

    start_date_opt = Option.query.filter_by(meta_key="start_date_performance_orders.csv").first()
    start_date = start_date_opt.meta_value if start_date_opt else None

    end_date_opt = Option.query.filter_by(meta_key="end_date_performance_orders.csv").first()
    end_date = end_date_opt.meta_value if end_date_opt else None

    input_file = "data/performance_orders.csv"  # Main dataset for conversions
    weekly_stats = get_weekly_order_stats(input_file)


    return render_template(
        "performance.html", 
        weekly_stats=weekly_stats,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date
    )

@main.route("/login", methods=["GET", "POST"])
def login():
    # If already logged in, send user to dashboard
    if current_user.is_authenticated:
        return redirect(url_for("main.monthly_sales"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        if not username or not password:
            flash("Please enter both username and password.", "danger")
            return redirect(url_for("main.login"))

        user = User.query.filter_by(username=username).first()

        if not user or not user.check_password(password):
            flash("Invalid username or password.", "danger")
            return redirect(url_for("main.login"))

        # Login user
        login_user(user)

        # Store last login in UTC
        user.last_login = datetime.now(timezone.utc)
        db.session.commit()

        flash("Logged in successfully.", "success")
        return redirect(url_for("main.monthly_sales"))

    return render_template("login.html")

@main.route("/logout")
@login_required
def logout():
    logout_user()  # remove user session
    flash("You have been logged out.", "success")
    return redirect(url_for("main.login"))

@main.route("/register", methods=["GET", "POST"])
def register():
    # 1. Check if any user already exists in the database
    existing_any_user = User.query.first()
    if existing_any_user:
        flash("There is an admin user already created. Please log in.", "warning")
        return redirect(url_for("main.login"))

    # 2. If no user exists yet, proceed with registration
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # (Optional) add more validation logic if needed
        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("main.register"))

        # Create the new user
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("main.login"))
    
    return render_template("register.html")

from functools import wraps
from flask import flash, redirect, url_for


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("main.login"))

        if getattr(current_user, "role", "user") != "admin":
            flash("You do not have permission to access that page.", "danger")
            return redirect(url_for("main.monthly_sales"))

        return view_func(*args, **kwargs)
    return wrapped


from datetime import timezone
from zoneinfo import ZoneInfo

@main.route("/admin/users")
@login_required
@admin_required
def admin_users_index():
    users = User.query.order_by(User.id.desc()).all()

    bogota = ZoneInfo("America/Bogota")

    for u in users:
        dt = u.last_login
        if not dt:
            u.last_login_bogota = None
            continue

        # SQLite often returns naive datetimes.
        # Since we store UTC, treat naive values as UTC.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        u.last_login_bogota = dt.astimezone(bogota)

    return render_template("admin/users_index.html", users=users)


@main.route("/admin/users/new", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users_new():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        role = (request.form.get("role") or "user").strip().lower()

        if role not in {"admin", "user"}:
            role = "user"

        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("main.admin_users_new"))

        existing = User.query.filter_by(username=username).first()
        if existing:
            flash("That username already exists.", "danger")
            return redirect(url_for("main.admin_users_new"))

        new_user = User(username=username, role=role)
        new_user.set_password(password)

        db.session.add(new_user)
        db.session.commit()

        flash("User created successfully.", "success")
        return redirect(url_for("main.admin_users_index"))

    return render_template("admin/users_new.html")


@main.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users_edit(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        role = (request.form.get("role") or user.role or "user").strip().lower()
        password = request.form.get("password") or ""

        if role not in {"admin", "user"}:
            role = "user"

        if not username:
            flash("Username is required.", "danger")
            return redirect(url_for("main.admin_users_edit", user_id=user_id))

        # Prevent duplicate usernames
        existing = User.query.filter(User.username == username, User.id != user.id).first()
        if existing:
            flash("That username is already taken by another user.", "danger")
            return redirect(url_for("main.admin_users_edit", user_id=user_id))

        # Optional safety: prevent removing the last admin role
        if user.role == "admin" and role != "admin":
            admins_count = User.query.filter_by(role="admin").count()
            if admins_count <= 1:
                flash("You cannot remove admin role from the last admin user.", "danger")
                return redirect(url_for("main.admin_users_edit", user_id=user_id))

        user.username = username
        user.role = role

        # Only update password if provided
        if password.strip():
            user.set_password(password)

        db.session.commit()
        flash("User updated successfully.", "success")
        return redirect(url_for("main.admin_users_index"))

    return render_template("admin/users_edit.html", user=user)


@main.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_users_delete(user_id):
    user = User.query.get_or_404(user_id)

    # Optional safety: do not let admin delete themselves
    if user.id == current_user.id:
        flash("You cannot delete your own user while logged in.", "danger")
        return redirect(url_for("main.admin_users_index"))

    # Optional safety: do not delete last admin
    if user.role == "admin":
        admins_count = User.query.filter_by(role="admin").count()
        if admins_count <= 1:
            flash("You cannot delete the last admin user.", "danger")
            return redirect(url_for("main.admin_users_index"))

    db.session.delete(user)
    db.session.commit()

    flash("User deleted successfully.", "success")
    return redirect(url_for("main.admin_users_index"))


@main.route("/settings")
@login_required
def settings():

    # If everything goes well, render the monthly_data.html template
    return render_template(
        "settings.html"
    )

@main.route("/ads_rankings")
@login_required
def ads_rankings():
    input_file = "data/ads_rankings.csv"  # Main dataset for conversions
    try:
        # Get the saved date options
        date_range_opt = Option.query.filter_by(meta_key="date_range_ads_rankings.csv").first()
        date_range = date_range_opt.meta_value if date_range_opt else None

        start_date_opt = Option.query.filter_by(meta_key="start_date_ads_rankings.csv").first()
        start_date = start_date_opt.meta_value if start_date_opt else None

        end_date_opt = Option.query.filter_by(meta_key="end_date_ads_rankings.csv").first()
        end_date = end_date_opt.meta_value if end_date_opt else None

        top_ten_utm_campaigns = get_top_ten_utm_campaigns(input_file)
        top_utm_answers = get_utm_answer_ranking(input_file)
        top_twenty_days_by_undefined_campaign = get_top_twenty_days_by_undefined_campaign(input_file)
        top_utm_content = get_top_ten_utm_content_by_sales(input_file)
        top_utm_source = get_top_ten_utm_source_by_sales(input_file)
        top_utm_medium = get_top_ten_utm_medium_by_sales(input_file)
        top_utm_term = get_top_ten_utm_term_by_sales(input_file)

        utm_content_ranking_by_gender_male = get_utm_content_ranking_by_gender(input_file, gender='male')
        utm_content_ranking_by_gender_female = get_utm_content_ranking_by_gender(input_file, gender='female')

    except Exception as e:
        error=str(e), 
        logger.info(error)
        return render_template(
            "ads_rankings.html", 
            error=str(e), 
            top_utm_answers=None,
            top_ten_utm_campaigns=None,
            top_twenty_days_by_undefined_campaign=None,
            top_utm_content=None,
            top_utm_source=None,
            top_utm_medium=None,
            top_utm_term=None,
            utm_content_ranking_by_gender_male=None,
            utm_content_ranking_by_gender_female=None
        )

    return render_template(
        "ads_rankings.html", 
        top_ten_utm_campaigns=top_ten_utm_campaigns,
        top_utm_answers=top_utm_answers,
        top_twenty_days_by_undefined_campaign=top_twenty_days_by_undefined_campaign,
        top_utm_content=top_utm_content,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        top_utm_source=top_utm_source,
        top_utm_medium=top_utm_medium,
        top_utm_term=top_utm_term,
        utm_content_ranking_by_gender_male=utm_content_ranking_by_gender_male,
        utm_content_ranking_by_gender_female=utm_content_ranking_by_gender_female
    )

from app.services.monthly_repurchases import get_monthly_repurchases_trend

@main.route("/monthly_repurchases")
@login_required
def monthly_repurchases_by_month():
    try:
        refresh_all_orders_if_needed()
    except Exception:
        current_app.logger.exception("Failed refreshing all_orders cache")

    all_orders = "data/all_orders.csv"

    monthly_repurchases_trend = []
    forecast_data = []

    pie_labels = []
    pie_values = []
    channel_charts = []
    other_channels_labels = []
    other_channels_pct = 0.0

    # Total meta for bridge point
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

        # Total repurchases trend + forecast with weekday-weighted projection
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

        forecast_includes_current_month_mtd = bool(meta.get("forecast_includes_current_month_mtd", False))
        projection_method = meta.get("projection_method", "weekday_weighted")
        current_month_label = meta.get("current_month_label")

        current_month_mtd_sales = float(meta.get("current_month_mtd_sales", 0.0) or 0.0)
        current_month_projected_sales = float(meta.get("current_month_projected_sales", 0.0) or 0.0)
        current_month_remaining_days = int(meta.get("current_month_remaining_days", 0) or 0)
        current_month_days_in_month = int(meta.get("current_month_days_in_month", 0) or 0)

        # -------------------------
        # Pie: repurchase sales by utm_source (up to end of previous month)
        # -------------------------
        import pandas as pd

        df = pd.read_csv(all_orders)
        required_cols = {"email", "order_date", "total_value", "utm_source", "order_id"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Orders file is missing required columns for pie chart: {sorted(missing)}")

        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
        df = df.dropna(subset=["order_date", "email"]).copy()
        df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

        # Cut to end of previous month (Bogota naive)
        today_bogota = pd.Timestamp.now(tz="America/Bogota").tz_localize(None).normalize()
        start_current_month = today_bogota.replace(day=1)
        end_prev_month = start_current_month - pd.Timedelta(microseconds=1)
        df = df[df["order_date"] <= end_prev_month].copy()

        # Compute repurchase flag from ALL orders (within cutoff)
        email_counts = df["email"].value_counts(dropna=True)
        repeat_emails = set(email_counts[email_counts > 1].index)
        first_order_dt = df.groupby("email")["order_date"].min()
        df = df.join(first_order_dt, on="email", rsuffix="_first")
        df["is_repurchase"] = (df["email"].isin(repeat_emails)) & (df["order_date"] > df["order_date_first"])
        rep = df[df["is_repurchase"]].copy()

        # Normalize utm_source
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

        MIN_SHARE_PERCENT = 2.0
        included = []
        excluded = []

        if total_sales_value > 0:
            for ch, val in grouped_value.items():
                val = float(val)
                if val <= 0:
                    continue

                pct = (val / total_sales_value) * 100.0
                cnt = int(grouped_count.get(ch, 0))
                item = {"key": str(ch), "pct": pct, "value": val, "count": cnt}

                if pct >= MIN_SHARE_PERCENT:
                    included.append(item)
                else:
                    excluded.append(item)

        # Match your style: rank channel charts by count (or value if you prefer)
        included.sort(key=lambda x: x["count"], reverse=True)
        excluded.sort(key=lambda x: x["pct"], reverse=True)

        other_channels_labels = [x["key"] for x in excluded]
        other_channels_pct = float(sum(x["pct"] for x in excluded)) if excluded else 0.0

        # -------------------------
        # Channel charts: trend + forecast + meta (bridge point)
        # -------------------------
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

                # Bridge meta
                "forecast_includes_current_month_mtd": bool(ch_meta.get("forecast_includes_current_month_mtd", False)),
                "projection_method": ch_meta.get("projection_method", "weekday_weighted"),
                "current_month_label": ch_meta.get("current_month_label"),
                "current_month_mtd_sales": float(ch_meta.get("current_month_mtd_sales", 0.0) or 0.0),
                "current_month_projected_sales": float(ch_meta.get("current_month_projected_sales", 0.0) or 0.0),
                "current_month_remaining_days": int(ch_meta.get("current_month_remaining_days", 0) or 0),
                "current_month_days_in_month": int(ch_meta.get("current_month_days_in_month", 0) or 0),
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

        # Total meta (for header + bridge)
        forecast_includes_current_month_mtd=forecast_includes_current_month_mtd,
        projection_method=projection_method,
        current_month_label=current_month_label,
        current_month_mtd_sales=current_month_mtd_sales,
        current_month_projected_sales=current_month_projected_sales,
        current_month_remaining_days=current_month_remaining_days,
        current_month_days_in_month=current_month_days_in_month,

        error=error,
    )

@main.route("/daily_repurchases")
@login_required
def daily_repurchases():
    try:
        refresh_all_orders_if_needed()
    except Exception:
        current_app.logger.exception("Failed refreshing all_orders cache")

    input_file = "data/all_orders.csv"

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

    # City pies (total + by channel)
    city_labels_total = []
    city_values_total = []
    city_pies_by_channel = {}  # { channel: {labels:[], values:[]} }

    # Hour chunk pies (total + by channel)
    hour_labels_total = []
    hour_values_total = []
    hour_pies_by_channel = {}  # { channel: {labels:[], values:[]} }

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

    def build_top_n_city_pie(city_series, top_n=20, other_label="Other cities"):
        """
        city_series: pd.Series indexed by city name with summed total_value
        Returns:
          labels, values
        Notes:
          We keep an "Other cities" slice if there is remainder,
          but we do NOT return or display the list of cities in that remainder.
        """
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
        """
        Groups sales into 3-hour buckets:
          00-03, 03-06, 06-09, 09-12, 12-15, 15-18, 18-21, 21-24
        Returns:
          labels, values
        Assumes dt_col is already in Bogota local time (naive) as requested.
        """
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

        # Ensure datetime
        tmp[dt_col] = pd.to_datetime(tmp[dt_col], errors="coerce")
        tmp = tmp.dropna(subset=[dt_col]).copy()
        if tmp.empty:
            return [], []

        hours = tmp[dt_col].dt.hour.fillna(0).astype(int)
        bucket_start = (hours // 3) * 3
        tmp["_bucket_start"] = bucket_start

        grouped = tmp.groupby("_bucket_start")[value_col].sum()

        # Force all buckets in order, show even if 0? We keep only >0 slices like other pies do.
        bucket_order = [0, 3, 6, 9, 12, 15, 18, 21]

        labels = []
        values = []
        for b in bucket_order:
            v = float(grouped.get(b, 0.0))
            if v > 0:
                labels.append(f"{b:02d}-{(b+3):02d}" if b < 21 else "21-24")
                values.append(v)

        return labels, values

    try:
        import pandas as pd

        # Main chart (all repurchases)
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
            raise ValueError(f"Orders file is missing required columns for repurchases charts: {sorted(missing)}")

        # Types
        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
        df = df.dropna(subset=["order_date", "email"]).copy()
        df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

        # Ensure city exists
        if "city" in df.columns:
            df["city"] = df["city"].fillna("unknown").astype(str).str.strip()
            df.loc[df["city"] == "", "city"] = "unknown"
        else:
            df["city"] = "unknown"

        # Gender normalization
        if "gender" in df.columns:
            df["gender"] = df["gender"].fillna("unknown").astype(str).str.strip().str.lower()
            df.loc[df["gender"] == "", "gender"] = "unknown"
        else:
            df["gender"] = "unknown"

        # Cutoff: end of yesterday in Bogota
        # order_date is already Bogota local time, we treat it as naive local time
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

        # Repurchase flag computed from ALL orders (within cutoff)
        email_counts = df["email"].value_counts(dropna=True)
        repeat_emails = set(email_counts[email_counts > 1].index)

        first_order_dt = df.groupby("email")["order_date"].min()
        df = df.join(first_order_dt, on="email", rsuffix="_first")
        df["is_repurchase"] = (df["email"].isin(repeat_emails)) & (df["order_date"] > df["order_date_first"])

        # Apply selected date range AFTER classification
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

        # Normalize utm_source (blank -> undefined)
        norm = df["utm_source"].fillna("").astype(str).str.strip().str.lower()
        norm = norm.replace({"nan": "", "none": ""})
        norm = norm.where(norm != "", "undefined")
        df["_utm_source_norm"] = norm

        rep_df = df[df["is_repurchase"]].copy()

        total_repurchases_sales_cop = float(rep_df["total_value"].sum()) if not rep_df.empty else 0.0

        # TOTAL gender pie
        gender_group_total = rep_df.groupby("gender")["total_value"].sum().sort_values(ascending=False)
        gender_labels_total = [str(k) for k, v in gender_group_total.items() if float(v) > 0]
        gender_values_total = [float(v) for v in gender_group_total.values if float(v) > 0]

        # PIE: repurchase sales by channel
        grouped = rep_df.groupby("_utm_source_norm")["total_value"].sum().sort_values(ascending=False)
        pie_labels = [str(k) for k, v in grouped.items() if float(v) > 0]
        pie_values = [float(v) for v in grouped.values if float(v) > 0]

        total_rep_sales = float(sum(pie_values)) if pie_values else 0.0
        MIN_SHARE_PERCENT = 2.0

        included = []
        excluded = []

        if total_rep_sales > 0:
            for ch, val in grouped.items():
                val = float(val)
                if val <= 0:
                    continue
                pct = (val / total_rep_sales) * 100.0
                ch_str = str(ch)
                if pct >= MIN_SHARE_PERCENT:
                    included.append({"key": ch_str, "pct": pct, "value": val})
                else:
                    excluded.append({"key": ch_str, "pct": pct, "value": val})

        included.sort(key=lambda x: x["pct"], reverse=True)
        excluded.sort(key=lambda x: x["pct"], reverse=True)

        other_channels_labels = [x["key"] for x in excluded]
        other_channels_pct = float(sum(x["pct"] for x in excluded)) if excluded else 0.0

        # CITY PIE (TOTAL): Top 20 + "Other cities" slice
        TOP_N_CITIES = 20
        OTHER_CITY_LABEL = "Other cities"

        city_group_total = rep_df.groupby("city")["total_value"].sum()
        city_labels_total, city_values_total = build_top_n_city_pie(
            city_group_total,
            top_n=TOP_N_CITIES,
            other_label=OTHER_CITY_LABEL,
        )

        # HOUR PIE (TOTAL): 3-hour chunks
        hour_labels_total, hour_values_total = build_3hour_bucket_pie(rep_df)

        # Channel charts + gender pies + city pies + hour pies
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

            # Gender pie by channel
            gg = rep_ch.groupby("gender")["total_value"].sum().sort_values(ascending=False)
            g_labels = [str(k) for k, v in gg.items() if float(v) > 0]
            g_values = [float(v) for v in gg.values if float(v) > 0]
            gender_pies_by_channel[channel] = {"labels": g_labels, "values": g_values}

            # City pie by channel: Top 20 + "Other cities" slice
            cg = rep_ch.groupby("city")["total_value"].sum()
            ch_labels, ch_values = build_top_n_city_pie(
                cg,
                top_n=TOP_N_CITIES,
                other_label=OTHER_CITY_LABEL,
            )
            city_pies_by_channel[channel] = {"labels": ch_labels, "values": ch_values}

            # Hour pie by channel: 3-hour chunks
            h_labels, h_values = build_3hour_bucket_pie(rep_ch)
            hour_pies_by_channel[channel] = {"labels": h_labels, "values": h_values}

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



@main.route("/generate_repeated_customers",methods=["POST"])
@login_required
def generate_repeated_customers():

    input_file = "data/all_orders.csv"    
    repeated_customers_file = "data/repeated_customers.csv"

    try:
       
        print_customers_with_multiple_purchases(input_file,repeated_customers_file)
        
    except Exception as e:
        # If something goes wrong, pass an error message to the template
         return jsonify({
                "status": "success",
                "message": "error",
            }), 200

    # If everything goes well, render the monthly_data.html template
    return jsonify({
                "status": "success",
                "message": "data/repeated_customers.csv created",
            }), 200

@main.route("/rankings")
@login_required
def rankings():
    #input_file = "woo2024/data.csv"
    input_file = "data/rankings_orders.csv"
    repeated_customers_file = "data/repeated_customers.csv"
    try:
        date_range = Option.query.filter_by(meta_key="date_range_repurchases_orders.csv").first()
        if date_range:
            date_range = date_range.meta_value
        start_date = Option.query.filter_by(meta_key="start_date_repurchases_orders.csv").first()
        if start_date:
            start_date = start_date.meta_value
        end_date = Option.query.filter_by(meta_key="end_date_repurchases_orders.csv").first()
        if end_date:
            end_date = end_date.meta_value

        top_cities = get_top_cities_by_gender(input_file)
        top_hours = get_top_hours_by_gender(input_file)
        top_days_of_the_week = get_top_days_of_the_week(input_file)
        top_days_of_the_month = get_top_days_of_month_by_gender(input_file)
        top_months = get_top_10_months_by_sales(repeated_customers_file,input_file)
        top_twenty_days = get_top_twenty_days_by_sales(input_file)
        top_ten_mondays = get_top_ten_mondays_by_sales(input_file)
        top_ten_tuesdays = get_top_ten_tuesdays_by_sales(input_file) 
        top_ten_wednesdays = get_top_ten_wednesdays_by_sales(input_file)  # <-- new data
        top_ten_thursdays = get_top_ten_thursdays_by_sales(input_file) 
        top_ten_fridays = get_top_ten_fridays_by_sales(input_file)
        top_ten_saturdays = get_top_ten_saturdays_by_sales(input_file)  
        top_ten_sundays = get_top_ten_sundays_by_sales(input_file)
        orders_percentage = get_order_percentage_by_city(input_file)

    except Exception as e:
        return render_template(
            "rankings.html",
            error=str(e),
            top_cities=None,
            top_hours=None,
            top_days_of_the_week=None,
            top_days_of_the_month=None,
            top_months=None,
            top_twenty_days=None,
            top_ten_mondays=None,
            top_ten_tuesdays=None ,
            top_ten_wednesdays=None,
            top_ten_thursdays=None,
            top_ten_fridays=None,
            top_ten_saturdays=None,
            top_ten_sundays=None,
            top_ten_utm_campaigns=None,
            top_utm_answers=None,
            top_twenty_days_by_undefined_campaign=None,
            orders_percentage=None
            
        )
    # Renderizar la plantilla
    return render_template(
        "rankings.html",
        top_cities=top_cities,
        top_hours=top_hours,
        top_days_of_the_week=top_days_of_the_week,
        top_days_of_the_month=top_days_of_the_month,
        top_months=top_months,  # <-- Pass the new data to the template
        top_twenty_days=top_twenty_days,
        top_ten_mondays=top_ten_mondays,
        top_ten_tuesdays=top_ten_tuesdays,
        top_ten_wednesdays=top_ten_wednesdays,
        top_ten_thursdays=top_ten_thursdays,
        top_ten_fridays=top_ten_fridays,
        top_ten_saturdays=top_ten_saturdays,
        top_ten_sundays=top_ten_sundays,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        orders_percentage=orders_percentage
    )

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)



from flask import request, jsonify
from flask_login import login_required
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import os
import requests

from flask import request, jsonify
from flask_login import login_required
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import os
import requests
import logging

from .models import Option
from . import db

from app.services.get_data import (
    fetch_json_and_create_csv,
    DAILY_SALES_SCHEMA,
    _build_daily_sales_row,
)

logger = logging.getLogger(__name__)

# app/routes.py (ONLY the /get_data method updated)
from flask import request, jsonify
from flask_login import login_required
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import os
import requests
import logging

from .models import Option
from . import db

from app.services.get_data import (
    fetch_json_and_create_csv,
    DAILY_SALES_SCHEMA,
    _build_daily_sales_row,
)

logger = logging.getLogger(__name__)

from flask import jsonify, request
from flask_login import login_required
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from app.services.get_data import fetch_orders_and_write_csv


@main.route("/get_data", methods=["POST"])
@login_required
def get_data():
    option_api_key = Option.query.filter_by(meta_key="api_key").first()
    option_orders_url = Option.query.filter_by(meta_key="orders_url").first()

    if not option_api_key or not option_orders_url:
        error_message = "API key or orders URL not found in options."
        logger.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 400

    api_key = option_api_key.meta_value
    orders_url = option_orders_url.meta_value

    date_range = (request.form.get("date_range", "") or "").strip().lower()
    start_date = (request.form.get("start_date", "") or "").strip()
    end_date = (request.form.get("end_date", "") or "").strip()
    file_name = (request.form.get("file_name", "") or "").strip()

    if not file_name:
        file_name = "orders.csv"
    elif not file_name.lower().endswith(".csv"):
        file_name += ".csv"

    valid_ranges = [
        "yesterday",
        "today",
        "last_7_days",
        "last_14_days",  # NEW
        "last_30_days",
        "last_quarter",
        "current_month",
        "last_month",
        "year_to_date",
        "last_year",
        "lifetime",
    ]

    tz = ZoneInfo("America/Bogota")

    def fmt_ddmmyyyy(d: date) -> str:
        return d.strftime("%d/%m/%Y")

    def bogota_today_date() -> date:
        return datetime.now(tz).date()

    def preset_to_dates(preset: str) -> tuple[str, str]:
        today = bogota_today_date()
        yesterday = today - timedelta(days=1)

        if preset in ("today", "yesterday"):
            return fmt_ddmmyyyy(yesterday), fmt_ddmmyyyy(yesterday)

        if preset == "last_7_days":
            end = yesterday
            start = end - timedelta(days=6)
            return fmt_ddmmyyyy(start), fmt_ddmmyyyy(end)

        if preset == "last_14_days":  # NEW
            end = yesterday
            start = end - timedelta(days=13)
            return fmt_ddmmyyyy(start), fmt_ddmmyyyy(end)

        if preset == "last_30_days":
            end = yesterday
            start = end - timedelta(days=29)
            return fmt_ddmmyyyy(start), fmt_ddmmyyyy(end)

        if preset == "current_month":
            end = yesterday
            start = end.replace(day=1)
            return fmt_ddmmyyyy(start), fmt_ddmmyyyy(end)

        if preset == "last_month":
            first_this_month = today.replace(day=1)
            last_last_month = first_this_month - timedelta(days=1)
            start = last_last_month.replace(day=1)
            end = last_last_month
            return fmt_ddmmyyyy(start), fmt_ddmmyyyy(end)

        if preset == "year_to_date":
            end = yesterday
            start = end.replace(month=1, day=1)
            return fmt_ddmmyyyy(start), fmt_ddmmyyyy(end)

        if preset == "last_year":
            y = today.year - 1
            return fmt_ddmmyyyy(date(y, 1, 1)), fmt_ddmmyyyy(date(y, 12, 31))

        if preset == "lifetime":
            return fmt_ddmmyyyy(date(2022, 1, 1)), fmt_ddmmyyyy(yesterday)

        if preset == "last_quarter":
            end = yesterday
            start = end - timedelta(days=89)
            return fmt_ddmmyyyy(start), fmt_ddmmyyyy(end)

        return fmt_ddmmyyyy(date(2022, 1, 1)), fmt_ddmmyyyy(yesterday)

    def upsert_option(meta_key: str, meta_value: str):
        opt = Option.query.filter_by(meta_key=meta_key).first()
        if opt:
            opt.meta_value = meta_value
        else:
            db.session.add(Option(meta_key=meta_key, meta_value=meta_value))

    def delete_option(meta_key: str):
        opt = Option.query.filter_by(meta_key=meta_key).first()
        if opt:
            db.session.delete(opt)

    # Decide final dates
    if date_range in valid_ranges:
        final_date_range = date_range
        final_start_date, final_end_date = preset_to_dates(date_range)
    else:
        if not (start_date and end_date):
            error_message = (
                "Invalid input: please provide a valid date_range OR "
                "start_date & end_date in DD/MM/YYYY format."
            )
            logger.error(error_message)
            return jsonify({"status": "error", "message": error_message}), 400

        final_date_range = ""
        final_start_date = start_date
        final_end_date = end_date

    # Persist options (kept in controller, as requested)
    try:
        upsert_option(f"start_date_{file_name}", final_start_date)
        upsert_option(f"end_date_{file_name}", final_end_date)

        date_range_key = f"date_range_{file_name}"
        if final_date_range:
            upsert_option(date_range_key, final_date_range)
        else:
            delete_option(date_range_key)

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("Error saving date options: %s", e)
        return jsonify({"status": "error", "message": "Could not save date options."}), 500

    # Delegate API call + CSV creation to service
    ok, result = fetch_orders_and_write_csv(
        orders_url=orders_url,
        api_key=api_key,
        start_date=final_start_date,
        end_date=final_end_date,
        file_name=file_name,
    )

    if ok:
        return jsonify({
            "status": "success",
            "message": result["message"],
            "csv_url": result["csv_url"],
        }), 200

    return jsonify({
        "status": "error",
        "message": result["message"],
    }), 400


@main.route("/get_unknown_genders", methods=["POST"])
@login_required
def get_unknown_genders():
    try:
        # Load the CSV file
        data_file = "data/orders.csv"
        output_file = "data/unknown_genders.csv"
        unknown_genders = []

        with open(data_file, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row.get("gender") == "unknown":
                    # Add only the name in lowercase and gender columns
                    unknown_genders.append({
                        "name": row.get("name", "").lower(),
                        "gender": "unknown"
                    })

        # If no unknown genders, return success message
        if not unknown_genders:
            return jsonify({"status": "success", "message": "No unknown genders found!"}), 200

        # Write unknown genders to a new CSV file with only selected columns
        with open(output_file, mode="w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["name", "gender"])
            writer.writeheader()
            writer.writerows(unknown_genders)

        # Return success message with count and file path
        return jsonify({
            "status": "success",
            "message": f"Unknown genders saved to {output_file}",
            "details": unknown_genders[:10]  # Limit details to the first 10 for display
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@main.route('/options')
@login_required
def list_options():
    options = Option.query.all()
    return render_template('options_list.html', options=options)

@main.route('/options/new', methods=['GET', 'POST'])
@login_required
def create_option():
    if request.method == 'POST':
        meta_key = request.form.get('meta_key')
        meta_value = request.form.get('meta_value')
        
        # Input Validation
        if not meta_key or not meta_value:
            flash('Both meta key and meta value are required.', 'danger')
            return redirect(url_for('main.create_option'))
        
        # Check for duplicate meta_key
        existing_option = Option.query.filter_by(meta_key=meta_key).first()
        if existing_option:
            flash('An option with this meta key already exists.', 'danger')
            return redirect(url_for('main.create_option'))
        
        # Create and add the new option
        new_option = Option(meta_key=meta_key, meta_value=meta_value)
        try:
            db.session.add(new_option)
            db.session.commit()
            flash('Option added successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding option: {str(e)}', 'danger')
        
        return redirect(url_for('main.list_options'))
    
    return render_template('options_create.html')

@main.route('/options/<int:option_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_option(option_id):
    option = Option.query.get_or_404(option_id)
    
    if request.method == 'POST':
        meta_value = request.form.get('meta_value')
        
        if not meta_value:
            flash('Meta value cannot be empty.', 'danger')
            return redirect(url_for('main.edit_option', option_id=option_id))
        
        option.meta_value = meta_value
        try:
            db.session.commit()
            flash('Option updated successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating option: {str(e)}', 'danger')
        
        return redirect(url_for('main.list_options'))
    
    return render_template('options_edit.html', option=option)

@main.route('/options/<int:option_id>/delete', methods=['POST'])
@login_required
def delete_option(option_id):
    option = Option.query.get_or_404(option_id)
    
    try:
        db.session.delete(option)
        db.session.commit()
        flash('Option deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting option: {str(e)}', 'danger')
    
    return redirect(url_for('main.list_options'))
