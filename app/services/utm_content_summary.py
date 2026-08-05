# app/services/utm_content_summary.py
"""
Total sales and repurchase percentage grouped by utm_content, for a single
utm_campaign, across the trailing time windows (Today, Last 7/30/90/180 days).

This is the drill-down counterpart of utm_campaign_summary: that endpoint lists
campaigns, this one breaks a single campaign down by its utm_content values.
Repurchase classification is identical: an order is a "repurchase" if its email
has more than one order in the FULL history and the order is not that email's
first order. Classification runs over ALL orders, then the campaign filter and
time window are applied for aggregation.
"""
import logging
import os

import pandas as pd

from app.services.facebook_insights import _normalize_gender

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


class CampaignNotFound(Exception):
    """Raised when the requested campaign_name has no orders in the dataset."""

    def __init__(self, campaign_name: str, suggestions: list[str]):
        self.campaign_name = campaign_name
        self.suggestions = suggestions
        super().__init__(f"No campaign matching '{campaign_name}' was found.")


def _now_bogota_naive() -> pd.Timestamp:
    """Current Bogota time as a timezone-naive Timestamp (data is Bogota GMT-5)."""
    return pd.Timestamp.now(tz="America/Bogota").tz_localize(None)


def _normalize_label(series: pd.Series) -> pd.Series:
    """Trim; treat blank/nan/none as 'undefined'. Keeps original casing."""
    norm = series.fillna("").astype(str).str.strip()
    lowered = norm.str.lower()
    return norm.where(~lowered.isin(["", "nan", "none"]), "undefined")


def _round2(x: float) -> float:
    return round(float(x), 2)


def _load_orders(orders_csv_path: str) -> pd.DataFrame:
    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    data = pd.read_csv(orders_csv_path)

    required_cols = {"email", "order_date", "total_value", "utm_campaign", "utm_content"}
    missing = required_cols - set(data.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    data["order_date"] = pd.to_datetime(data["order_date"], errors="coerce")
    data = data.dropna(subset=["order_date", "email"]).copy()
    data["total_value"] = pd.to_numeric(data["total_value"], errors="coerce").fillna(0.0)

    # Repurchase classification over the FULL history (before any filtering).
    email_counts = data["email"].value_counts(dropna=True)
    repeat_emails = set(email_counts[email_counts > 1].index)
    first_order_dt = data.groupby("email")["order_date"].min()
    data = data.join(first_order_dt, on="email", rsuffix="_first")
    data["is_repurchase"] = (
        data["email"].isin(repeat_emails)
        & (data["order_date"] > data["order_date_first"])
    )

    data["utm_campaign_norm"] = _normalize_label(data["utm_campaign"])
    data["utm_content_norm"] = _normalize_label(data["utm_content"])
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


def _gender_share_sales(subset: pd.DataFrame) -> list[dict]:
    """
    Sales and orders by gender (Female / Male / Other-Unknown), same
    normalization as the /facebook_insights view. Empty groups are omitted.
    Returns a list of {"gender": str, "sales": float, "orders": int}.
    """
    if subset.empty or "gender" not in subset.columns:
        return []

    g = subset["gender"].apply(_normalize_gender)
    grouped = subset.assign(_g=g).groupby("_g")["total_value"].agg(["sum", "size"])
    grouped = grouped.sort_values("sum", ascending=False)

    name = {"female": "Female", "male": "Male"}
    rows = []
    for key, row in grouped.iterrows():
        v = float(row["sum"])
        if v <= 0:
            continue
        rows.append({
            "gender": name.get(key, "Other/Unknown"),
            "sales": _round2(v),
            "orders": int(row["size"]),
        })
    return rows


def _resolve_campaign(data: pd.DataFrame, campaign_name: str) -> str:
    """
    Resolve the requested campaign to its canonical label (case-insensitive
    exact match on the trimmed name). Raises CampaignNotFound with suggestions.
    """
    wanted = (campaign_name or "").strip()
    if not wanted:
        raise CampaignNotFound(campaign_name, [])

    names = data["utm_campaign_norm"]
    match = names[names.str.lower() == wanted.lower()]
    if not match.empty:
        return str(match.iloc[0])

    # Offer close-ish alternatives (substring match), ranked by sales.
    by_sales = (
        data.groupby("utm_campaign_norm")["total_value"].sum().sort_values(ascending=False)
    )
    suggestions = [
        str(name) for name in by_sales.index
        if wanted.lower() in str(name).lower() or str(name).lower() in wanted.lower()
    ][:10]
    raise CampaignNotFound(campaign_name, suggestions)


def _summarize_window(window: pd.DataFrame, limit: int) -> dict:
    agg = window.groupby("utm_content_norm").agg(
        total_sales=("total_value", "sum"),
        total_orders=("total_value", "size"),
    )
    rep = window[window["is_repurchase"]].groupby("utm_content_norm").agg(
        repurchase_sales=("total_value", "sum"),
        repurchase_orders=("total_value", "size"),
    )
    agg = agg.join(rep, how="left")
    agg["repurchase_sales"] = agg["repurchase_sales"].fillna(0.0)
    agg["repurchase_orders"] = agg["repurchase_orders"].fillna(0).astype(int)
    agg = agg.sort_values("total_sales", ascending=False)

    distinct_contents = int(len(agg))

    if limit and limit > 0:
        top = agg.iloc[:limit]
        rest = agg.iloc[limit:]
    else:
        top = agg
        rest = agg.iloc[0:0]

    by_content = [
        {"utm_content": content,
         **_metrics(row["total_sales"], row["total_orders"],
                    row["repurchase_sales"], row["repurchase_orders"]),
         "gender_share_sales": _gender_share_sales(
             window[window["utm_content_norm"] == content])}
        for content, row in top.iterrows()
    ]

    others = None
    if len(rest) > 0:
        rest_subset = window[window["utm_content_norm"].isin(set(rest.index))]
        others = {
            "contents_count": int(len(rest)),
            **_metrics(rest["total_sales"].sum(), rest["total_orders"].sum(),
                       rest["repurchase_sales"].sum(), rest["repurchase_orders"].sum()),
            "gender_share_sales": _gender_share_sales(rest_subset),
        }

    totals = _metrics(
        window["total_value"].sum(),
        len(window),
        window.loc[window["is_repurchase"], "total_value"].sum(),
        int(window["is_repurchase"].sum()),
    )
    totals["distinct_contents"] = distinct_contents
    totals["gender_share_sales"] = _gender_share_sales(window)

    return {
        "totals": totals,
        "limit": int(limit) if limit and limit > 0 else None,
        "by_utm_content": by_content,
        "others": others,
    }


def get_utm_content_summary(
    campaign_name: str,
    period: str = "all",
    orders_csv_path: str = "data/all_orders.csv",
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """
    Returns total sales and repurchase percentage grouped by utm_content for a
    single campaign.

    Args:
        campaign_name: the campaign to break down (case-insensitive exact match
            on the trimmed utm_campaign value, as returned by
            /api/utm_campaign_summary).
        period: one of PERIODS keys or "all".
        orders_csv_path: path to the all-orders CSV.
        limit: max utm_content rows per period (top N by sales); the rest are
            rolled up into "others". Pass 0 to return every value.

    Raises:
        CampaignNotFound: if no orders match campaign_name.
    """
    period = (period or "all").strip().lower()
    if period != "all" and period not in PERIODS:
        raise ValueError(
            f"Invalid period '{period}'. Valid values: {['all'] + list(PERIODS)}"
        )

    data = _load_orders(orders_csv_path)
    resolved = _resolve_campaign(data, campaign_name)
    campaign_data = data[data["utm_campaign_norm"] == resolved].copy()

    now = _now_bogota_naive()
    today = now.normalize()
    selected = list(PERIODS) if period == "all" else [period]

    out: dict = {
        "generated_at": now.isoformat(),
        "utm_campaign": resolved,
        "periods": {},
    }
    for key in selected:
        days = PERIODS[key]
        start = today - pd.Timedelta(days=days)
        window = campaign_data[
            (campaign_data["order_date"] >= start) & (campaign_data["order_date"] <= now)
        ].copy()

        out["periods"][key] = {
            "label": LABELS[key],
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": today.strftime("%Y-%m-%d"),
            **_summarize_window(window, limit),
        }

    return out
