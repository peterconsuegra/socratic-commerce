# app/routes/common.py
import os
import time
from functools import wraps

from flask import current_app, flash, redirect, url_for
from flask_login import current_user

from app.models import Option
from app import db
from app.services.get_data import fetch_orders_and_write_csv


CACHE_TTL_SECONDS = 60 * 60 * 24


def get_option_value(meta_key: str, default=None):
    row = Option.query.filter_by(meta_key=meta_key).first()
    return row.meta_value if row and row.meta_value is not None else default


def should_refresh_all_orders() -> bool:
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
    project_root = current_app.config["PROJECT_ROOT"]
    output_csv = os.path.join(current_app.config["DATA_DIR"], file_name)

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    orders_url = get_option_value("orders_url")
    api_key = get_option_value("api_key")

    if not orders_url:
        raise ValueError("Missing 'orders_url' in options table")

    if not api_key:
        raise ValueError("Missing 'api_key' in options table")

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

    return output_csv


def generate_all_orders_csv() -> str:
    output_csv = build_orders_csv(
        file_name="all_orders.csv",
        send_date_params=False,
    )

    touch_all_orders_cache()
    return output_csv


def refresh_all_orders_if_needed():
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

    raise FileNotFoundError(f"Could not generate required file: {csv_path}")


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