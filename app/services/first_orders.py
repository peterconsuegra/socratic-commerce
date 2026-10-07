# app/services/first_orders.py
"""
Where first-time orders come from: each customer's first paid purchase (the
store's purchase_number 1), by month, by utm_source, utm_campaign and
utm_content.

Two views of the same orders: a monthly trend by source since UTM tracking
began, and for any month since the first sale the breakdown source > campaign
> content. UTM values are
shown as the store recorded them: "undefined" means the order carried no UTM,
and "n/a" marks orders from before the store recorded UTMs.
"""
import logging
import threading

import pandas as pd

from app.services.get_data import BOGOTA, load_orders
from app.services.recurrent_customers import _file_key

logger = logging.getLogger(__name__)

# The trend chart's series: these sources by name, then every other source
# together, then the orders without a UTM. Fixed rather than ranked, so a
# source keeps its place and colour as the shares move.
TREND_SOURCES = ["wati", "facebook", "google", "ig"]
OTHER_SERIES = "other sources"
NO_UTM_SERIES = "no UTM (undefined, n/a)"
NO_UTM = {"undefined", "n/a", "(not set)"}
NOT_SET = "(not set)"
# The trend starts after August 2023: the store began recording UTMs in July
# and August 2023, and orders before carry "n/a". The month selector and the
# breakdown still reach back to the first sale.
TREND_START = pd.Period("2023-09", freq="M")
UTM_COLUMNS = ["utm_source", "utm_campaign", "utm_content"]

_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "first": None, "orders_by_month": None}


def _load(orders_csv_path: str) -> tuple[pd.DataFrame, pd.Series]:
    """(first-time orders, all orders per month), cached per orders-file version."""
    key = _file_key(orders_csv_path)
    if _CACHE["key"] == key:
        return _CACHE["first"], _CACHE["orders_by_month"]
    with _LOCK:
        if _CACHE["key"] != key:
            data = load_orders(orders_csv_path)
            month = data["order_date"].dt.to_period("M")
            first = data.loc[data["purchase_number"].eq(1), ["total_value", *UTM_COLUMNS]].copy()
            first["month"] = month[first.index]
            for col in UTM_COLUMNS:
                first[col] = first[col].str.strip().replace("", NOT_SET)
            first["utm_source"] = first["utm_source"].str.lower()
            _CACHE.update(key=key, first=first, orders_by_month=month.value_counts())
            logger.info("first-time orders rebuilt from %s (%d orders)", orders_csv_path, len(first))
        return _CACHE["first"], _CACHE["orders_by_month"]


def _row(name: str, orders: int, revenue: float, total: int) -> dict:
    return {"name": name, "orders": int(orders), "share": orders / total if total else 0.0,
            "revenue": float(revenue), "avg_value": revenue / orders if orders else 0.0}


def _ranked(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (-r["orders"], -r["revenue"], r["name"]))


def _breakdown(orders: pd.DataFrame) -> list[dict]:
    """The month's first-time orders as source > campaign > content, largest first."""
    total = len(orders)
    leaves = orders.groupby(UTM_COLUMNS).agg(orders=("total_value", "size"), revenue=("total_value", "sum"))
    sources = []
    for source, by_source in leaves.groupby(level="utm_source"):
        campaigns = []
        for campaign, by_campaign in by_source.groupby(level="utm_campaign"):
            contents = [_row(content, r.orders, r.revenue, total)
                        for (_s, _c, content), r in by_campaign.iterrows()]
            campaigns.append({**_row(campaign, by_campaign["orders"].sum(), by_campaign["revenue"].sum(), total),
                              "contents": _ranked(contents)})
        sources.append({**_row(source, by_source["orders"].sum(), by_source["revenue"].sum(), total),
                        "campaigns": _ranked(campaigns)})
    return _ranked(sources)


def _trend(first: pd.DataFrame, months: pd.PeriodIndex) -> dict:
    """First-time orders per month in the fixed series order, for a stacked chart."""
    source = first["utm_source"]
    series = source.where(source.isin(TREND_SOURCES), OTHER_SERIES).mask(source.isin(NO_UTM), NO_UTM_SERIES)
    counts = (
        first.groupby([first["month"], series]).size().unstack(fill_value=0)
        .reindex(index=months, columns=[*TREND_SOURCES, OTHER_SERIES, NO_UTM_SERIES], fill_value=0)
    )
    return {
        "months": [str(m) for m in months],
        "series": [{"name": name, "counts": [int(v) for v in counts[name]]} for name in counts.columns],
    }


def first_orders_report(orders_csv_path: str, month: str | None = None) -> dict:
    """
    The first-time orders report for one month (YYYY-MM): by default the last
    complete month, or the current one while it is the only month on record.
    """
    first, orders_by_month = _load(orders_csv_path)
    current = pd.Timestamp.now(tz=BOGOTA).tz_localize(None).to_period("M")
    if first.empty:
        return {"months": [], "selected": None, "trend": {"months": [], "series": []}, "breakdown": [], "kpis": None}

    months = pd.period_range(first["month"].min(), current, freq="M")
    per_month = first["month"].value_counts().reindex(months, fill_value=0)

    try:
        selected = pd.Period(month, freq="M") if month else None
    except (TypeError, ValueError):
        selected = None
    if selected not in months:
        selected = current - 1 if current - 1 in months else current

    orders = first[first["month"] == selected]
    total = len(orders)
    revenue = float(orders["total_value"].sum())
    all_orders = int(orders_by_month.get(selected, 0))

    return {
        "months": [{"month": str(m), "orders": int(per_month[m]), "partial": m == current}
                   for m in reversed(months)],
        "selected": {"month": str(selected), "partial": selected == current},
        "kpis": {
            "orders": total,
            "share_of_orders": total / all_orders if all_orders else 0.0,
            "all_orders": all_orders,
            "revenue": revenue,
            "avg_value": revenue / total if total else 0.0,
        },
        "trend": _trend(first, months[months >= TREND_START]),
        "breakdown": _breakdown(orders),
    }
