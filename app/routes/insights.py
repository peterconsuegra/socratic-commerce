# app/routes/insights.py
import logging
import os

import pandas as pd
from flask import abort, current_app, jsonify, render_template, request, send_file
from flask_login import login_required

from app.models import Option
from app.services.bogota_insights import (
    build_city_filtered_csv,
    get_bogota_insights_view_data,
)
from app.services.checkout_insights import get_checkout_insights_daily
from app.services.facebook_insights import get_facebook_insights_daily
from app.services.google_insights import get_google_insights_daily
from app.services.wati_insights import get_wati_sales_trend

from . import main

logger = logging.getLogger(__name__)


@main.route("/wati_insights")
@login_required
def wati_insights():
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
            raise ValueError(
                f"Wati insights file is missing required columns: {sorted(missing)}"
            )

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

        df["city"] = df["city"].fillna("unknown").astype(str).str.strip()
        df.loc[df["city"] == "", "city"] = "unknown"

        df["product"] = df["product"].fillna("unknown").astype(str).str.strip()
        df.loc[df["product"] == "", "product"] = "unknown"

        df["hour"] = df["order_date"].dt.hour

        def hour_bucket_3h(hour: int) -> str:
            start = (int(hour) // 3) * 3
            end = start + 2
            return f"{start:02d}-{end:02d}"

        df["hour_bucket_3h"] = df["hour"].apply(hour_bucket_3h)
        bucket_order = [
            "00-02",
            "03-05",
            "06-08",
            "09-11",
            "12-14",
            "15-17",
            "18-20",
            "21-23",
        ]

        top_n_city = 8
        city_sales = df.groupby("city")["total_value"].sum().sort_values(ascending=False)
        city_top = city_sales.head(top_n_city)
        city_other = (
            float(city_sales.iloc[top_n_city:].sum())
            if len(city_sales) > top_n_city
            else 0.0
        )

        wati_city_labels = [str(x) for x in city_top.index]
        wati_city_values = [float(v) for v in city_top.values]

        if city_other > 0:
            wati_city_labels.append("Other cities")
            wati_city_values.append(city_other)

        time_counts = (
            df["hour_bucket_3h"]
            .value_counts()
            .reindex(bucket_order)
            .fillna(0)
        )

        wati_time_labels = [str(x) for x in time_counts.index]
        wati_time_values = [int(v) for v in time_counts.values]

        top_n_product = 8
        product_sales = df.groupby("product")["total_value"].sum().sort_values(ascending=False)
        product_top = product_sales.head(top_n_product)
        product_other = (
            float(product_sales.iloc[top_n_product:].sum())
            if len(product_sales) > top_n_product
            else 0.0
        )

        wati_product_labels = [str(x) for x in product_top.index]
        wati_product_values = [float(v) for v in product_top.values]

        if product_other > 0:
            wati_product_labels.append("Other products")
            wati_product_values.append(product_other)

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


@main.route("/checkout_insights")
@login_required
def checkout_insights():
    input_file = "data/checkout_insights_orders.csv"
    option_file_name = "checkout_insights_orders.csv"

    error = None

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
        date_range = rec.meta_value if rec else None

        if date_range:
            date_range = date_range.strip().lower() or None

    except Exception:
        logger.exception("Reading date range options failed (checkout_insights)")

    req_start = (request.args.get("start_date") or "").strip()
    req_end = (request.args.get("end_date") or "").strip()
    req_range = (request.args.get("date_range") or "").strip()

    if req_start and req_end:
        start_date = req_start
        end_date = req_end
        date_range = ""
    elif req_range:
        date_range = req_range
        start_date = ""
        end_date = ""

    empty_context = {
        "error": error,
        "date_range": date_range,
        "start_date": start_date,
        "end_date": end_date,
        "total_daily_trend": [],
        "forecast_data": [],
        "pie_labels": [],
        "pie_values": [],
        "answer_charts": [],
        "other_answers_labels": [],
        "other_answers_pct": 0.0,
        "gender_labels_total": [],
        "gender_values_total": [],
        "city_labels_total": [],
        "city_values_total": [],
        "city_other_label_total": None,
        "hour_labels_total": [],
        "hour_values_total": [],
    }

    if not os.path.exists(input_file):
        empty_context["error"] = (
            f"{input_file} not found. Use the date selector above to fetch data first."
        )
        return render_template("checkout_insights.html", **empty_context)

    context = empty_context.copy()

    try:
        result = get_checkout_insights_daily(
            orders_csv_path=input_file,
            forecast_periods=30,
            min_share_percent=2.0,
            start_date=start_date if (start_date and end_date) else None,
            end_date=end_date if (start_date and end_date) else None,
            top_cities=20,
        )

        context.update({
            "total_daily_trend": result["total_daily_trend"],
            "forecast_data": result["forecast_data"],
            "pie_labels": result["pie_labels"],
            "pie_values": result["pie_values"],
            "answer_charts": result["answer_charts"],
            "other_answers_labels": result["other_answers_labels"],
            "other_answers_pct": result["other_answers_pct"],
            "gender_labels_total": result["gender_labels_total"],
            "gender_values_total": result["gender_values_total"],
            "city_labels_total": result["city_labels_total"],
            "city_values_total": result["city_values_total"],
            "city_other_label_total": result.get("city_other_label_total"),
            "hour_labels_total": result["hour_labels_total"],
            "hour_values_total": result["hour_values_total"],
        })

    except Exception as e:
        logger.exception("checkout_insights view failed")
        context["error"] = str(e)

    return render_template("checkout_insights.html", **context)


@main.route("/bogota_insights")
@login_required
def bogota_insights():
    source_file = "data/bogota_sales_orders.csv"
    filtered_file = "data/bogota_sales_orders_bogota_only.csv"
    bogota_city_value = "BOGOTA (C/MARCA)"

    try:
        ctx = get_bogota_insights_view_data(
            OptionModel=Option,
            source_file=source_file,
            filtered_file=filtered_file,
            city_value=bogota_city_value,
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


def _render_bogota_insights_by_gender(gender: str):
    source_file = f"data/bogota_insights_{gender}.csv"
    filtered_file = f"data/bogota_insights_{gender}_filtered.csv"
    template_name = f"bogota_insights_{gender}.html"
    bogota_city_value = "BOGOTA (C/MARCA)"

    try:
        if not os.path.exists(source_file):
            return render_template(
                template_name,
                error=f"{source_file} not found. Use the date selector above to fetch data first.",
                date_range=None,
                start_date=None,
                end_date=None,
                total_daily_trend=[],
                forecast_data=[],
                campaign_groups=[],
                gender=gender,
            )

        if not os.path.exists(filtered_file):
            stats = build_city_filtered_csv(
                src_path=source_file,
                dst_path=filtered_file,
                target_city=bogota_city_value,
                gender=gender,
            )

            logger.info(
                "Filtered CSV created: %s (kept=%s removed=%s bad_rows=%s)",
                stats.get("output_file"),
                stats.get("kept"),
                stats.get("removed"),
                stats.get("bad_rows"),
            )
        else:
            logger.debug("Filtered CSV already exists. Skipping regeneration.")

        forecast_periods = 7 if gender == "female" else 30

        ctx = get_bogota_insights_view_data(
            OptionModel=Option,
            source_file=source_file,
            filtered_file=filtered_file,
            city_value=bogota_city_value,
            forecast_periods=forecast_periods,
            gender=gender,
            logger=logger,
        )

    except Exception as e:
        logger.exception("bogota_insights_%s view failed", gender)
        ctx = {
            "error": str(e),
            "date_range": None,
            "start_date": None,
            "end_date": None,
            "total_daily_trend": [],
            "forecast_data": [],
            "campaign_groups": [],
            "gender": gender,
        }

    return render_template(template_name, **ctx)


@main.route("/bogota_insights_female")
@login_required
def bogota_insights_female():
    return _render_bogota_insights_by_gender("female")


@main.route("/bogota_insights_male")
@login_required
def bogota_insights_male():
    return _render_bogota_insights_by_gender("male")


@main.route("/downloads/bogota_insights/<path:filename>")
@login_required
def download_bogota_insights_file(filename):
    allowed_files = {
        "bogota_insights_female_filtered.csv",
        "bogota_insights_male_filtered.csv",
    }

    filename = (filename or "").strip()

    if filename not in allowed_files:
        abort(404)

    project_root = os.path.abspath(os.path.join(current_app.root_path, ".."))
    file_path = os.path.join(project_root, "data", filename)

    if not os.path.exists(file_path):
        abort(404)

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv",
    )


def _render_ad_platform_insights(
    *,
    platform: str,
    input_file: str,
    template_name: str,
    service_func,
):
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

    total_content_pie_labels = []
    total_content_pie_values = []

    try:
        rec = Option.query.filter_by(meta_key=f"date_range_{platform}_sales_orders.csv").first()
        date_range = rec.meta_value if rec else None

        rec = Option.query.filter_by(meta_key=f"start_date_{platform}_sales_orders.csv").first()
        start_date = rec.meta_value if rec else None

        rec = Option.query.filter_by(meta_key=f"end_date_{platform}_sales_orders.csv").first()
        end_date = rec.meta_value if rec else None

    except Exception:
        logger.exception("Reading date range options failed (%s_insights)", platform)

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Use the date selector above to fetch data first."

        return render_template(
            template_name,
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
        result = service_func(
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

        total_content_pie_labels = result.get("total_content_pie_labels", [])
        total_content_pie_values = result.get("total_content_pie_values", [])

    except Exception as e:
        logger.exception("%s_insights view failed", platform)
        error = str(e)

    return render_template(
        template_name,
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


@main.route("/facebook_insights")
@login_required
def facebook_insights():
    return _render_ad_platform_insights(
        platform="facebook",
        input_file="data/facebook_sales_orders.csv",
        template_name="facebook_insights.html",
        service_func=get_facebook_insights_daily,
    )


@main.route("/google_insights")
@login_required
def google_insights():
    return _render_ad_platform_insights(
        platform="google",
        input_file="data/google_sales_orders.csv",
        template_name="google_insights.html",
        service_func=get_google_insights_daily,
    )


@main.route("/debug/barrio_test")
@login_required
def debug_barrio_test():
    from app.services.barrioResult import (
        barrio_from_address,
        barrio_legalizado_from_point,
    )

    sector_shp = os.path.abspath(
        os.path.join(current_app.root_path, "..", "barrios", "bogota", "SECTOR.shp")
    )
    barrio_shp = os.path.abspath(
        os.path.join(
            current_app.root_path,
            "..",
            "barrios",
            "bogota",
            "BarrioLegalizado.shp",
        )
    )

    if not os.path.exists(sector_shp):
        return jsonify({
            "ok": False,
            "error": f"Sector shapefile not found at {sector_shp}",
        }), 404

    if not os.path.exists(barrio_shp):
        return jsonify({
            "ok": False,
            "error": f"Barrio legalizado shapefile not found at {barrio_shp}",
        }), 404

    try:
        sector_result = barrio_from_address("Diagonal 51a #60f-53 sur", sector_shp)

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
                "No barrio name found. Check barrio_debug.matched_by and "
                "barrio_debug.nearest_distance_m. If matched_by=nearest and "
                "nearest_distance_m is large, CRS is wrong. If nearest_distance_m "
                "is small, polygon gaps exist and buffer_intersects should match."
            )

        return jsonify(payload)

    except Exception as e:
        logger.exception("debug_barrio_test failed")
        return jsonify({"ok": False, "error": str(e)}), 500