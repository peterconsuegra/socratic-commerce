# app/services/utm_sales_summary.py
"""
Total sales and repurchase percentage grouped by utm_source, for a set of
trailing time windows (Today, Last 7/30/90/180 days).

Repurchase classification follows the same convention used across the app
(see daily_repurchases.py): an order is a "repurchase" if its email has more
than one order in the FULL history and the order is not that email's first
order. Classification is computed over ALL orders, then the time window is
applied only for the aggregation.
"""
import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)

# period key -> number of trailing days (today inclusive)
PERIODS: dict[str, int] = {
    "today": 1,
    "last_7d": 7,
    "last_30d": 30,
    "last_90d": 90,
    "last_180d": 180,
}


def _now_bogota_naive() -> pd.Timestamp:
    """Current Bogota time as a timezone-naive Timestamp (data is Bogota GMT-5)."""
    return pd.Timestamp.now(tz="America/Bogota").tz_localize(None)


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
    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    data = pd.read_csv(orders_csv_path)

    required_cols = {"email", "order_date", "total_value", "utm_source"}
    missing = required_cols - set(data.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    data["order_date"] = pd.to_datetime(data["order_date"], errors="coerce")
    data = data.dropna(subset=["order_date", "email"]).copy()
    data["total_value"] = pd.to_numeric(data["total_value"], errors="coerce").fillna(0.0)

    # Repurchase classification over the FULL history.
    email_counts = data["email"].value_counts(dropna=True)
    repeat_emails = set(email_counts[email_counts > 1].index)
    first_order_dt = data.groupby("email")["order_date"].min()
    data = data.join(first_order_dt, on="email", rsuffix="_first")
    data["is_repurchase"] = (
        data["email"].isin(repeat_emails)
        & (data["order_date"] > data["order_date_first"])
    )

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


def get_utm_sales_summary(
    period: str = "all",
    orders_csv_path: str = "data/all_orders.csv",
) -> dict:
    """
    Returns total sales and repurchase percentage grouped by utm_source.

    Args:
        period: one of PERIODS keys ("today", "last_7d", "last_30d",
                "last_90d", "last_180d") or "all" to return every period.
        orders_csv_path: path to the all-orders CSV.

    Returns a dict shaped as:
        {
          "generated_at": "<ISO Bogota time>",
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
    """
    period = (period or "all").strip().lower()
    if period != "all" and period not in PERIODS:
        raise ValueError(
            f"Invalid period '{period}'. Valid values: {['all'] + list(PERIODS)}"
        )

    data = _load_orders(orders_csv_path)

    now = _now_bogota_naive()
    today = now.normalize()

    labels = {
        "today": "Today",
        "last_7d": "Last 7 days",
        "last_30d": "Last 30 days",
        "last_90d": "Last 90 days",
        "last_180d": "Last 180 days",
    }

    selected = list(PERIODS) if period == "all" else [period]

    out: dict = {"generated_at": now.isoformat(), "periods": {}}
    for key in selected:
        days = PERIODS[key]
        start = today - pd.Timedelta(days=days - 1)
        window = data[(data["order_date"] >= start) & (data["order_date"] <= now)].copy()

        summary = _summarize_window(window)
        out["periods"][key] = {
            "label": labels[key],
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": today.strftime("%Y-%m-%d"),
            **summary,
        }

    return out
