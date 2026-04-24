# app/routes/top.py
import io
import os
from datetime import datetime

import pandas as pd
from flask import abort, current_app, render_template, request, send_file
from flask_login import login_required

from app.models import Option
from app.services.localidades import LOCALIDADES_ESTRATOS, find_localidad
from app.services.top_cities import get_top_cities_daily_trend_with_forecast
from app.services.top_cities_gender import get_top_cities_gender_daily_trend_with_forecast

from . import main


ALLOWED_TOP_VALUES = {10, 20, 30, 50}


def _get_top_n(default=10):
    try:
        top_n = int(request.args.get("top_number", default))
    except (TypeError, ValueError):
        top_n = default

    if top_n not in ALLOWED_TOP_VALUES:
        top_n = default

    return top_n


def _read_date_options(file_name: str):
    date_range = start_date = end_date = None

    try:
        rec = Option.query.filter_by(meta_key=f"date_range_{file_name}").first()
        date_range = rec.meta_value if rec else None

        rec = Option.query.filter_by(meta_key=f"start_date_{file_name}").first()
        start_date = rec.meta_value if rec else None

        rec = Option.query.filter_by(meta_key=f"end_date_{file_name}").first()
        end_date = rec.meta_value if rec else None

    except Exception:
        current_app.logger.exception("Reading date range options failed for %s", file_name)

    return date_range, start_date, end_date


def _build_top_cities_response(
    *,
    input_file: str,
    template_name: str,
    option_file_name: str,
    top_n: int,
    service_kwargs: dict | None = None,
    missing_message: str | None = None,
    zero_message: str | None = None,
):
    service_kwargs = service_kwargs or {}
    date_range, start_date, end_date = _read_date_options(option_file_name)

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []
    error = None

    if not os.path.exists(input_file):
        error = missing_message or f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)

        return render_template(
            template_name,
            top_cities=top_cities_list,
            cities_daily_trend=cities_daily_trend,
            cities_forecast_trend=cities_forecast_trend,
            pie_labels=pie_labels,
            pie_values=pie_values,
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
        ) = get_top_cities_daily_trend_with_forecast(
            input_file,
            top_n=top_n,
            forecast_periods=14,
            **service_kwargs,
        )

        totals_map = {r["City"]: float(r["Total Sales"]) for r in city_totals_rows}
        top_sum = sum(totals_map.get(city, 0.0) for city in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(city, 0.0) for city in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

        if grand_total == 0 and zero_message:
            error = zero_message

    except Exception as e:
        error = str(e)
        current_app.logger.exception("%s view failed", template_name)

    return render_template(
        template_name,
        top_cities=top_cities_list,
        cities_daily_trend=cities_daily_trend,
        cities_forecast_trend=cities_forecast_trend,
        pie_labels=pie_labels,
        pie_values=pie_values,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        error=error,
        top_number=top_n,
    )


def _render_gender_top_cities(gender: str):
    input_file = f"data/top_cities_{gender}.csv"
    template_name = f"top_cities_{gender}.html"

    top_n = _get_top_n()

    date_range, start_date, end_date = _read_date_options(f"top_cities_{gender}.csv")

    top_cities_list = []
    cities_daily_trend = []
    cities_forecast_trend = []
    city_totals_rows = []
    pie_labels = []
    pie_values = []

    city_campaign_pies = {}
    city_content_pies = {}
    city_hour_pies = {}

    error = None

    if not os.path.exists(input_file):
        error = f"{input_file} not found. Generate the report first."
        current_app.logger.warning(error)

        return render_template(
            template_name,
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
        top_sum = sum(totals_map.get(city, 0.0) for city in top_cities_list)
        grand_total = sum(totals_map.values())
        others = max(grand_total - top_sum, 0.0)

        pie_labels = list(top_cities_list)
        pie_values = [totals_map.get(city, 0.0) for city in top_cities_list]

        if others > 0:
            pie_labels.append("Other cities")
            pie_values.append(others)

    except Exception as e:
        error = str(e)
        current_app.logger.exception("Top cities %s view failed", gender)

    return render_template(
        template_name,
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


def _to_float(value):
    try:
        if value is None:
            return None

        text = str(value).strip()

        if not text or text.lower() in {"nan", "none", "null", "undefined"}:
            return None

        return float(text)

    except Exception:
        return None


def _calc_bogota_fields(row):
    lat = _to_float(row.get("order_lat"))
    lng = _to_float(row.get("order_lng"))

    if lat is None or lng is None:
        return "", "", ""

    try:
        loc = find_localidad(lat, lng) or ""
        loc_key = str(loc).strip().upper()

        meta = LOCALIDADES_ESTRATOS.get(loc_key, {}) if loc_key else {}
        estrato = str(meta.get("estrato") or "").strip()
        nivel = str(meta.get("nivel_socioeconomico") or "").strip()

        return loc_key, estrato, nivel

    except Exception:
        return "", "", ""


def _download_gender_city_csv(gender: str):
    city = (request.args.get("city") or "").strip()

    if not city:
        abort(400)

    master_path = os.path.join(
        current_app.root_path,
        "..",
        "data",
        f"top_cities_{gender}.csv",
    )

    if not os.path.exists(master_path):
        current_app.logger.warning("Master file not found: %s", master_path)
        abort(404)

    try:
        df = pd.read_csv(master_path)

        if "city" not in df.columns:
            current_app.logger.warning("Master CSV missing required column: city")
            abort(500)

        df["city"] = df["city"].astype(str).fillna("").str.strip()

        requested_city = city.upper()
        df_city = df[df["city"].str.upper() == requested_city].copy()

        if "gender" in df_city.columns:
            df_city["gender"] = (
                df_city["gender"]
                .astype(str)
                .fillna("")
                .str.strip()
                .str.lower()
            )
            df_city = df_city[df_city["gender"] == gender].copy()

        if df_city.empty:
            abort(404)

        for col in ["order_lat", "order_lng"]:
            if col not in df_city.columns:
                df_city[col] = ""

        if requested_city == "BOGOTA (C/MARCA)":
            df_city[["localidad", "Estrato", "nivel_socioeconomico"]] = df_city.apply(
                lambda row: pd.Series(_calc_bogota_fields(row)),
                axis=1,
            )
        else:
            for col in ["localidad", "Estrato", "nivel_socioeconomico"]:
                if col not in df_city.columns:
                    df_city[col] = ""

        tail_cols = ["order_lat", "order_lng"]

        for col in ["localidad", "Estrato", "nivel_socioeconomico"]:
            if col in df_city.columns:
                tail_cols.append(col)

        base_cols = [col for col in df_city.columns if col not in set(tail_cols)]
        df_city = df_city[base_cols + tail_cols]

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")

        safe_city = "".join(
            ch for ch in requested_city if ch.isalnum() or ch in ("_", "-", " ", "(", ")")
        ).strip() or "CITY"
        safe_city = safe_city.replace(" ", "_")

        download_name = f"top_cities_{gender}_{safe_city}_{timestamp}.csv"

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
            "Failed preparing CSV download from master (top_cities_%s)",
            gender,
        )
        abort(500)


@main.route("/top_cities")
@login_required
def top_cities():
    top_n = _get_top_n()

    return _build_top_cities_response(
        input_file="data/top_cities.csv",
        template_name="top_cities.html",
        option_file_name="top_cities.csv",
        top_n=top_n,
    )


@main.route("/top_cities_female")
@login_required
def top_cities_female():
    return _render_gender_top_cities("female")


@main.route("/download/top_cities_female")
@login_required
def download_top_cities_female_city_csv():
    return _download_gender_city_csv("female")


@main.route("/top_cities_male")
@login_required
def top_cities_male():
    return _render_gender_top_cities("male")


@main.route("/download/top_cities_male")
@login_required
def download_top_cities_male_city_csv():
    return _download_gender_city_csv("male")


@main.route("/top_cities_wati")
@login_required
def top_cities_wati():
    top_n = _get_top_n()

    return _build_top_cities_response(
        input_file="data/top_cities_wati.csv",
        template_name="top_cities_wati.html",
        option_file_name="top_cities_wati.csv",
        top_n=top_n,
        service_kwargs={"utm_campaign_filter": "wati"},
        zero_message="No orders found for utm_campaign = wati in the selected date range.",
    )


@main.route("/top_cities_facebook")
@login_required
def top_cities_facebook():
    top_n = _get_top_n()

    return _build_top_cities_response(
        input_file="data/top_cities_facebook.csv",
        template_name="top_cities_facebook.html",
        option_file_name="top_cities_facebook.csv",
        top_n=top_n,
        service_kwargs={"utm_source_filter": "facebook"},
        zero_message="No orders found for utm_source = facebook in the selected date range.",
    )


@main.route("/top_cities_google")
@login_required
def top_cities_google():
    top_n = _get_top_n()

    return _build_top_cities_response(
        input_file="data/top_cities_google.csv",
        template_name="top_cities_google.html",
        option_file_name="top_cities_google.csv",
        top_n=top_n,
        service_kwargs={"utm_source_filter": "google"},
        zero_message="No orders found for utm_source = google in the selected date range.",
    )


@main.route("/top_cities_tiktok")
@login_required
def top_cities_tiktok():
    top_n = _get_top_n()

    return _build_top_cities_response(
        input_file="data/top_cities_tiktok.csv",
        template_name="top_cities_tiktok.html",
        option_file_name="top_cities_tiktok.csv",
        top_n=top_n,
        service_kwargs={"utm_source_filter": "tiktok"},
        zero_message="No orders found for utm_source = tiktok in the selected date range.",
    )


@main.route("/top_cities_ecostand")
@login_required
def top_cities_ecostand():
    top_n = _get_top_n()
    product_name = "Promoción Empresa: (24 unidades + ecostand)"

    return _build_top_cities_response(
        input_file="data/top_cities_ecostand.csv",
        template_name="top_cities_ecostand.html",
        option_file_name="top_cities_ecostand.csv",
        top_n=top_n,
        service_kwargs={"product_filter": product_name},
        zero_message=f'No orders found for product = "{product_name}" in the selected date range.',
    )


@main.route("/top_cities_ecohotel")
@login_required
def top_cities_ecohotel():
    top_n = _get_top_n()
    product_name = "Promoción Eco-Hotel: (24 unidades + 24 etiquetas con precio)"

    return _build_top_cities_response(
        input_file="data/top_cities_ecohotel.csv",
        template_name="top_cities_ecohotel.html",
        option_file_name="top_cities_ecohotel.csv",
        top_n=top_n,
        service_kwargs={"product_filter": product_name},
        zero_message=f'No orders found for product = "{product_name}" in the selected date range.',
    )