# app/services/utm_campaign_insights.py
"""
Facebook insights data grouped by utm_campaign, for a set of trailing time
windows (Today, Last 7/30/90/180 days).

This mirrors the data shown in the /facebook_insights view (sales by campaign
plus pie breakdowns by hour bucket, gender, city and utm_content), but instead
of a single option-driven date range it computes the breakdown for each
trailing period. Source orders are filtered to utm_source == "facebook", same
as the view.

Normalization and pie bucketing reuse the exact helpers from
facebook_insights so the numbers match the view. The one intentional difference
is the time window: the view cuts off at end of yesterday, while this API
honors "Today" (the period is the trailing window up to *now*).
"""
import logging
import os

import pandas as pd

from app.services.facebook_insights import (
    _normalize_campaign_key,
    _normalize_city,
    _normalize_content,
    _normalize_gender,
    _normalize_str,
    _normalize_utm_source,
)

logger = logging.getLogger(__name__)

TZ = "America/Bogota"

# period key -> number of trailing days (today inclusive)
PERIODS: dict[str, int] = {
    "today": 1,
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


def _now_bogota_naive() -> pd.Timestamp:
    """Current Bogota time as a timezone-naive Timestamp (data is Bogota GMT-5)."""
    return pd.Timestamp.now(tz=TZ).tz_localize(None)


def _round2(x: float) -> float:
    return round(float(x), 2)


# ---- Pie builders (window-scoped; mirror facebook_insights bucketing) --------

def _hour_pie(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"labels": [], "values": []}
    hours = df["order_date"].dt.hour.astype(int)
    bucket = (hours // 3) * 3
    grouped = df.assign(_b=bucket).groupby("_b")["total_value"].sum().sort_index()
    labels, values = [], []
    for start_hour, val in grouped.items():
        v = float(val)
        if v <= 0:
            continue
        labels.append(f"{int(start_hour):02d}:00-{int(start_hour) + 2:02d}:59")
        values.append(_round2(v))
    return {"labels": labels, "values": values}


def _gender_pie(df: pd.DataFrame) -> dict:
    if df.empty or "gender" not in df.columns:
        return {"labels": [], "values": []}
    g = df["gender"].apply(_normalize_gender)
    grouped = df.assign(_g=g).groupby("_g")["total_value"].sum().sort_values(ascending=False)
    name = {"female": "Female", "male": "Male"}
    labels, values = [], []
    for key, val in grouped.items():
        v = float(val)
        if v <= 0:
            continue
        labels.append(name.get(key, "Other/Unknown"))
        values.append(_round2(v))
    return {"labels": labels, "values": values}


def _top_n_pie(df: pd.DataFrame, col: str, normalizer, top_n: int = 12) -> dict:
    if df.empty or col not in df.columns:
        return {"labels": [], "values": []}
    norm = df[col].apply(normalizer)
    grouped = df.assign(_k=norm).groupby("_k")["total_value"].sum().sort_values(ascending=False)
    grouped = grouped[grouped > 0]
    if grouped.empty:
        return {"labels": [], "values": []}
    if top_n and len(grouped) > top_n:
        top = grouped.iloc[:top_n]
        other_sum = float(grouped.iloc[top_n:].sum())
        labels = [str(x) for x in top.index.tolist()]
        values = [_round2(float(x)) for x in top.values.tolist()]
        if other_sum > 0:
            labels.append("Other")
            values.append(_round2(other_sum))
        return {"labels": labels, "values": values}
    return {
        "labels": [str(x) for x in grouped.index.tolist()],
        "values": [_round2(float(x)) for x in grouped.values.tolist()],
    }


def _daily_trend(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    daily = (
        df.assign(_day=df["order_date"].dt.floor("D"))
        .groupby("_day")["total_value"]
        .sum()
        .reset_index()
        .sort_values("_day")
    )
    return [
        {"date": d.strftime("%Y-%m-%d"), "sales": _round2(float(v))}
        for d, v in zip(daily["_day"], daily["total_value"])
    ]


# ---- Core --------------------------------------------------------------------

def _load_facebook_orders(orders_csv_path: str) -> pd.DataFrame:
    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    df = pd.read_csv(orders_csv_path)

    required_cols = {"order_date", "total_value", "utm_source", "utm_campaign"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    df["utm_source_norm"] = df["utm_source"].apply(_normalize_utm_source)
    df = df[df["utm_source_norm"] == "facebook"].copy()

    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df = df.dropna(subset=["order_date"]).copy()
    df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

    df["utm_campaign_key"] = df["utm_campaign"].apply(_normalize_campaign_key)
    df["utm_campaign_display"] = df["utm_campaign"].apply(lambda x: _normalize_str(x, fallback="unknown"))
    return df


def _summarize_window(window: pd.DataFrame, min_share_percent: float) -> dict:
    total_sales = float(window["total_value"].sum())
    total_orders = int(len(window))

    # Pick the most common raw display label per normalized campaign key.
    if not window.empty:
        display_map = (
            window.groupby("utm_campaign_key")["utm_campaign_display"]
            .agg(lambda s: s.value_counts().index[0] if len(s) else "unknown")
            .to_dict()
        )
    else:
        display_map = {}

    grouped = window.groupby("utm_campaign_key")["total_value"].sum().sort_values(ascending=False)

    by_campaign = []
    other_labels = []
    other_pct = 0.0

    for camp_key, val in grouped.items():
        val = float(val)
        if val <= 0:
            continue
        share = (val / total_sales * 100.0) if total_sales > 0 else 0.0
        label = display_map.get(camp_key, str(camp_key))

        if share < float(min_share_percent):
            other_labels.append(label)
            other_pct += share
            continue

        subset = window[window["utm_campaign_key"] == camp_key]
        by_campaign.append({
            "utm_campaign": label,
            "utm_campaign_key": str(camp_key),
            "total_sales": _round2(val),
            "total_orders": int(len(subset)),
            "sales_share_percentage": _round2(share),
            "hour_pie": _hour_pie(subset),
            "gender_pie": _gender_pie(subset),
            "city_pie": _top_n_pie(subset, "city", _normalize_city),
            "content_pie": _top_n_pie(subset, "utm_content", _normalize_content),
        })

    return {
        "totals": {
            "total_sales": _round2(total_sales),
            "total_orders": total_orders,
            "distinct_campaigns": int(grouped[grouped > 0].shape[0]),
        },
        "daily_trend": _daily_trend(window),
        "total_hour_pie": _hour_pie(window),
        "total_gender_pie": _gender_pie(window),
        "total_city_pie": _top_n_pie(window, "city", _normalize_city),
        "total_content_pie": _top_n_pie(window, "utm_content", _normalize_content),
        "by_utm_campaign": by_campaign,
        "other_campaigns": {
            "labels": other_labels,
            "pct": _round2(other_pct),
            "min_share_percent": float(min_share_percent),
        },
    }


def get_utm_campaign_insights(
    period: str = "all",
    orders_csv_path: str = "data/all_orders.csv",
    min_share_percent: float = 2.0,
) -> dict:
    """
    Facebook insights grouped by utm_campaign for one (or all) trailing periods.

    Args:
        period: one of PERIODS keys or "all".
        orders_csv_path: path to the all-orders CSV (filtered to facebook here).
        min_share_percent: campaigns below this share of the period's facebook
            sales are folded into "other_campaigns" (matches the view default).
    """
    period = (period or "all").strip().lower()
    if period != "all" and period not in PERIODS:
        raise ValueError(
            f"Invalid period '{period}'. Valid values: {['all'] + list(PERIODS)}"
        )

    df = _load_facebook_orders(orders_csv_path)

    now = _now_bogota_naive()
    today = now.normalize()
    selected = list(PERIODS) if period == "all" else [period]

    out: dict = {
        "generated_at": now.isoformat(),
        "utm_source": "facebook",
        "periods": {},
    }
    for key in selected:
        days = PERIODS[key]
        start = today - pd.Timedelta(days=days - 1)
        window = df[(df["order_date"] >= start) & (df["order_date"] <= now)].copy()

        out["periods"][key] = {
            "label": LABELS[key],
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": today.strftime("%Y-%m-%d"),
            **_summarize_window(window, min_share_percent),
        }

    return out
