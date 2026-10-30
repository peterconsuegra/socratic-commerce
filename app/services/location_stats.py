# app/services/location_stats.py
"""
The stats section of /sales_by_location: headline figures for the selected
department and/or city, and the same figures broken down by city, channel
(utm_source), product (SKU) and weekday.

Repurchase figures are the store's (is_repurchase, purchase_number): a first
purchase is purchase_number 1, so new customers = first purchases. An order
the store had not numbered yet counts as neither (see load_orders).
"""
import pandas as pd

from app.services.get_data import filter_by_location, load_orders
from app.services.periods import orders_cutoff

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_ROWS = 50  # cities and SKUs listed per tab


def _clean_label(series: pd.Series, missing: str) -> pd.Series:
    s = series.fillna("").astype(str).str.strip()
    return s.mask(s.str.lower().isin({"", "n/a", "nan", "none"}), missing)


def _group_figures(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Sales, orders, AOV, first purchases and repurchases per value of key, by sales."""
    g = df.assign(
        _rep_sales=df["total_value"].where(df["is_repurchase"], 0.0),
        _first=df["purchase_number"].eq(1),
    ).groupby(key, sort=False).agg(
        sales=("total_value", "sum"),
        orders=("order_id", "size"),
        first_purchases=("_first", "sum"),
        repurchases=("is_repurchase", "sum"),
        repurchase_sales=("_rep_sales", "sum"),
    )
    total = float(g["sales"].sum()) or 1.0
    g["share"] = g["sales"] / total * 100.0
    g["aov"] = g["sales"] / g["orders"]
    g["repurchase_rate"] = g["repurchases"] / g["orders"] * 100.0
    return g.sort_values("sales", ascending=False)


def _rows(g: pd.DataFrame, limit: int | None = None) -> list[dict]:
    g = g.head(limit) if limit else g
    return [
        {
            "label": str(label),
            "sales": float(r.sales),
            "share": float(r.share),
            "orders": int(r.orders),
            "aov": float(r.aov),
            "first_purchases": int(r.first_purchases),
            "repurchases": int(r.repurchases),
            "repurchase_sales": float(r.repurchase_sales),
            "repurchase_rate": float(r.repurchase_rate),
        }
        for label, r in g.iterrows()
    ]


def build_location_stats(csv_path: str, state: str | None = None, city: str | None = None,
                         include_today: bool = False) -> dict | None:
    """
    The stats of the orders in csv_path for the location, up to the same
    cutoff as the charts (orders_cutoff). None when there are no orders.
    """
    df = filter_by_location(load_orders(csv_path), state, city)
    df = df[df["order_date"] <= orders_cutoff(include_today)]
    if df.empty:
        return None

    df = df.assign(
        city=_clean_label(df["city"], "unknown"),
        utm_source=_clean_label(df["utm_source"], "unknown").str.lower(),
    )

    sales = float(df["total_value"].sum())
    orders = len(df)
    rep = df[df["is_repurchase"]]
    first = df[df["purchase_number"].eq(1)]
    days = (df["order_date"].dt.normalize().max() - df["order_date"].dt.normalize().min()).days + 1

    summary = {
        "sales": sales,
        "orders": orders,
        "aov": sales / orders,
        "repurchase_sales": float(rep["total_value"].sum()),
        "repurchase_sales_share": float(rep["total_value"].sum()) / sales * 100.0 if sales else 0.0,
        "repurchases": len(rep),
        "repurchase_rate": len(rep) / orders * 100.0,
        "repurchase_aov": float(rep["total_value"].mean()) if len(rep) else 0.0,
        "first_purchases": len(first),
        "first_purchase_sales": float(first["total_value"].sum()),
        "first_purchase_aov": float(first["total_value"].mean()) if len(first) else 0.0,
        "third_plus": int(df["purchase_number"].ge(3).sum()),
        "unnumbered": int(df["purchase_number"].isna().sum()),
        "days": days,
        "sales_per_day": sales / days,
        "cities": int(df.loc[df["city"] != "unknown", "city"].nunique()),
    }

    by_city = _group_figures(df, "city")
    top_channel = (
        df.groupby(["city", "utm_source"])["total_value"].sum()
        .reset_index().sort_values("total_value", ascending=False)
        .drop_duplicates("city").set_index("city")["utm_source"]
    )
    city_rows = _rows(by_city, MAX_ROWS)
    for row in city_rows:
        row["top_channel"] = top_channel.get(row["label"], "")

    # A multi-item order lists every SKU, comma-separated: an order counts once
    # for each SKU it holds, so the SKU rows count orders, not sales.
    skus = df[["sku", "is_repurchase", "purchase_number"]].assign(
        sku=df["sku"].str.split(",")
    ).explode("sku")
    skus["sku"] = _clean_label(skus["sku"], "")
    skus = skus[skus["sku"] != ""]
    sku_rows = []
    if not skus.empty:
        g = skus.assign(_first=skus["purchase_number"].eq(1)).groupby("sku").agg(
            orders=("sku", "size"), first_purchases=("_first", "sum"), repurchases=("is_repurchase", "sum"),
        ).sort_values("orders", ascending=False).head(MAX_ROWS)
        sku_rows = [
            {
                "label": str(label),
                "orders": int(r.orders),
                "share": int(r.orders) / orders * 100.0,
                "first_purchases": int(r.first_purchases),
                "repurchases": int(r.repurchases),
                "repurchase_rate": int(r.repurchases) / int(r.orders) * 100.0,
            }
            for label, r in g.iterrows()
        ]

    by_weekday = _group_figures(df.assign(weekday=df["order_date"].dt.dayofweek), "weekday")
    by_weekday = by_weekday.reindex([d for d in range(7) if d in by_weekday.index])
    by_weekday.index = [WEEKDAYS[d] for d in by_weekday.index]

    return {
        "summary": summary,
        "by_city": city_rows,
        "city_count": len(by_city),
        "by_channel": _rows(_group_figures(df, "utm_source")),
        "by_sku": sku_rows,
        "by_weekday": _rows(by_weekday),
    }
