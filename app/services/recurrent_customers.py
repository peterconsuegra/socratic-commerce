# app/services/recurrent_customers.py
"""
Customers with more than one order ("recurrent" / repeat customers).

Customers are keyed by email, the same identity the repurchase logic uses
across the app (see utm_campaign_summary and daily_repurchases), so a customer
counted as recurrent here is exactly one whose orders are classified as
repurchases elsewhere.

Note on available fields: the orders source only carries a first name and an
email per order - there is no last name or phone number in the upstream orders
API, so those cannot be listed here.
"""
import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_PER_PAGE = 100
MAX_PER_PAGE = 1000

# sort key -> (dataframe column, default descending?)
SORT_COLUMNS = {
    "orders": ("orders_count", True),
    "spent": ("total_spent", True),
    "name": ("name", False),
    "email": ("email", False),
    "last_order": ("last_order", True),
}
DEFAULT_SORT = "spent"


def _load_orders(orders_csv_path: str) -> pd.DataFrame:
    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    data = pd.read_csv(orders_csv_path)

    required_cols = {"email", "order_date", "total_value"}
    missing = required_cols - set(data.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    data["order_date"] = pd.to_datetime(data["order_date"], errors="coerce")
    data = data.dropna(subset=["order_date", "email"]).copy()
    data["total_value"] = pd.to_numeric(data["total_value"], errors="coerce").fillna(0.0)

    data["email"] = data["email"].astype(str).str.strip()
    data = data[data["email"] != ""].copy()
    # Group case-insensitively so "A@x.com" and "a@x.com" are one customer.
    data["email_key"] = data["email"].str.lower()

    if "name" in data.columns:
        data["name"] = data["name"].fillna("").astype(str).str.strip()
    else:
        data["name"] = ""

    return data


def _pick_name(series: pd.Series) -> str:
    """Most frequent non-empty name for the customer, else empty."""
    vals = series[series.astype(str).str.strip() != ""]
    if vals.empty:
        return ""
    return str(vals.value_counts().index[0])


def get_recurrent_customers(
    orders_csv_path: str = "data/all_orders.csv",
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
    sort: str = DEFAULT_SORT,
    direction: str | None = None,
    search: str = "",
    min_orders: int = 2,
) -> dict:
    """
    Returns a page of customers with more than one order.

    Args:
        orders_csv_path: path to the all-orders CSV.
        page: 1-based page number.
        per_page: rows per page (default 100).
        sort: one of SORT_COLUMNS keys.
        direction: "asc" or "desc"; defaults to the sort key's natural order.
        search: optional case-insensitive filter on name or email.
        min_orders: minimum orders to count as recurrent (default 2).

    Returns a dict with the page rows plus pagination and summary metadata.
    """
    sort = (sort or DEFAULT_SORT).strip().lower()
    if sort not in SORT_COLUMNS:
        sort = DEFAULT_SORT

    column, default_desc = SORT_COLUMNS[sort]
    direction = (direction or ("desc" if default_desc else "asc")).strip().lower()
    if direction not in {"asc", "desc"}:
        direction = "desc" if default_desc else "asc"

    try:
        per_page = int(per_page)
    except (TypeError, ValueError):
        per_page = DEFAULT_PER_PAGE
    per_page = max(1, min(per_page, MAX_PER_PAGE))

    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)

    data = _load_orders(orders_csv_path)

    grouped = data.groupby("email_key").agg(
        orders_count=("total_value", "size"),
        total_spent=("total_value", "sum"),
        last_order=("order_date", "max"),
        first_order=("order_date", "min"),
        email=("email", "first"),
        name=("name", _pick_name),
    )

    total_customers = int(len(grouped))
    recurrent = grouped[grouped["orders_count"] >= int(min_orders)].copy()

    # Summary over ALL recurrent customers, before search/pagination.
    summary = {
        "total_customers": total_customers,
        "recurrent_customers": int(len(recurrent)),
        "recurrent_orders": int(recurrent["orders_count"].sum()) if len(recurrent) else 0,
        "recurrent_revenue": float(recurrent["total_spent"].sum()) if len(recurrent) else 0.0,
    }

    search = (search or "").strip()
    if search:
        needle = search.lower()
        mask = (
            recurrent["email"].str.lower().str.contains(needle, na=False, regex=False)
            | recurrent["name"].str.lower().str.contains(needle, na=False, regex=False)
        )
        recurrent = recurrent[mask].copy()

    recurrent = recurrent.sort_values(
        column, ascending=(direction == "asc"), kind="mergesort"
    )

    total_rows = int(len(recurrent))
    total_pages = max(1, (total_rows + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    window = recurrent.iloc[start:start + per_page]

    rows = []
    for i, (_, r) in enumerate(window.iterrows(), start=start + 1):
        rows.append({
            "rank": i,
            "name": r["name"],
            "email": r["email"],
            "orders_count": int(r["orders_count"]),
            "total_spent": float(r["total_spent"]),
            "avg_order_value": float(r["total_spent"]) / int(r["orders_count"]) if r["orders_count"] else 0.0,
            "first_order": r["first_order"].strftime("%Y-%m-%d") if pd.notna(r["first_order"]) else "",
            "last_order": r["last_order"].strftime("%Y-%m-%d") if pd.notna(r["last_order"]) else "",
        })

    return {
        "rows": rows,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "start": start + 1 if total_rows else 0,
            "end": start + len(rows),
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
        "sort": {"key": sort, "direction": direction},
        "search": search,
        "summary": summary,
    }
