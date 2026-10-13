# app/routes/api.py
"""
The JSON API that ROAS Link and its MCP methods read: sales and repurchases by
utm_source, utm_campaign and utm_content (and WATI alone), for the trailing
presets or a custom range of Bogotá days (app/services/periods.py). Every
summary says its timezone and how fresh its orders are (orders_as_of).
"""
import logging

from flask import current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app.services.periods import ALL, PERIODS, PeriodError, resolve_windows
from app.services.utm_campaign_insights import get_utm_campaign_insights
from app.services.utm_campaign_summary import get_utm_campaign_summary, get_wati_summary
from app.services.utm_content_summary import CampaignNotFound, get_utm_content_summary
from app.services.utm_source_summary import get_utm_source_summary

from . import main
from .common import (
    API_ORDERS_MAX_AGE_SECONDS,
    api_access_required,
    refresh_all_orders_if_needed,
)

logger = logging.getLogger(__name__)


def _wants_fresh() -> bool:
    """True when the caller asks to bypass the orders cache (?refresh=true)."""
    return (request.args.get("refresh", "") or "").strip().lower() in {"1", "true", "yes"}


def _window_args() -> dict:
    """
    period, since and until from the query, as the summaries take them;
    checked before the orders are touched. Raises PeriodError (a 400, its
    message for the caller) for a request no window answers.
    """
    args = {
        "period": request.args.get("period", ALL),
        "since": request.args.get("since"),
        "until": request.args.get("until"),
    }
    resolve_windows(**args)
    return args


def _refresh_orders():
    """Refresh orders for an API call: force on ?refresh=true, else 1h freshness."""
    refresh_all_orders_if_needed(
        force=_wants_fresh(),
        max_age_seconds=API_ORDERS_MAX_AGE_SECONDS,
    )


@main.route("/api/health", methods=["GET"])
def api_health():
    """Unauthenticated health check for integrations (ROAS Link / MCP)."""
    return jsonify({"status": "ok", "service": "save-a-playa-data"}), 200


@main.route("/api/me", methods=["GET"])
@api_access_required
def api_me():
    """Verify credentials. Returns how the caller authenticated."""
    if current_user.is_authenticated:
        return jsonify({"status": "success", "auth": "session",
                        "user": current_user.username}), 200
    return jsonify({"status": "success", "auth": "api_token"}), 200


@main.route("/api_test/utm_source_summary", methods=["GET"])
@login_required
def api_test_utm_source_summary():
    """Interactive page to test the /api/utm_source_summary endpoint."""
    return render_template(
        "api_test_utm_source.html",
        periods=list(PERIODS),
    )


@main.route("/api_test/utm_campaign_insights", methods=["GET"])
@login_required
def api_test_utm_campaign_insights():
    """Interactive page to test the /api/utm_campaign_insights endpoint."""
    return render_template(
        "api_test_utm_campaign.html",
        periods=list(PERIODS),
    )


@main.route("/api_test/utm_campaign_summary", methods=["GET"])
@login_required
def api_test_utm_campaign_summary():
    """Interactive page to test the /api/utm_campaign_summary endpoint."""
    return render_template(
        "api_test_utm_campaign_summary.html",
        periods=list(PERIODS),
    )


@main.route("/api_test/utm_content_summary", methods=["GET"])
@login_required
def api_test_utm_content_summary():
    """Interactive page to test the /api/utm_content_summary endpoint."""
    return render_template(
        "api_test_utm_content_summary.html",
        periods=list(PERIODS),
    )


@main.route("/api_test/wati_summary", methods=["GET"])
@login_required
def api_test_wati_summary():
    """Interactive page to test the /api/wati_summary endpoint."""
    return render_template(
        "api_test_wati_summary.html",
        periods=list(PERIODS),
    )


@main.route("/api/utm_source_summary", methods=["GET"])
@api_access_required
def api_utm_source_summary():
    """
    Total sales and repurchase percentage grouped by utm_source for the
    selected trailing period.

    Query params:
        period: one of "today", "last_7d", "last_30d", "last_90d",
                "last_180d", "all" (default) to return every one of those,
                or "custom" for since to until.
        since, until: with period=custom, and only then (required): the
                first and last Bogotá days as YYYY-MM-DD, both included; at
                most 366 days, until no later than today. The period comes
                back under "custom", its start_date and end_date the same
                dates.

        refresh: "true" to bypass the orders cache and fetch the latest
                orders before computing (default: use cache, ~1h TTL).

    Each response also carries timezone ("America/Bogota") and orders_as_of,
    when the orders were last synced from the store (Bogotá time).

    Examples: GET /api/utm_source_summary?period=last_30d
              GET /api/utm_source_summary?period=custom&since=2026-01-01&until=2026-01-31
    """
    try:
        window_args = _window_args()
    except PeriodError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    try:
        _refresh_orders()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        result = get_utm_source_summary(orders_csv_path=orders_csv, **window_args)

        return jsonify({"status": "success", **result}), 200

    except Exception as e:
        logger.exception("Failed to build utm sales summary")
        return jsonify({"status": "error", "message": str(e)}), 500


@main.route("/api/utm_campaign_insights", methods=["GET"])
@api_access_required
def api_utm_campaign_insights():
    """
    Facebook insights (same data as /facebook_insights) grouped by
    utm_campaign for the selected trailing period.

    Query params:
        period: one of "today", "last_7d", "last_30d", "last_90d",
                "last_180d", or "all" (default) to return every period.
        min_share_percent: campaigns below this share of the period's facebook
                sales are folded into "other_campaigns" (default 2.0).

        refresh: "true" to bypass the orders cache and fetch the latest
                orders before computing (default: use cache, ~1h TTL).

    Example: GET /api/utm_campaign_insights?period=last_30d
    """
    period = (request.args.get("period", "all") or "all").strip().lower()

    valid = ["all"] + list(PERIODS)
    if period not in valid:
        return jsonify({
            "status": "error",
            "message": f"Invalid period '{period}'. Valid values: {valid}",
        }), 400

    try:
        min_share = float(request.args.get("min_share_percent", 2.0))
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "min_share_percent must be a number.",
        }), 400

    try:
        _refresh_orders()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        result = get_utm_campaign_insights(
            period=period,
            orders_csv_path=orders_csv,
            min_share_percent=min_share,
        )

        return jsonify({"status": "success", **result}), 200

    except Exception as e:
        logger.exception("Failed to build utm campaign insights")
        return jsonify({"status": "error", "message": str(e)}), 500


@main.route("/api/utm_campaign_summary", methods=["GET"])
@api_access_required
def api_utm_campaign_summary():
    """
    Total sales and repurchase percentage grouped by utm_campaign for the
    selected trailing period. Each campaign also includes, same as the
    /facebook_insights view (each a list of {dimension, sales} objects):
      - time_slot_sales:    [{"time_slot": "00:00-02:59", "sales": N}, ...]
      - gender_share_sales: [{"gender": "Female", "sales": N}, ...]
      - city_share_sales:   [{"city": "Bogota (C/Marca)", "sales": N}, ...] (top 12 + "Other")

    Query params:
        period: one of "today", "last_7d", "last_30d", "last_90d",
                "last_180d", "all" (default) to return every one of those,
                or "custom" for since to until.
        since, until: with period=custom, and only then (required): the
                first and last Bogotá days as YYYY-MM-DD, both included; at
                most 366 days, until no later than today. The period comes
                back under "custom", its start_date and end_date the same
                dates.
        limit: max campaigns per period (top N by sales); the rest are rolled
                up into "others". Pass 0 for all campaigns. Default 50.

        refresh: "true" to bypass the orders cache and fetch the latest
                orders before computing (default: use cache, ~1h TTL).

    Example: GET /api/utm_campaign_summary?period=last_30d&limit=20
    """
    try:
        window_args = _window_args()
    except PeriodError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "limit must be an integer.",
        }), 400

    try:
        _refresh_orders()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        result = get_utm_campaign_summary(
            orders_csv_path=orders_csv,
            limit=limit,
            **window_args,
        )

        return jsonify({"status": "success", **result}), 200

    except Exception as e:
        logger.exception("Failed to build utm campaign summary")
        return jsonify({"status": "error", "message": str(e)}), 500


@main.route("/api/wati_summary", methods=["GET"])
@api_access_required
def api_wati_summary():
    """
    Total sales and repurchase percentage for the utm_campaign "wati" only,
    with all other campaigns stripped out.

    Same metrics and breakdowns as /api/utm_campaign_summary (time_slot_sales,
    gender_share_sales, city_share_sales), but scoped to a single campaign, so
    there is no by_utm_campaign list and no "others" bucket. Values match the
    "wati" row of /api/utm_campaign_summary exactly.

    Query params:
        period: one of "today", "last_7d", "last_30d", "last_90d",
                "last_180d", "all" (default) to return every one of those,
                or "custom" for since to until.
        since, until: with period=custom, and only then (required): the
                first and last Bogotá days as YYYY-MM-DD, both included; at
                most 366 days, until no later than today. The period comes
                back under "custom", its start_date and end_date the same
                dates.
        refresh: "true" to bypass the orders cache and fetch the latest
                orders before computing (default: use cache, ~1h TTL).

    Example: GET /api/wati_summary?period=last_30d
    """
    try:
        window_args = _window_args()
    except PeriodError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    try:
        _refresh_orders()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        result = get_wati_summary(orders_csv_path=orders_csv, **window_args)

        return jsonify({"status": "success", **result}), 200

    except Exception as e:
        logger.exception("Failed to build wati summary")
        return jsonify({"status": "error", "message": str(e)}), 500


@main.route("/api/utm_content_summary", methods=["GET"])
@api_access_required
def api_utm_content_summary():
    """
    Total sales and repurchase percentage grouped by utm_content for a single
    campaign (the drill-down of /api/utm_campaign_summary).

    Each utm_content row (plus "others" and the period totals) also includes
    gender_share_sales: [{"gender": "Female"|"Male"|"Other/Unknown",
    "sales": N, "orders": N}, ...], using the same gender normalization as
    the /facebook_insights view.

    Query params:
        campaign_name: REQUIRED. The campaign to break down, matched
                case-insensitively against utm_campaign (use the value returned
                by /api/utm_campaign_summary).
        period: one of "today", "last_7d", "last_30d", "last_90d",
                "last_180d", "all" (default) to return every one of those,
                or "custom" for since to until.
        since, until: with period=custom, and only then (required): the
                first and last Bogotá days as YYYY-MM-DD, both included; at
                most 366 days, until no later than today. The period comes
                back under "custom", its start_date and end_date the same
                dates.
        limit: max utm_content rows per period (top N by sales); the rest are
                rolled up into "others". Pass 0 for all. Default 50.
        refresh: "true" to bypass the orders cache and fetch the latest
                orders before computing (default: use cache, ~1h TTL).

    Example: GET /api/utm_content_summary?campaign_name=wati&period=last_30d
    """
    campaign_name = (request.args.get("campaign_name", "") or "").strip()
    if not campaign_name:
        return jsonify({
            "status": "error",
            "message": "campaign_name is required.",
        }), 400

    try:
        window_args = _window_args()
    except PeriodError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "limit must be an integer.",
        }), 400

    try:
        _refresh_orders()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        result = get_utm_content_summary(
            campaign_name=campaign_name,
            orders_csv_path=orders_csv,
            limit=limit,
            **window_args,
        )

        return jsonify({"status": "success", **result}), 200

    except CampaignNotFound as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "suggestions": e.suggestions,
        }), 404

    except Exception as e:
        logger.exception("Failed to build utm content summary")
        return jsonify({"status": "error", "message": str(e)}), 500
