# app/routes/wati_stats.py
"""
/wati_stats: orders tagged utm_campaign=wati, the last 30 days next to the
last 7 days, so a change in pace shows at a glance.

The numbers come from get_wati_summary, the same service behind
/api/wati_summary, so this page matches what ROAS Link reads for "wati".
"""
import logging

from flask import current_app, render_template
from flask_login import login_required

from app.services.roxy_leads import RoxyLeadsError, fetch_new_leads
from app.services.utm_campaign_summary import get_wati_summary

from . import main
from .common import refresh_all_orders_if_needed
from .options import get_roxy_config

logger = logging.getLogger(__name__)

# The two windows compared, longest first: (period key, days in the window).
PERIODS = (("last_30d", 30), ("last_7d", 7))


def _pct_change(new: float, old: float) -> float | None:
    """Percent change from old to new; None when there is nothing to compare against."""
    if not old:
        return None
    return (new - old) / old * 100


def _period_stats(period: dict, days: int) -> dict:
    """The page's figures for one window: totals plus per-day rates and averages."""
    t = period["totals"]
    sales, orders = t["total_sales"], t["total_orders"]
    first_orders = orders - t["repurchase_orders"]
    return {
        "label": period["label"],
        "start_date": period["start_date"],
        "end_date": period["end_date"],
        "days": days,
        **t,
        "first_time_orders": first_orders,
        "first_time_sales": round(sales - t["repurchase_sales"], 2),
        "sales_per_day": sales / days,
        "orders_per_day": orders / days,
        "first_time_orders_per_day": first_orders / days,
        "avg_order_value": sales / orders if orders else 0.0,
    }


def _shares(rows: list[dict], key: str) -> dict[str, float]:
    """{label: % of the window's sales} for a breakdown list of {key, "sales"}."""
    total = sum(r["sales"] for r in rows)
    return {r[key]: (r["sales"] / total * 100 if total else 0.0) for r in rows}


def _compare_breakdown(long: dict, short: dict, field: str, key: str, sort_by_label: bool = False) -> list[dict]:
    """
    One breakdown (time slot, gender, city) side by side: each label's share of
    sales in both windows and the change in percentage points.
    """
    long_rows, short_rows = long[field], short[field]
    long_sales = {r[key]: r["sales"] for r in long_rows}
    short_sales = {r[key]: r["sales"] for r in short_rows}
    long_share, short_share = _shares(long_rows, key), _shares(short_rows, key)
    labels = list(long_sales) + [k for k in short_sales if k not in long_sales]
    if sort_by_label:
        labels.sort()
    rows = []
    for label in labels:
        a, b = long_share.get(label, 0.0), short_share.get(label, 0.0)
        rows.append({
            "label": label,
            "long_sales": long_sales.get(label, 0.0),
            "long_share": a,
            "short_sales": short_sales.get(label, 0.0),
            "short_share": b,
            "delta_pp": b - a,
        })
    return rows


def build_wati_stats(orders_csv_path: str) -> dict:
    summary = get_wati_summary(period="all", orders_csv_path=orders_csv_path)
    periods = summary["periods"]
    (long_key, long_days), (short_key, short_days) = PERIODS
    long, short = periods[long_key], periods[short_key]
    s30, s7 = _period_stats(long, long_days), _period_stats(short, short_days)

    # Rates are compared per day, so a 7-day window can be read against a 30-day one.
    comparisons = {
        k: _pct_change(s7[k], s30[k])
        for k in ("sales_per_day", "orders_per_day", "first_time_orders_per_day")
    }
    # Order value and shares only mean something when both windows have orders;
    # an empty week would otherwise read as a -100% order value.
    both = s7["total_orders"] > 0 and s30["total_orders"] > 0
    comparisons["avg_order_value"] = _pct_change(s7["avg_order_value"], s30["avg_order_value"]) if both else None
    # Shares are compared in percentage points.
    for k in ("repurchase_sales_percentage", "repurchase_orders_percentage"):
        comparisons[k] = s7[k] - s30[k] if both else None

    return {
        "generated_at": summary["generated_at"],
        "orders_as_of": summary["orders_as_of"],
        "timezone": summary["timezone"],
        "long": s30,
        "short": s7,
        "change": comparisons,
        "time_slots": _compare_breakdown(long, short, "time_slot_sales", "time_slot", sort_by_label=True),
        "gender": _compare_breakdown(long, short, "gender_share_sales", "gender"),
        "cities": _compare_breakdown(long, short, "city_share_sales", "city"),
    }


def _rate(orders: int, leads: int) -> float | None:
    return orders / leads * 100 if leads else None


def conversion_rates(stats: dict, fetch_leads) -> dict:
    """
    The WhatsApp conversion ratios for both windows, from Revive's new leads.

    Like over like, per Revive's handoff: first-time WATI orders over new
    leads, and all WATI orders over all arrivals (new leads + returning
    customers). These are orders in the window over leads who arrived in it,
    a trend ratio rather than a cohort conversion.

    fetch_leads(since, until) returns Revive's counts for those Bogotá days.
    Returns {"long": {...}, "short": {...}, "change": {...}}, or {"error": msg}.
    """
    out: dict = {}
    for key in ("long", "short"):
        s = stats[key]
        try:
            leads = fetch_leads(s["start_date"], s["end_date"])
        except RoxyLeadsError as e:
            return {"error": str(e)}
        out[key] = {
            **leads,
            "new_customer_rate": _rate(s["first_time_orders"], leads["new_leads"]),
            "all_rate": _rate(s["total_orders"], leads["all_arrivals"]),
            "new_leads_per_day": leads["new_leads"] / s["days"],
        }
    out["change"] = {
        k: (out["short"][k] - out["long"][k])
        if out["short"][k] is not None and out["long"][k] is not None else None
        for k in ("new_customer_rate", "all_rate")
    }
    out["change"]["new_leads_per_day"] = _pct_change(out["short"]["new_leads_per_day"],
                                                     out["long"]["new_leads_per_day"])
    # Above 100% means mismatched windows or repurchases in the numerator.
    out["suspect"] = any((out[k][r] or 0) > 100 for k in ("long", "short") for r in ("new_customer_rate", "all_rate"))
    return out


@main.route("/wati_stats")
@login_required
def wati_stats():
    """WATI (utm_campaign=wati) orders: last 30 days vs last 7 days."""
    error, stats, conversion = None, None, None
    try:
        refresh_all_orders_if_needed()
        stats = build_wati_stats(current_app.config["ALL_ORDERS_CSV"])
    except Exception as e:
        logger.exception("Failed to build WATI stats")
        error = str(e)

    if stats:
        roxy = get_roxy_config()
        if not roxy["token"]:
            conversion = {"not_configured": True}
        else:
            conversion = conversion_rates(
                stats, lambda since, until: fetch_new_leads(since, until, roxy["token"], roxy["base_url"]))
    return render_template("wati_stats.html", error=error, stats=stats, conversion=conversion)
