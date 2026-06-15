# app/routes/api.py
import logging

from flask import current_app, jsonify, render_template, request
from flask_login import login_required

from app.services.utm_sales_summary import PERIODS, get_utm_sales_summary

from . import main
from .common import refresh_all_orders_if_needed

logger = logging.getLogger(__name__)


@main.route("/api_test/utm_sales_summary", methods=["GET"])
@login_required
def api_test_utm_sales_summary():
    """Interactive page to test the /api/utm_sales_summary endpoint."""
    return render_template(
        "api_test_utm_sales.html",
        periods=list(PERIODS),
    )


@main.route("/api/utm_sales_summary", methods=["GET"])
@login_required
def api_utm_sales_summary():
    """
    Total sales and repurchase percentage grouped by utm_source for the
    selected trailing period.

    Query params:
        period: one of "today", "last_7d", "last_30d", "last_90d",
                "last_180d", or "all" (default) to return every period.

    Example: GET /api/utm_sales_summary?period=last_30d
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
        result = get_utm_sales_summary(period=period, orders_csv_path=orders_csv)

        return jsonify({"status": "success", **result}), 200

    except Exception as e:
        logger.exception("Failed to build utm sales summary")
        return jsonify({"status": "error", "message": str(e)}), 500
