# app/services/utm_source_summary.py
"""
Total sales and repurchase percentage grouped by utm_source, for the trailing
time windows (Today, Last 7/30/90/180 days) or a custom range of days (see
periods.py).

A "repurchase" is an order the store flags is_repurchase: the customer's
second or later paid purchase across their whole history, so a time window
never changes how an order is classified.
"""
import logging

import pandas as pd

from app.services.get_data import load_orders, orders_as_of
from app.services.periods import TIMEZONE, now_bogota_naive, resolve_windows

logger = logging.getLogger(__name__)


def _normalize_utm_source(series: pd.Series) -> pd.Series:
    norm = (
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    norm = norm.replace({"nan": "", "none": ""})
    norm = norm.where(norm != "", "undefined")
    return norm


def _round2(x: float) -> float:
    return round(float(x), 2)


def _load_orders(orders_csv_path: str) -> pd.DataFrame:
    # is_repurchase comes with each order from the store.
    data = load_orders(orders_csv_path)

    data["utm_source_norm"] = _normalize_utm_source(data["utm_source"])
    return data


def _summarize_window(window: pd.DataFrame) -> dict:
    """Build the by-utm_source breakdown and totals for an already-filtered window."""
    grouped = window.groupby("utm_source_norm")
    agg = grouped.agg(
        total_sales=("total_value", "sum"),
        total_orders=("order_id", "size") if "order_id" in window.columns else ("total_value", "size"),
    )
    rep = window[window["is_repurchase"]].groupby("utm_source_norm").agg(
        repurchase_sales=("total_value", "sum"),
        repurchase_orders=("total_value", "size"),
    )
    agg = agg.join(rep, how="left")
    agg["repurchase_sales"] = agg["repurchase_sales"].fillna(0.0)
    agg["repurchase_orders"] = agg["repurchase_orders"].fillna(0).astype(int)
    agg = agg.sort_values("total_sales", ascending=False)

    by_source = []
    for utm_source, row in agg.iterrows():
        total_sales = float(row["total_sales"])
        total_orders = int(row["total_orders"])
        rep_sales = float(row["repurchase_sales"])
        rep_orders = int(row["repurchase_orders"])
        by_source.append({
            "utm_source": utm_source,
            "total_sales": _round2(total_sales),
            "total_orders": total_orders,
            "repurchase_sales": _round2(rep_sales),
            "repurchase_orders": rep_orders,
            "repurchase_sales_percentage": _round2(rep_sales / total_sales * 100) if total_sales > 0 else 0.0,
            "repurchase_orders_percentage": _round2(rep_orders / total_orders * 100) if total_orders > 0 else 0.0,
        })

    total_sales = float(window["total_value"].sum())
    total_orders = int(len(window))
    rep_sales = float(window.loc[window["is_repurchase"], "total_value"].sum())
    rep_orders = int(window["is_repurchase"].sum())

    totals = {
        "total_sales": _round2(total_sales),
        "total_orders": total_orders,
        "repurchase_sales": _round2(rep_sales),
        "repurchase_orders": rep_orders,
        "repurchase_sales_percentage": _round2(rep_sales / total_sales * 100) if total_sales > 0 else 0.0,
        "repurchase_orders_percentage": _round2(rep_orders / total_orders * 100) if total_orders > 0 else 0.0,
    }
    return {"totals": totals, "by_utm_source": by_source}


def get_utm_source_summary(
    period: str = "all",
    orders_csv_path: str = "data/all_orders.csv",
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """
    Returns total sales and repurchase percentage grouped by utm_source.

    Args:
        period: one of periods.PERIODS keys ("today", "last_7d", "last_30d",
                "last_90d", "last_180d"), "all" to return every one, or
                "custom" for since to until.
        orders_csv_path: path to the all-orders CSV.
        since, until: with period "custom" only, the first and last Bogotá
                days (YYYY-MM-DD), both included.

    Raises periods.PeriodError for a period it cannot answer.

    Returns a dict shaped as:
        {
          "generated_at": "<ISO Bogota time>",
          "timezone": "America/Bogota",
          "orders_as_of": "<ISO Bogota time the orders were last synced>",
          "periods": {
             "last_30d": {
                "label": "Last 30 days",
                "start_date": "YYYY-MM-DD",
                "end_date": "YYYY-MM-DD",
                "totals": { total_sales, total_orders, repurchase_sales,
                            repurchase_orders, repurchase_sales_percentage,
                            repurchase_orders_percentage },
                "by_utm_source": [ { utm_source, total_sales, ... }, ... ]
             },
             ...
          }
        }
        (a custom period comes under "custom", labelled "YYYY-MM-DD – YYYY-MM-DD")
    """
    now = now_bogota_naive()
    windows = resolve_windows(period, since, until, now)
    data = _load_orders(orders_csv_path)

    out: dict = {
        "generated_at": now.isoformat(),
        "timezone": TIMEZONE,
        "orders_as_of": orders_as_of(orders_csv_path),
        "periods": {},
    }
    for window in windows:
        out["periods"][window.key] = {**window.describe(), **_summarize_window(window.orders(data, now))}

    return out
