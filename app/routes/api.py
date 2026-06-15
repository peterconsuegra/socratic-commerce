# app/routes/api.py
import logging

from flask import current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app.services.utm_campaign_insights import get_utm_campaign_insights
from app.services.utm_campaign_summary import get_utm_campaign_summary
from app.services.utm_source_summary import PERIODS, get_utm_source_summary

from . import main
from .common import api_access_required, refresh_all_orders_if_needed

logger = logging.getLogger(__name__)


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


@main.route("/api/utm_source_summary", methods=["GET"])
@api_access_required
def api_utm_source_summary():
    """
    Total sales and repurchase percentage grouped by utm_source for the
    selected trailing period.

    Query params:
        period: one of "today", "last_7d", "last_30d", "last_90d",
                "last_180d", or "all" (default) to return every period.

    Example: GET /api/utm_source_summary?period=last_30d
    """
    period = (request.args.get("period", "all") or "all").strip().lower()

    valid = ["all"] + list(PERIODS)
    if period not in valid:
        return jsonify({
            "status": "error",
            "message": f"Invalid period '{period}'. Valid values: {valid}",
        }), 400

    try:
        refresh_all_orders_if_needed()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        result = get_utm_source_summary(period=period, orders_csv_path=orders_csv)

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
        refresh_all_orders_if_needed()
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
    selected trailing period.

    Query params:
        period: one of "today", "last_7d", "last_30d", "last_90d",
                "last_180d", or "all" (default) to return every period.
        limit: max campaigns per period (top N by sales); the rest are rolled
                up into "others". Pass 0 for all campaigns. Default 50.

    Example: GET /api/utm_campaign_summary?period=last_30d&limit=20
    """
    period = (request.args.get("period", "all") or "all").strip().lower()

    valid = ["all"] + list(PERIODS)
    if period not in valid:
        return jsonify({
            "status": "error",
            "message": f"Invalid period '{period}'. Valid values: {valid}",
        }), 400

    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "limit must be an integer.",
        }), 400

    try:
        refresh_all_orders_if_needed()
        orders_csv = current_app.config["ALL_ORDERS_CSV"]
        result = get_utm_campaign_summary(
            period=period,
            orders_csv_path=orders_csv,
            limit=limit,
        )

        return jsonify({"status": "success", **result}), 200

    except Exception as e:
        logger.exception("Failed to build utm campaign summary")
        return jsonify({"status": "error", "message": str(e)}), 500
