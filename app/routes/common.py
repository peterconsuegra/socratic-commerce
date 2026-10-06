# app/routes/common.py
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_login import current_user

from app.models import ApiToken, Option
from app import db
from app.services.get_data import BOGOTA, fetch_orders_and_write_csv, orders_file_is_current


# How long a cached all_orders.csv is considered fresh for the in-app views
# (override with ALL_ORDERS_CACHE_TTL_SECONDS).
CACHE_TTL_SECONDS = int(os.getenv("ALL_ORDERS_CACHE_TTL_SECONDS", str(60 * 60 * 24)))

# Freshness threshold for the JSON API endpoints. Shorter so the current day's
# orders show up within the hour without forcing a re-fetch on every call
# (override with API_ORDERS_MAX_AGE_SECONDS).
API_ORDERS_MAX_AGE_SECONDS = int(os.getenv("API_ORDERS_MAX_AGE_SECONDS", str(60 * 60)))

# The store rebuilds every customer's purchase numbers at 03:30 Colombia time,
# so a copy fetched before 04:00 is stale from 04:00 on, whatever its age.
NIGHTLY_REBUILD_HOUR = 4


def _last_nightly_rebuild() -> float:
    """Epoch seconds of the most recent 04:00 in Colombia."""
    now = datetime.now(BOGOTA)
    boundary = now.replace(hour=NIGHTLY_REBUILD_HOUR, minute=0, second=0, microsecond=0)
    if boundary > now:
        boundary -= timedelta(days=1)
    return boundary.timestamp()


def get_option_value(meta_key: str, default=None):
    row = Option.query.filter_by(meta_key=meta_key).first()
    return row.meta_value if row and row.meta_value is not None else default


def external_url(endpoint: str, **values) -> str:
    """
    Absolute URL for a link that goes into a sent email. Forced to https
    except on localhost, because the app sits behind a proxy that terminates
    TLS and would otherwise report plain http.
    """
    host = request.host.split(":")[0]
    scheme = request.scheme if host in ("localhost", "127.0.0.1") else "https"
    return url_for(endpoint, _external=True, _scheme=scheme, **values)


def should_refresh_all_orders(max_age_seconds: int | None = None) -> bool:
    if max_age_seconds is None:
        max_age_seconds = CACHE_TTL_SECONDS

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

    if not orders_file_is_current(csv_path):
        current_app.logger.info("all_orders.csv predates the store's repurchase fields; refresh required")
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

    expired = (time.time() - last_ts) > max_age_seconds or last_ts < _last_nightly_rebuild()

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
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Fetch the orders into data/<file_name>: the whole history without dates."""
    # The token is configuration (the options table), never code: it is
    # replaced from time to time.
    orders_url = get_option_value("orders_url")
    api_key = get_option_value("api_key")

    if not orders_url:
        raise ValueError("Missing 'orders_url' in options table")

    if not api_key:
        raise ValueError("Missing 'api_key' in options table")

    ok, payload = fetch_orders_and_write_csv(
        orders_url=orders_url,
        api_key=api_key,
        file_name=file_name,
        start_date=start_date,
        end_date=end_date,
        cwd=current_app.config["PROJECT_ROOT"],
    )

    if not ok:
        raise RuntimeError(payload.get("message", f"Unknown error generating {file_name}"))

    return os.path.join(current_app.config["DATA_DIR"], file_name)


def generate_all_orders_csv() -> str:
    output_csv = build_orders_csv(file_name="all_orders.csv")

    touch_all_orders_cache()
    return output_csv


def resync_range_files() -> list[str]:
    """
    Re-fetch every date-range order file a page saved through the date
    selector, over its saved dates (a page otherwise refreshes its file only
    when the range is picked again). Returns the files re-synced; a failure is
    logged and the old file kept.
    """
    synced = []
    for opt in Option.query.filter(Option.meta_key.like("start\\_date\\_%", escape="\\")).all():
        file_name = opt.meta_key[len("start_date_"):]
        end_date = get_option_value(f"end_date_{file_name}")
        path = os.path.join(current_app.config["DATA_DIR"], file_name)
        if file_name != os.path.basename(file_name) or not end_date or not os.path.exists(path):
            continue
        try:
            build_orders_csv(file_name=file_name, start_date=opt.meta_value, end_date=end_date)
            synced.append(file_name)
        except Exception:
            current_app.logger.exception("Re-sync of %s failed; keeping the old file", file_name)
    return synced


# The orders API can take minutes to answer for the full history, longer than
# the web worker's request timeout. So when a usable file already exists the
# refresh runs in a background thread and the request is served from the
# file on disk; only a first run with no file at all waits for the API.
_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH_FAILURE = 0.0
RETRY_AFTER_FAILURE_SECONDS = int(os.getenv("ALL_ORDERS_RETRY_AFTER_FAILURE_SECONDS", str(10 * 60)))


def _refresh_in_background(app):
    """Start one refresh per worker at a time; a second caller just returns."""
    if not _REFRESH_LOCK.acquire(blocking=False):
        app.logger.info("all_orders.csv refresh already running; serving the cached file")
        return

    def run():
        global _LAST_REFRESH_FAILURE
        try:
            with app.app_context():
                app.logger.info("Refreshing all_orders.csv from API in the background")
                generate_all_orders_csv()
                app.logger.info("all_orders.csv refreshed and cached")
        except Exception:
            # The stale file stays in use; retry after a pause rather than on
            # every request while the API is down.
            _LAST_REFRESH_FAILURE = time.time()
            app.logger.exception("Background refresh of all_orders.csv failed; keeping the cached file")
        finally:
            _REFRESH_LOCK.release()

    threading.Thread(target=run, name="all-orders-refresh", daemon=True).start()


def refresh_all_orders_if_needed(force: bool = False, max_age_seconds: int | None = None):
    csv_path = current_app.config["ALL_ORDERS_CSV"]

    if not force and not should_refresh_all_orders(max_age_seconds=max_age_seconds):
        current_app.logger.info("Using cached all_orders.csv")
        return

    if force:
        current_app.logger.info("Forced refresh of all_orders.csv requested")

    have_usable_cache = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0

    if have_usable_cache and not force:
        if time.time() - _LAST_REFRESH_FAILURE < RETRY_AFTER_FAILURE_SECONDS:
            current_app.logger.info("all_orders.csv refresh failed recently; serving the cached file")
            return
        _refresh_in_background(current_app._get_current_object())
        return

    current_app.logger.info("Refreshing all_orders.csv from API")

    try:
        generate_all_orders_csv()
    except Exception as e:
        current_app.logger.exception("Failed to refresh all_orders.csv")
        # If we already have a usable (if slightly stale) CSV, serve it rather
        # than failing the request — important now that the TTL is short.
        if have_usable_cache:
            current_app.logger.warning(
                "Refresh failed; serving existing all_orders.csv. Reason: %s", e
            )
            return
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


def _extract_api_token() -> str | None:
    """Pull a raw API token from the Authorization/X-API-Key headers (or query)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()

    header_key = request.headers.get("X-API-Key")
    if header_key:
        return header_key.strip()

    # Convenience for quick testing; header is strongly preferred.
    qp = request.args.get("api_token")
    return qp.strip() if qp else None


def api_access_required(view_func):
    """
    Allow access to JSON API endpoints via EITHER an authenticated browser
    session OR a valid API token (Authorization: Bearer <token> / X-API-Key).

    Used for integrations such as ROAS Link and its MCP methods.
    """
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        # Logged-in users (the in-app API test pages) pass through.
        if current_user.is_authenticated:
            return view_func(*args, **kwargs)

        raw = _extract_api_token()
        if raw:
            token = ApiToken.verify(raw)
            if token:
                token.last_used_at = datetime.now(timezone.utc)
                db.session.commit()
                return view_func(*args, **kwargs)

        return jsonify({
            "status": "error",
            "message": "Unauthorized. Provide a valid API token via the "
                       "'Authorization: Bearer <token>' header.",
        }), 401

    return wrapped