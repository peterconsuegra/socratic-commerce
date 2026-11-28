# app/services/top_campaigns.py
"""
The top values of a UTM field (utm_campaign, utm_content) of a set of orders
by sales, for the "Top campaigns" / "Top ads" tables of the monthly pages:
/male_sales_monthly and /female_sales_monthly (top 20 campaigns each),
/new_customers_monthly (top 20 campaigns and top 20 utm_content) and
/link_in_bio_stats_monthly (top 20 campaigns), see routes/monthly_by_kind.py.

The orders come in already chosen (e.g. the ones the store marks male, see
ORDER_KINDS) and the window is the last WINDOW_MONTHS complete months, Bogota
time: recent enough that campaigns no longer running drop out. Values are
cleaned as /api/utm_campaign_summary and /api/utm_content_summary clean them
(trimmed, blank = "undefined"); "undefined" and "N/A" mean the order had no
value, so they are left out of the ranking and reported as no_value_sales.

A utm_content name can be reused across campaigns, so each utm_content row
also names its main campaign: the one with the most of its sales.

One vectorized groupby per table; nothing per value in Python.
"""
import pandas as pd

from app.services.utm_campaign_summary import _normalize_campaign
from app.services.utm_content_summary import _normalize_label

WINDOW_MONTHS = 12
NO_VALUE = {"undefined", "n/a"}
NORMALIZERS = {"utm_campaign": _normalize_campaign, "utm_content": _normalize_label}


def campaign_window(today: pd.Timestamp, months: int = WINDOW_MONTHS) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last moment of the last `months` complete months before today's month."""
    start_current_month = today.normalize().replace(day=1)
    return start_current_month - pd.DateOffset(months=months), start_current_month - pd.Timedelta(microseconds=1)


def top_utm_values(orders: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                   field: str = "utm_campaign", n: int = 10) -> dict:
    """
    orders: load_orders rows (order_date, total_value, utm_campaign,
    utm_content, purchase_number). field: a key of NORMALIZERS. Returns:

      rows: up to n values by sales, each with value, sales, share (% of all
            the window's sales, with a value or not), orders, avg_ticket,
            new_customers (first purchases, purchase_number 1), last_order
            (YYYY-MM-DD) and, for utm_content, main_campaign;
      total_sales, no_value_sales and no_value_share for the window;
      values: how many values had sales in the window.
    """
    window = orders[(orders["order_date"] >= start) & (orders["order_date"] <= end)].copy()
    total_sales = float(window["total_value"].sum())

    window["_value"] = NORMALIZERS[field](window[field])
    no_value = window["_value"].str.lower().isin(NO_VALUE)
    no_value_sales = float(window.loc[no_value, "total_value"].sum())

    named = window[~no_value].assign(_first=window["purchase_number"].eq(1))
    grouped = (
        named.groupby("_value")
        .agg(
            sales=("total_value", "sum"),
            orders=("total_value", "size"),
            new_customers=("_first", "sum"),
            last_order=("order_date", "max"),
        )
    )
    grouped = grouped[grouped["sales"] > 0].sort_values(["sales", "orders"], ascending=False)
    top = grouped.head(n)

    main_campaign = {}
    if field == "utm_content" and not top.empty:
        pairs = (
            named[named["_value"].isin(top.index)]
            .assign(_campaign=lambda d: _normalize_campaign(d["utm_campaign"]))
            .groupby(["_value", "_campaign"])["total_value"].sum()
        )
        main_campaign = {value: campaign for value, campaign in pairs.groupby(level=0).idxmax().map(lambda k: k[1]).items()}

    rows = []
    for value, r in top.iterrows():
        row = {
            "value": str(value),
            "sales": float(r["sales"]),
            "share": float(r["sales"] / total_sales * 100.0) if total_sales else 0.0,
            "orders": int(r["orders"]),
            "avg_ticket": float(r["sales"] / r["orders"]),
            "new_customers": int(r["new_customers"]),
            "last_order": r["last_order"].strftime("%Y-%m-%d"),
        }
        if field == "utm_content":
            row["main_campaign"] = str(main_campaign.get(value, ""))
        rows.append(row)

    return {
        "field": field,
        "rows": rows,
        "total_sales": total_sales,
        "no_value_sales": no_value_sales,
        "no_value_share": no_value_sales / total_sales * 100.0 if total_sales else 0.0,
        "values": int(len(grouped)),
        "start": start.strftime("%Y-%m"),
        "end": end.strftime("%Y-%m"),
    }


def top_utm_campaigns(orders: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, n: int = 10) -> dict:
    """top_utm_values for utm_campaign."""
    return top_utm_values(orders, start, end, field="utm_campaign", n=n)
