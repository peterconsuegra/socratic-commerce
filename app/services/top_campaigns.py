# app/services/top_campaigns.py
"""
The top utm_campaigns of a set of orders by sales, for the "Top 10 campaigns"
section of /male_sales_monthly (routes/monthly_by_kind.py).

The orders come in already chosen (e.g. the ones the store marks male, see
ORDER_KINDS) and the window is the last WINDOW_MONTHS complete months, Bogota
time: recent enough that campaigns no longer running drop out. Campaign names
are cleaned as /api/utm_campaign_summary cleans them (_normalize_campaign);
"undefined" and "N/A" mean the order had no campaign, so they are left out of
the ranking and reported as no_campaign_sales instead.

One vectorized groupby; nothing per campaign in Python.
"""
import pandas as pd

from app.services.utm_campaign_summary import _normalize_campaign

WINDOW_MONTHS = 12
NO_CAMPAIGN = {"undefined", "n/a"}


def campaign_window(today: pd.Timestamp, months: int = WINDOW_MONTHS) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last moment of the last `months` complete months before today's month."""
    start_current_month = today.normalize().replace(day=1)
    return start_current_month - pd.DateOffset(months=months), start_current_month - pd.Timedelta(microseconds=1)


def top_utm_campaigns(orders: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, n: int = 10) -> dict:
    """
    orders: load_orders rows (order_date, total_value, utm_campaign,
    purchase_number). Returns:

      rows: up to n campaigns by sales, each with campaign, sales, share (% of
            all the window's sales, campaign or not), orders, avg_ticket,
            new_customers (first purchases, purchase_number 1) and last_order
            (YYYY-MM-DD);
      total_sales, no_campaign_sales and no_campaign_share for the window;
      campaigns: how many campaigns had sales in the window.
    """
    window = orders[(orders["order_date"] >= start) & (orders["order_date"] <= end)].copy()
    total_sales = float(window["total_value"].sum())

    window["campaign"] = _normalize_campaign(window["utm_campaign"])
    no_campaign = window["campaign"].str.lower().isin(NO_CAMPAIGN)
    no_campaign_sales = float(window.loc[no_campaign, "total_value"].sum())

    named = window[~no_campaign].assign(_first=window["purchase_number"].eq(1))
    grouped = (
        named.groupby("campaign")
        .agg(
            sales=("total_value", "sum"),
            orders=("total_value", "size"),
            new_customers=("_first", "sum"),
            last_order=("order_date", "max"),
        )
    )
    grouped = grouped[grouped["sales"] > 0].sort_values(["sales", "orders"], ascending=False)

    rows = [
        {
            "campaign": str(campaign),
            "sales": float(r["sales"]),
            "share": float(r["sales"] / total_sales * 100.0) if total_sales else 0.0,
            "orders": int(r["orders"]),
            "avg_ticket": float(r["sales"] / r["orders"]),
            "new_customers": int(r["new_customers"]),
            "last_order": r["last_order"].strftime("%Y-%m-%d"),
        }
        for campaign, r in grouped.head(n).iterrows()
    ]

    return {
        "rows": rows,
        "total_sales": total_sales,
        "no_campaign_sales": no_campaign_sales,
        "no_campaign_share": no_campaign_sales / total_sales * 100.0 if total_sales else 0.0,
        "campaigns": int(len(grouped)),
        "start": start.strftime("%Y-%m"),
        "end": end.strftime("%Y-%m"),
    }
