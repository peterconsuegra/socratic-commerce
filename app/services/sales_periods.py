# app/services/sales_periods.py
"""
Total sales by period, for the MCP server (routes/mcp.py): the time windows
an agent can ask for, the sales in one or every window (optionally split by
day, week or month), and two ranges side by side.

The windows are the API's own (periods.py): the trailing presets, which run
from N days back through now (so they include today so far), and a custom
range of Bogotá days, both ends included, at most 366 days, ending no later
than today. Sales are the order totals the store reports (COP), one row per
paid order, the same figures /api/utm_source_summary totals.

First-time and repurchase come from the store (purchase_number == 1 and
is_repurchase). An order the store has not numbered yet counts as neither
until the next sync; it shows under unnumbered_orders, never guessed.
"""
import pandas as pd

from app.services.get_data import load_orders, orders_as_of
from app.services.periods import (
    CUSTOM,
    MAX_CUSTOM_DAYS,
    PERIODS,
    TIMEZONE,
    PeriodError,
    Window,
    now_bogota_naive,
    resolve_windows,
)

BREAKDOWNS = ("none", "day", "week", "month")
CURRENCY = "COP"


def _round2(x) -> float:
    return round(float(x), 2)


def _pct(part: float, whole: float) -> float:
    return _round2(part / whole * 100) if whole else 0.0


def _metrics(orders: pd.DataFrame) -> dict:
    """The sales figures of a set of orders."""
    sales = float(orders["total_value"].sum())
    count = int(len(orders))
    first = orders["purchase_number"].eq(1)
    repeat = orders["is_repurchase"]
    first_sales = float(orders.loc[first, "total_value"].sum())
    repeat_sales = float(orders.loc[repeat, "total_value"].sum())
    return {
        "total_sales": _round2(sales),
        "total_orders": count,
        "average_order_value": _round2(sales / count) if count else 0.0,
        "first_time_orders": int(first.sum()),
        "first_time_sales": _round2(first_sales),
        "repurchase_orders": int(repeat.sum()),
        "repurchase_sales": _round2(repeat_sales),
        "repurchase_sales_percentage": _pct(repeat_sales, sales),
        "unnumbered_orders": int((orders["purchase_number"].isna()).sum()),
    }


def _bucket_start(dates: pd.Series, breakdown: str) -> pd.Series:
    days = dates.dt.normalize()
    if breakdown == "week":  # weeks start on Monday
        return days - pd.to_timedelta(days.dt.weekday, unit="D")
    if breakdown == "month":
        return days.dt.to_period("M").dt.start_time
    return days


def _buckets(window: Window, orders: pd.DataFrame, breakdown: str, now: pd.Timestamp) -> list[dict]:
    """
    One row per day, week or month of the window, empty ones included, each
    clipped to the window: a range from the 15th starts its first month there.
    """
    last_day = min(window.end - pd.Timedelta(days=1), now.normalize())
    days = pd.Series(pd.date_range(window.start, last_day, freq="D"))
    starts = _bucket_start(days, breakdown)
    bounds = days.groupby(starts).agg(["min", "max"])
    keyed = orders.assign(_bucket=_bucket_start(orders["order_date"], breakdown))
    groups = dict(tuple(keyed.groupby("_bucket")))
    empty = orders.iloc[0:0]
    rows = []
    for start, (first, last) in bounds.iterrows():
        rows.append({
            "start_date": f"{first:%Y-%m-%d}",
            "end_date": f"{last:%Y-%m-%d}",
            **_metrics(groups.get(start, empty)),
        })
    return rows


def _check_breakdown(breakdown: str | None) -> str:
    breakdown = (breakdown or "none").strip().lower()
    if breakdown not in BREAKDOWNS:
        raise PeriodError(f"Invalid breakdown '{breakdown}'. Valid values: {list(BREAKDOWNS)}")
    return breakdown


def _envelope(orders_csv_path: str, now: pd.Timestamp) -> dict:
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": TIMEZONE,
        "currency": CURRENCY,
        "orders_as_of": orders_as_of(orders_csv_path),
    }


def describe_periods(orders_csv_path: str, now: pd.Timestamp | None = None) -> dict:
    """The presets with today's dates, the custom-range rules and the span of the order history."""
    now = now if now is not None else now_bogota_naive()
    data = load_orders(orders_csv_path)
    presets = [
        {"period": w.key, **w.describe(), "days": PERIODS[w.key] + 1}
        for w in resolve_windows("all", now=now)
    ]
    return {
        **_envelope(orders_csv_path, now),
        "today": f"{now:%Y-%m-%d}",
        "presets": presets,
        "presets_note": "Each preset runs from its start_date through now, so it includes "
                        "today's orders so far (a partial day).",
        "custom": {
            "period": CUSTOM,
            "since_until_format": "YYYY-MM-DD",
            "both_days_included": True,
            "max_days": MAX_CUSTOM_DAYS,
            "latest_until": f"{now:%Y-%m-%d}",
        },
        "breakdowns": list(BREAKDOWNS),
        "order_history": {
            "first_order_date": f"{data['order_date'].min():%Y-%m-%d}" if len(data) else None,
            "last_order_date": f"{data['order_date'].max():%Y-%m-%d}" if len(data) else None,
            "total_orders": int(len(data)),
        },
    }


def sales_by_period(orders_csv_path: str, period: str = "all", since: str | None = None,
                    until: str | None = None, breakdown: str | None = "none",
                    now: pd.Timestamp | None = None) -> dict:
    """
    Sales for every preset ("all"), one preset, or a custom range, each with
    its first and last day; with a breakdown, also split by day, week (Monday
    first) or month. Raises PeriodError, its message for the caller.
    """
    now = now if now is not None else now_bogota_naive()
    breakdown = _check_breakdown(breakdown)
    windows = resolve_windows(period, since, until, now)
    data = load_orders(orders_csv_path)
    periods = {}
    for window in windows:
        orders = window.orders(data, now)
        entry = {**window.describe(), "totals": _metrics(orders)}
        if breakdown != "none":
            entry["breakdown"] = breakdown
            entry["rows"] = _buckets(window, orders, breakdown, now)
        periods[window.key] = entry
    return {**_envelope(orders_csv_path, now), "periods": periods}


def _custom(since: str | None, until: str | None, now: pd.Timestamp, name: str) -> Window:
    try:
        [window] = resolve_windows(CUSTOM, since, until, now)
    except PeriodError as e:
        raise PeriodError(f"{name}: {e}") from None
    return window


def compare_periods(orders_csv_path: str, since: str, until: str,
                    compare_since: str | None = None, compare_until: str | None = None,
                    now: pd.Timestamp | None = None) -> dict:
    """
    One custom range against another; without the second, against the same
    number of days right before the first. Changes are current minus
    previous, and the percentage of the previous (null when it was zero).
    """
    now = now if now is not None else now_bogota_naive()
    current = _custom(since, until, now, "period")
    if bool(compare_since) != bool(compare_until):
        raise PeriodError("Give both compare_since and compare_until, or neither for the previous period.")
    if compare_since:
        previous = _custom(compare_since, compare_until, now, "compare period")
    else:
        days = current.end - current.start
        previous = Window(CUSTOM, "", current.start - days, current.start)

    data = load_orders(orders_csv_path)
    a, b = _metrics(current.orders(data, now)), _metrics(previous.orders(data, now))
    change = {}
    for key, value in a.items():
        before = b[key]
        change[key] = {
            "difference": _round2(value - before),
            "percentage": _pct(value - before, before) if before else None,
        }

    def side(window: Window, totals: dict) -> dict:
        info = window.describe()
        info["label"] = f"{info['start_date']} – {info['end_date']}"
        return {**info, "days": (window.end - window.start).days, "totals": totals}

    return {
        **_envelope(orders_csv_path, now),
        "current": side(current, a),
        "previous": side(previous, b),
        "previous_is": "the given compare range" if compare_since else "the same number of days right before",
        "change": change,
    }

