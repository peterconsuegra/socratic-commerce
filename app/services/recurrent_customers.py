# app/services/recurrent_customers.py
"""
Customers with more than one order ("recurrent" / repeat customers).

Customers are keyed by email, the same identity the repurchase logic uses
across the app (see utm_campaign_summary and daily_repurchases), so a customer
counted as recurrent here is exactly one whose orders are classified as
repurchases elsewhere.

Note on available fields: the orders source carries a first name, an email and
a billing phone per order. There is still no last name in the upstream orders
API, so that cannot be listed here.

Phone is a display column only. It is never used to identify or de-duplicate
customers: the upstream values are not normalised (both "+573008007384" and
"3165391777" occur for Colombian numbers, sometimes for the same person), so
matching on the raw string would split one customer into several. Any
phone-based matching would need E.164 normalisation first, as a separate
decision.

sku can list several comma-separated SKUs when an order had several line
items, so it is always split - never grouped on raw, which would invent a
phantom product. The report shows the SKU(s) of the customer's last purchase.
Like phone it is display only: not part of customer identity, and it does not
affect any total.

order_date_utc is the same instant as order_date expressed in UTC. It is kept
as a plain ISO-8601 string rather than parsed, so tz-aware values can never
meet the tz-naive order_date timestamps (pandas raises when they are compared).
ISO-8601 with a fixed "Z" suffix sorts lexicographically in chronological
order, so a plain string max() is a correct "most recent".
"""
import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)

# The orders API's missing-value sentinel, mirrored from get_data.py.
MISSING_SENTINELS = {"n/a", "na", "none", "null"}

DEFAULT_PER_PAGE = 100
MAX_PER_PAGE = 1000

# sort key -> (dataframe column, default descending?)
SORT_COLUMNS = {
    "orders": ("orders_count", True),
    "spent": ("total_spent", True),
    "name": ("name", False),
    "email": ("email", False),
    "last_order": ("last_order", True),
    "last_order_utc": ("last_order_utc", True),
    "last_value": ("last_order_value", True),
}
DEFAULT_SORT = "spent"


def _load_orders(orders_csv_path: str) -> pd.DataFrame:
    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    # phone and order_date_utc must be read as text. Left to type inference,
    # a column of all-digit phone numbers becomes float64 - "+573008007384"
    # turns into 573008007384.0, losing the "+" and any leading zero. Only
    # columns actually present are named, so a pre-schema CSV still loads.
    header = pd.read_csv(orders_csv_path, nrows=0).columns
    text_cols = {c: str for c in ("phone", "order_date_utc", "sku") if c in header}

    data = pd.read_csv(orders_csv_path, dtype=text_cols or None)

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

    # phone and order_date_utc are newer columns. A CSV written before the
    # schema change will not have them, so default to empty rather than
    # treating them as required - a stale cache should render blank cells,
    # not crash the page.
    for col in ("phone", "order_date_utc", "sku", "last_name"):
        if col in data.columns:
            data[col] = data[col].fillna("").astype(str).str.strip()
            # "N/A" is the API sentinel; blank it here too in case an older
            # CSV was written before _clean_optional existed.
            data.loc[data[col].str.lower().isin(MISSING_SENTINELS), col] = ""
        else:
            data[col] = ""

    return data


def _days_since(utc_iso: str):
    """Whole days between an ISO-8601 "Z" timestamp and now, or None."""
    if not utc_iso:
        return None
    ts = pd.to_datetime(utc_iso, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return int((pd.Timestamp.now(tz="UTC") - ts).days)


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
    sku_filter: set | list | None = None,
    inactive_months: int | None = None,
    max_orders: int | None = None,
    emails: list | None = None,
    paginate: bool = True,
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
        min_orders: minimum orders to count as recurrent (default 2). Pass 1
            to include one-time buyers.
        sku_filter: if given, keep only customers whose LAST purchase included
            one of these SKUs.
        inactive_months: if given, keep only customers whose last order (UTC)
            is older than this many months - a lapsed / win-back segment.
        max_orders: if given, keep only customers with at most this many
            orders, so min_orders/max_orders together bound the range.
        emails: if given, keep only these customers (case-insensitive email
            match). Used to resolve a checkbox selection: without it a caller
            would have to page through the spend-ranked listing and anyone
            below the page cap would silently resolve as missing.

    Both filters default to None, leaving the returned figures identical to a
    call without them.

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

    # Phone and the UTC timestamp are derived separately and joined on, so the
    # aggregation above - and therefore orders_count, total_spent and the
    # summary totals - stays exactly as it was.
    #
    # A customer's phone can differ per order, so take the most recent
    # non-empty one: sort that subset by order_date and keep the last.
    with_phone = data[data["phone"] != ""]
    phone_by_customer = (
        with_phone.sort_values("order_date", kind="mergesort")
        .groupby("email_key")["phone"]
        .last()
        .rename("phone")
    )

    # Last name follows the phone pattern - most recent non-empty value -
    # rather than the last-order join, so customers whose newest order predates
    # the field still get a surname once any of their orders carries one.
    with_last_name = data[data["last_name"] != ""]
    last_name_by_customer = (
        with_last_name.sort_values("order_date", kind="mergesort")
        .groupby("email_key")["last_name"]
        .last()
        .rename("last_name")
    )

    # String max is chronological for ISO-8601 "Z" timestamps (see module docstring).
    with_utc = data[data["order_date_utc"] != ""]
    last_utc_by_customer = (
        with_utc.groupby("email_key")["order_date_utc"].max().rename("last_order_utc")
    )

    # SKU(s) of the customer's LAST purchase - the most recent order itself,
    # not the most recent order that happened to carry a SKU. If that order
    # has no SKU the cell is blank, because showing an older order's SKU under
    # a "last purchase" heading would misreport it.
    #
    # One order can list several comma-separated SKUs when it had several line
    # items, so the value is split into a list. It must never be grouped on
    # raw: "una_unidad, pack_favorito" is two products, not a third one.
    last_orders = (
        data.sort_values("order_date", kind="mergesort")
        .groupby("email_key")
        .tail(1)
        .set_index("email_key")
    )
    last_skus_by_customer = last_orders["sku"].apply(
        lambda v: [p.strip() for p in str(v).split(",") if p.strip()]
    ).rename("last_skus")
    last_value_by_customer = last_orders["total_value"].rename("last_order_value")
    # City and gender travel with the same last order, for audience exports.
    # Guarded per column so a CSV predating either still loads.
    last_city_by_customer = (
        last_orders["city"] if "city" in last_orders.columns
        else pd.Series(dtype=object, index=last_orders.index)
    ).rename("city")
    last_gender_by_customer = (
        last_orders["gender"] if "gender" in last_orders.columns
        else pd.Series(dtype=object, index=last_orders.index)
    ).rename("gender")

    grouped = (
        grouped.join(phone_by_customer)
        .join(last_name_by_customer)
        .join(last_utc_by_customer)
        .join(last_skus_by_customer)
        .join(last_value_by_customer)
        .join(last_city_by_customer)
        .join(last_gender_by_customer)
    )
    grouped["phone"] = grouped["phone"].fillna("")
    grouped["last_name"] = grouped["last_name"].fillna("").astype(str)
    grouped["last_order_utc"] = grouped["last_order_utc"].fillna("")
    grouped["last_skus"] = grouped["last_skus"].apply(lambda v: v if isinstance(v, list) else [])
    grouped["last_order_value"] = grouped["last_order_value"].fillna(0.0)
    grouped["city"] = grouped["city"].fillna("").astype(str)
    grouped["gender"] = grouped["gender"].fillna("").astype(str)

    total_customers = int(len(grouped))
    available_skus = sorted({sku for lst in grouped["last_skus"] for sku in lst})
    recurrent = grouped[grouped["orders_count"] >= int(min_orders)].copy()

    # Optional segment filters. Applied before the summary so the tiles
    # describe the segment being listed, and skipped entirely when not asked
    # for, which keeps the plain recurrent-customers figures unchanged.
    if emails:
        wanted = {str(e).strip().lower() for e in emails if str(e).strip()}
        recurrent = recurrent[recurrent.index.isin(wanted)].copy()

    if max_orders:
        recurrent = recurrent[recurrent["orders_count"] <= int(max_orders)].copy()

    if sku_filter:
        wanted = {str(x).strip() for x in sku_filter if str(x).strip()}
        recurrent = recurrent[
            recurrent["last_skus"].apply(lambda L: bool(set(L) & wanted))
        ].copy()

    if inactive_months:
        # last_order_utc is an ISO-8601 "Z" string, so a string comparison is
        # chronological and no tz-aware/naive timestamps are created. Customers
        # with no UTC timestamp are excluded rather than treated as ancient.
        cutoff = (
            pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=int(inactive_months))
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        recurrent = recurrent[
            (recurrent["last_order_utc"] != "")
            & (recurrent["last_order_utc"] < cutoff)
        ].copy()

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
    if paginate:
        total_pages = max(1, (total_rows + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page
        window = recurrent.iloc[start:start + per_page]
    else:
        # Export mode: every matching row, ignoring the page window. Pagination
        # metadata still describes what was returned.
        total_pages = 1
        page = 1
        start = 0
        window = recurrent

    rows = []
    for i, (_, r) in enumerate(window.iterrows(), start=start + 1):
        rows.append({
            "rank": i,
            "name": r["name"],
            "last_name": r["last_name"],
            "phone": r["phone"],
            "last_order_utc": r["last_order_utc"],
            "last_skus": list(r["last_skus"]),
            "last_order_value": float(r["last_order_value"]),
            "city": r["city"],
            "gender": r["gender"],
            "days_since_last_order": _days_since(r["last_order_utc"]),
            "email": r["email"],
            "orders_count": int(r["orders_count"]),
            "total_spent": float(r["total_spent"]),
            "avg_order_value": float(r["total_spent"]) / int(r["orders_count"]) if r["orders_count"] else 0.0,
            "first_order": r["first_order"].strftime("%Y-%m-%d") if pd.notna(r["first_order"]) else "",
            "last_order": r["last_order"].strftime("%Y-%m-%d") if pd.notna(r["last_order"]) else "",
        })

    return {
        "rows": rows,
        # Distinct last-purchase SKUs across all customers, so a filter UI can
        # offer real catalogue values instead of a hardcoded list.
        "available_skus": available_skus,
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
