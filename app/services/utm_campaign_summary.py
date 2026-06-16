# app/services/utm_campaign_summary.py
"""
Total sales and repurchase percentage grouped by utm_campaign, for a set of
trailing time windows (Today, Last 7/30/90/180 days).

This is the utm_campaign counterpart of utm_source_summary (which groups by
utm_source). Repurchase classification is identical: an order is a "repurchase"
if its email has more than one order in the FULL history and the order is not
that email's first order. Classification runs over ALL orders, then the time
window is applied for aggregation.

Because there are hundreds of campaigns, results are limited to the top N
campaigns by sales per period; the remainder is rolled up into an "others"
bucket so the per-campaign rows plus "others" reconcile to the period totals.
"""
import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)

# period key -> days subtracted from today for the window start: window is
# [today - N, now], today-inclusive (PYS "last N days" convention). "today" = 0.
PERIODS: dict[str, int] = {
    "today": 0,
    "last_7d": 7,
    "last_30d": 30,
    "last_90d": 90,
    "last_180d": 180,
}

LABELS = {
    "today": "Today",
    "last_7d": "Last 7 days",
    "last_30d": "Last 30 days",
    "last_90d": "Last 90 days",
    "last_180d": "Last 180 days",
}

DEFAULT_LIMIT = 50


def _now_bogota_naive() -> pd.Timestamp:
    """Current Bogota time as a timezone-naive Timestamp (data is Bogota GMT-5)."""
    return pd.Timestamp.now(tz="America/Bogota").tz_localize(None)


def _normalize_campaign(series: pd.Series) -> pd.Series:
    norm = (
        series.fillna("")
        .astype(str)
        .str.strip()
    )
    lowered = norm.str.lower()
    norm = norm.where(~lowered.isin(["", "nan", "none"]), "undefined")
    return norm


def _round2(x: float) -> float:
    return round(float(x), 2)


def _load_orders(orders_csv_path: str) -> pd.DataFrame:
    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    data = pd.read_csv(orders_csv_path)

    required_cols = {"email", "order_date", "total_value", "utm_campaign"}
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

    data["utm_campaign_norm"] = _normalize_campaign(data["utm_campaign"])
    return data


def _metrics(total_sales, total_orders, rep_sales, rep_orders) -> dict:
    total_sales = float(total_sales)
    total_orders = int(total_orders)
    rep_sales = float(rep_sales)
    rep_orders = int(rep_orders)
    return {
        "total_sales": _round2(total_sales),
        "total_orders": total_orders,
        "repurchase_sales": _round2(rep_sales),
        "repurchase_orders": rep_orders,
        "repurchase_sales_percentage": _round2(rep_sales / total_sales * 100) if total_sales > 0 else 0.0,
        "repurchase_orders_percentage": _round2(rep_orders / total_orders * 100) if total_orders > 0 else 0.0,
    }


def _summarize_window(window: pd.DataFrame, limit: int) -> dict:
    grouped = window.groupby("utm_campaign_norm")
    agg = grouped.agg(
        total_sales=("total_value", "sum"),
        total_orders=("total_value", "size"),
    )
    rep = window[window["is_repurchase"]].groupby("utm_campaign_norm").agg(
        repurchase_sales=("total_value", "sum"),
        repurchase_orders=("total_value", "size"),
    )
    agg = agg.join(rep, how="left")
    agg["repurchase_sales"] = agg["repurchase_sales"].fillna(0.0)
    agg["repurchase_orders"] = agg["repurchase_orders"].fillna(0).astype(int)
    agg = agg.sort_values("total_sales", ascending=False)

    total_campaigns = int(len(agg))

    if limit and limit > 0:
        top = agg.iloc[:limit]
        rest = agg.iloc[limit:]
    else:
        top = agg
        rest = agg.iloc[0:0]

    by_campaign = []
    for campaign, row in top.iterrows():
        m = _metrics(row["total_sales"], row["total_orders"],
                     row["repurchase_sales"], row["repurchase_orders"])
        by_campaign.append({"utm_campaign": campaign, **m})

    others = None
    if len(rest) > 0:
        others = {
            "campaigns_count": int(len(rest)),
            **_metrics(rest["total_sales"].sum(), rest["total_orders"].sum(),
                       rest["repurchase_sales"].sum(), rest["repurchase_orders"].sum()),
        }

    totals = _metrics(
        window["total_value"].sum(),
        len(window),
        window.loc[window["is_repurchase"], "total_value"].sum(),
        int(window["is_repurchase"].sum()),
    )
    totals["distinct_campaigns"] = total_campaigns

    return {
        "totals": totals,
        "limit": int(limit) if limit and limit > 0 else None,
        "by_utm_campaign": by_campaign,
        "others": others,
    }


def get_utm_campaign_summary(
    period: str = "all",
    orders_csv_path: str = "data/all_orders.csv",
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """
    Returns total sales and repurchase percentage grouped by utm_campaign.

    Args:
        period: one of PERIODS keys or "all".
        orders_csv_path: path to the all-orders CSV.
        limit: max campaigns returned per period (top N by sales); the rest are
            rolled up into "others". Pass 0 to return every campaign.
    """
    period = (period or "all").strip().lower()
    if period != "all" and period not in PERIODS:
        raise ValueError(
            f"Invalid period '{period}'. Valid values: {['all'] + list(PERIODS)}"
        )

    data = _load_orders(orders_csv_path)

    now = _now_bogota_naive()
    today = now.normalize()
    selected = list(PERIODS) if period == "all" else [period]

    out: dict = {"generated_at": now.isoformat(), "periods": {}}
    for key in selected:
        days = PERIODS[key]
        start = today - pd.Timedelta(days=days)
        window = data[(data["order_date"] >= start) & (data["order_date"] <= now)].copy()

        out["periods"][key] = {
            "label": LABELS[key],
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": today.strftime("%Y-%m-%d"),
            **_summarize_window(window, limit),
        }

    return out
