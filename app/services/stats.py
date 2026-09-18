# app/services/stats.py
"""
Repurchase statistics for the /stats view.

Two halves. What the orders file says about repeat buying (repeat rate, time
between orders, first-vs-repeat orders by month, repeat rate by first SKU) is
cached per file version, like the customers table, because it costs a pass
over ~60k orders. What the contact log says about the win-back programme
(who bought after being contacted) is cheap and computed per request, so a
send made a minute ago shows up.

Attribution is last-touch: an order counts for the most recent contact that
preceded it, so a customer tagged on the 3rd and emailed on the 10th who buys
on the 12th is a recovery for the email, not both.
"""
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.services.recurrent_customers import _file_key, _load_orders

logger = logging.getLogger(__name__)

# Days between consecutive orders, in the bands a win-back window is chosen from.
GAP_BUCKETS = [
    (0, 30, "0–30"), (31, 60, "31–60"), (61, 90, "61–90"),
    (91, 180, "91–180"), (181, 365, "181–365"), (366, None, "365+"),
]
MONTHS_BACK = 12
# A first-purchase SKU needs this many customers before its repeat rate means anything.
MIN_SKU_CUSTOMERS = 100
MAX_LABELS = 62  # two months of daily labels on the win-back chart

_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "stats": None, "orders": None}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def orders_stats(orders_csv_path: str) -> tuple[dict, pd.DataFrame]:
    """(cached orders-derived stats, light orders frame for the win-back join)."""
    key = _file_key(orders_csv_path)
    if _CACHE["key"] == key:
        return _CACHE["stats"], _CACHE["orders"]
    with _LOCK:
        if _CACHE["key"] == key:
            return _CACHE["stats"], _CACHE["orders"]
        data = _load_orders(orders_csv_path)
        stats = _compute_orders_stats(data)
        orders = _light_orders(data)
        _CACHE.update(key=key, stats=stats, orders=orders)
        logger.info("repurchase stats rebuilt from %s (%d orders)", orders_csv_path, len(data))
        return stats, orders


def _light_orders(data: pd.DataFrame) -> pd.DataFrame:
    """email, order id, value and a UTC timestamp comparable with the contact log."""
    ts = pd.to_datetime(data["order_date_utc"], utc=True, errors="coerce").dt.tz_localize(None)
    # Orders written before the UTC column existed fall back to the local time.
    ts = ts.fillna(pd.to_datetime(data["order_date"], errors="coerce"))
    return pd.DataFrame({
        "email_key": data["email_key"].values,
        "order_id": data["order_id"].values,
        "order_ts": ts.values,
        "total_value": data["total_value"].values,
    })


def _bucket(days: float) -> str:
    for lo, hi, label in GAP_BUCKETS:
        if days >= lo and (hi is None or days <= hi):
            return label
    return GAP_BUCKETS[-1][2]


def _compute_orders_stats(data: pd.DataFrame) -> dict:
    d = data.sort_values(["email_key", "order_date"], kind="mergesort").copy()
    d["order_rank"] = d.groupby("email_key").cumcount() + 1  # 1 = the customer's first order

    per_customer = d.groupby("email_key").agg(orders=("order_id", "size"), first_date=("order_date", "min"))
    customers = int(len(per_customer))
    repeat_customers = int((per_customer["orders"] >= 2).sum())
    orders_total = int(len(d))
    repeat_orders = orders_total - customers
    revenue = float(d["total_value"].sum())
    repeat_revenue = float(d.loc[d["order_rank"] > 1, "total_value"].sum())

    # Gaps between consecutive orders (any rank), and first -> second specifically.
    gaps = d.groupby("email_key")["order_date"].diff().dt.days.dropna()
    second = d[d["order_rank"] == 2]
    first_to_second = (
        second["order_date"].values - per_customer.loc[second["email_key"], "first_date"].values
    ) / pd.Timedelta(days=1)
    first_to_second = pd.Series(first_to_second, index=second["email_key"].values)

    hist_counts = gaps.apply(_bucket).value_counts()
    histogram = [
        {"bucket": label, "gaps": int(hist_counts.get(label, 0)),
         "share": float(hist_counts.get(label, 0)) / len(gaps) if len(gaps) else 0.0}
        for _lo, _hi, label in GAP_BUCKETS
    ]

    # First vs repeat orders per month, the last MONTHS_BACK months incl. the current one.
    d["month"] = d["order_date"].dt.to_period("M")
    by_month = d.groupby("month").agg(
        first_orders=("order_rank", lambda s: int((s == 1).sum())),
        repeat_orders=("order_rank", lambda s: int((s > 1).sum())),
        repeat_revenue=("total_value", lambda s: float(s[d.loc[s.index, "order_rank"] > 1].sum())),
    )
    months = pd.period_range(end=pd.Timestamp.now().to_period("M"), periods=MONTHS_BACK, freq="M")
    by_month = by_month.reindex(months, fill_value=0)
    current = str(months[-1])
    monthly = [
        {"month": str(m), "first_orders": int(r.first_orders), "repeat_orders": int(r.repeat_orders),
         "repeat_revenue": float(r.repeat_revenue),
         "repeat_share": float(r.repeat_orders) / (r.first_orders + r.repeat_orders) if (r.first_orders + r.repeat_orders) else 0.0,
         "partial": str(m) == current}
        for m, r in by_month.iterrows()
    ]

    # Repeat rate by the SKU of the first order.
    first_rows = d[d["order_rank"] == 1][["email_key", "sku"]].copy()
    first_rows["sku"] = first_rows["sku"].replace("", "(sin SKU)")
    first_rows = first_rows.join(per_customer["orders"], on="email_key")
    first_rows["repeat"] = first_rows["orders"] >= 2
    first_rows["days_to_second"] = first_rows["email_key"].map(first_to_second)
    by_sku = first_rows.groupby("sku").agg(
        customers=("email_key", "size"), repeat_customers=("repeat", "sum"),
        avg_orders=("orders", "mean"), median_days_to_second=("days_to_second", "median"),
    )
    by_sku = by_sku[by_sku["customers"] >= MIN_SKU_CUSTOMERS].sort_values("customers", ascending=False)
    first_sku = [
        {"sku": sku, "customers": int(r.customers), "repeat_customers": int(r.repeat_customers),
         "repeat_rate": float(r.repeat_customers) / r.customers if r.customers else 0.0,
         "avg_orders": float(r.avg_orders),
         "median_days_to_second": None if pd.isna(r.median_days_to_second) else int(r.median_days_to_second)}
        for sku, r in by_sku.iterrows()
    ]

    return {
        "customers": customers,
        "repeat_customers": repeat_customers,
        "repeat_rate": repeat_customers / customers if customers else 0.0,
        "orders": orders_total,
        "repeat_orders": repeat_orders,
        "repeat_order_share": repeat_orders / orders_total if orders_total else 0.0,
        "revenue": revenue,
        "repeat_revenue": repeat_revenue,
        "repeat_revenue_share": repeat_revenue / revenue if revenue else 0.0,
        "median_gap_days": None if gaps.empty else int(gaps.median()),
        "median_first_to_second_days": None if first_to_second.empty else int(first_to_second.median()),
        "second_within_90_share": float((first_to_second <= 90).mean()) if len(first_to_second) else 0.0,
        "gap_count": int(len(gaps)),
        "histogram": histogram,
        "monthly": monthly,
        "first_sku": first_sku,
        "min_sku_customers": MIN_SKU_CUSTOMERS,
    }


def winback_stats(orders: pd.DataFrame, contacts: pd.DataFrame, period_days: int) -> dict:
    """
    What the contact log says. contacts has id, email, channel, label, sent_at
    (naive UTC). period_days limits which contacts are evaluated (0 = all);
    attempts before a recovery are always counted over the whole log.
    """
    empty = {"contacts": 0, "customers": 0, "recovered_customers": 0, "conversion": 0.0,
             "orders": 0, "revenue": 0.0, "median_days_to_order": None,
             "by_channel": [], "by_label": [], "attempts": [], "period_days": period_days}
    if contacts is None or contacts.empty:
        return empty

    all_contacts = contacts.copy()
    contacts = contacts.copy()
    if period_days:
        contacts = contacts[contacts["sent_at"] >= _utcnow() - timedelta(days=period_days)].copy()
    if contacts.empty:
        return empty

    m = contacts.merge(orders, left_on="email", right_on="email_key", how="inner")
    m = m[m["order_ts"] > m["sent_at"]].copy()
    # Last touch: an order belongs to the latest contact that preceded it.
    m = m.sort_values(["order_id", "sent_at"], kind="mergesort").drop_duplicates("order_id", keep="last")
    m["days_to_order"] = (m["order_ts"] - m["sent_at"]) / pd.Timedelta(days=1)
    recovered_ids = set(m["id"])
    contacts["recovered"] = contacts["id"].isin(recovered_ids)

    def summarise(group_contacts: pd.DataFrame, group_orders: pd.DataFrame) -> dict:
        customers = int(group_contacts["email"].nunique())
        recovered = int(group_orders["email"].nunique())
        return {
            "contacts": int(len(group_contacts)),
            "customers": customers,
            "recovered_customers": recovered,
            "conversion": recovered / customers if customers else 0.0,
            "orders": int(len(group_orders)),
            "revenue": float(group_orders["total_value"].sum()) if len(group_orders) else 0.0,
            "median_days_to_order": None if group_orders.empty else int(group_orders["days_to_order"].median()),
        }

    overall = summarise(contacts, m)

    by_channel = []
    for channel, g in contacts.groupby("channel"):
        row = summarise(g, m[m["channel"] == channel])
        by_channel.append({"channel": channel, **row})

    by_label = []
    first_sent = contacts.groupby("label")["sent_at"].min().sort_values(ascending=False)
    for label in first_sent.index[:MAX_LABELS]:
        g = contacts[contacts["label"] == label]
        row = summarise(g, m[m["label"] == label])
        by_label.append({"label": label, "channel": g["channel"].iloc[0], "first_sent": first_sent[label].strftime("%Y-%m-%d"), **row})

    # How many contacts (any channel, whole log) preceded each recovering order.
    attempts = []
    if not m.empty:
        a = m[["order_id", "email", "order_ts"]].merge(all_contacts[["email", "sent_at"]], on="email", how="left")
        a = a[a["sent_at"] < a["order_ts"]].groupby("order_id").size()
        buckets = a.clip(upper=4).value_counts().sort_index()
        total = int(buckets.sum())
        attempts = [{"attempts": ("4+" if n == 4 else str(n)), "orders": int(c), "share": c / total if total else 0.0}
                    for n, c in buckets.items()]

    return {**overall, "by_channel": by_channel, "by_label": by_label, "attempts": attempts,
            "period_days": period_days}
