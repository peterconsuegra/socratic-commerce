# app/routes/data.py
import csv
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import jsonify, request
from flask_login import login_required

from app import db
from app.models import Option
from app.services.get_data import fetch_orders_and_write_csv

from . import main
from .common import generate_all_orders_csv

logger = logging.getLogger(__name__)


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
        logger.exception("Failed to generate all_orders.csv")

        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


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
        "last_14_days",
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

        if preset == "last_14_days":
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
            year = today.year - 1
            return fmt_ddmmyyyy(date(year, 1, 1)), fmt_ddmmyyyy(date(year, 12, 31))

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
        return jsonify({
            "status": "error",
            "message": "Could not save date options.",
        }), 500

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
        data_file = "data/orders.csv"
        output_file = "data/unknown_genders.csv"
        unknown_genders = []

        with open(data_file, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)

            for row in reader:
                if row.get("gender") == "unknown":
                    unknown_genders.append({
                        "name": row.get("name", "").lower(),
                        "gender": "unknown",
                    })

        if not unknown_genders:
            return jsonify({
                "status": "success",
                "message": "No unknown genders found!",
            }), 200

        with open(output_file, mode="w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["name", "gender"])
            writer.writeheader()
            writer.writerows(unknown_genders)

        return jsonify({
            "status": "success",
            "message": f"Unknown genders saved to {output_file}",
            "details": unknown_genders[:10],
        }), 200

    except Exception as e:
        logger.exception("Failed to export unknown genders")
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500