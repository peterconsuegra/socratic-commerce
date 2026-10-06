# app/services/recurrent_customers.py
"""
Customers with more than one paid purchase ("recurrent" / repeat customers),
one row per email.

How many purchases a customer has made is the store's count: the highest
purchase_number among the email's orders. The store links a customer's orders
by phone and email across their whole history, so this also counts purchases
made under another email. The app does not match customers itself.

Email is still the row key because the orders API has no customer id yet. So
a customer who bought under two emails has two rows, and total spent, the last
order and its SKU, phone, city and gender cover only that email's orders.
These screens move to the store's customer id once the API sends one.

Phone is a display column only, never a key: the store does the matching.

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
import threading

import pandas as pd

from app.services.get_data import MISSING_SENTINELS, load_orders

logger = logging.getLogger(__name__)

DEFAULT_PER_PAGE = 100
# Large enough for the segment pages' 5000-row option; the table is cached,
# so a big page only costs rendering.
MAX_PER_PAGE = 5000

# sort key -> (dataframe column, default descending?)
SORT_COLUMNS = {
    "orders": ("orders_count", True),
    "spent": ("total_spent", True),
    "name": ("name", False),
    "email": ("email", False),
    "last_order": ("last_order", True),
    "last_order_utc": ("last_order_utc", True),
    "last_value": ("last_order_value", True),
    # "Days since the last order" is the same column read the other way round:
    # ascending days = most recent last order first. Default ascending, so a
    # win-back list opens with the customers who lapsed most recently.
    "days_since": ("last_order_utc", False),
}
DEFAULT_SORT = "spent"

# Sort keys whose direction is the opposite of their underlying column's.
INVERTED_SORTS = {"days_since"}


def customer_orders(data: pd.DataFrame) -> pd.DataFrame:
    """
    The orders that carry an email, with email_key: the email lowercased, so
    "A@x.com" and "a@x.com" are one row. Email is the customer key of the
    per-customer screens until the orders API sends a customer id.
    """
    data = data.assign(email=data["email"].str.strip(), name=data["name"].str.strip())
    data = data[~data["email"].str.lower().isin(MISSING_SENTINELS | {""})].copy()
    data["email_key"] = data["email"].str.lower()
    return data


def _load_orders(orders_csv_path: str) -> pd.DataFrame:
    return customer_orders(load_orders(orders_csv_path))


def _days_since(utc_iso: str):
    """Whole days between an ISO-8601 "Z" timestamp and now, or None."""
    if not utc_iso:
        return None
    ts = pd.to_datetime(utc_iso, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return int((pd.Timestamp.now(tz="UTC") - ts).days)


def _name_by_customer(data: pd.DataFrame) -> pd.Series:
    """
    Most frequent non-empty name per customer, ties going to the name seen
    first. One grouped count for everyone: the previous per-customer Python
    aggregation ran value_counts ~44k times and took ~10s per request.
    """
    named = data.loc[data["name"] != "", ["email_key", "name"]]
    if named.empty:
        return pd.Series(dtype=object, name="name")
    return (
        named.groupby(["email_key", "name"], sort=False).size().rename("n").reset_index()
        .sort_values("n", ascending=False, kind="mergesort")
        .drop_duplicates("email_key")
        .set_index("email_key")["name"]
    )


# One aggregated customers table per orders-file version, shared by every
# request in the worker. Grouping ~60k orders is far too slow to repeat for
# each page, sort or filter change, and the result only changes when the
# file does. Readers never mutate it: filters below work on copies.
_TABLE_LOCK = threading.Lock()
_TABLE_CACHE: dict = {"key": None, "table": None}


def _file_key(path: str) -> tuple:
    st = os.stat(path)
    return os.path.abspath(path), st.st_mtime_ns, st.st_size


def _customers_table(orders_csv_path: str) -> pd.DataFrame:
    try:
        key = _file_key(orders_csv_path)
    except FileNotFoundError:
        # The file is briefly absent while a refresh rewrites it. Serving the
        # last aggregate for that moment beats failing the page.
        if _TABLE_CACHE["table"] is not None:
            return _TABLE_CACHE["table"]
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    if _TABLE_CACHE["key"] == key:
        return _TABLE_CACHE["table"]
    with _TABLE_LOCK:
        if _TABLE_CACHE["key"] == key:
            return _TABLE_CACHE["table"]
        table = _aggregate_customers(_load_orders(orders_csv_path))
        _TABLE_CACHE["key"], _TABLE_CACHE["table"] = key, table
        logger.info("customers table rebuilt: %d customers from %s", len(table), orders_csv_path)
        return table


def _aggregate_customers(data: pd.DataFrame) -> pd.DataFrame:
    """One row per customer (email, case-insensitive) from the orders rows."""
    grouped = data.groupby("email_key").agg(
        # The store's count of the customer's paid purchases (see the module
        # docstring); 0 while none of the email's orders is numbered yet.
        orders_count=("purchase_number", "max"),
        # Orders under this email, which total_spent sums.
        email_orders=("total_value", "size"),
        total_spent=("total_value", "sum"),
        last_order=("order_date", "max"),
        first_order=("order_date", "min"),
        email=("email", "first"),
    )
    grouped["orders_count"] = grouped["orders_count"].fillna(0).astype(int)
    grouped["name"] = _name_by_customer(data).reindex(grouped.index).fillna("").astype(str)

    # Phone and the UTC timestamp are derived separately and joined on, so the
    # counts and totals above never depend on which orders carry them.
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
    last_city_by_customer = last_orders["city"].rename("city")
    last_gender_by_customer = last_orders["gender"].rename("gender")

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
    return grouped


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
    exclude_emails: set | list | None = None,
    contact_counts: dict | None = None,
    max_contacts: int | None = None,
    unsubscribed_emails: set | list | None = None,
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
        min_orders: minimum purchases (the store's count) to count as
            recurrent (default 2). Pass 1 to include one-time buyers.
        sku_filter: if given, keep only customers whose LAST purchase included
            one of these SKUs.
        inactive_months: if given, keep only customers whose last order (UTC)
            is older than this many months - a lapsed / win-back segment.
        max_orders: if given, keep only customers with at most this many
            purchases, so min_orders/max_orders together bound the range.
        emails: if given, keep only these customers (case-insensitive email
            match). Used to resolve a checkbox selection: without it a caller
            would have to page through the spend-ranked listing and anyone
            below the page cap would silently resolve as missing.
        exclude_emails: if given, drop these customers (lowercased emails),
            e.g. everyone contacted recently. Applied before the summary, so
            the tiles describe the customers actually listed; the number
            dropped is reported as summary["excluded"].
        contact_counts: {(email, last_order_utc): n} - how many times each
            customer was contacted while that was their last order. Every row
            gets "contacts_since_order" from it (0 when absent).
        max_contacts: with contact_counts, drop customers contacted at least
            this many times since their last order; the number dropped is
            reported as summary["exhausted"]. None or 0 means no limit.
        unsubscribed_emails: if given, drop these customers (lowercased
            emails), who used an email's unsubscribe link. Applied after the
            segment filters, so summary["unsubscribed"] counts the ones this
            segment hides.

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

    grouped = _customers_table(orders_csv_path)

    total_customers = int(len(grouped))
    available_skus = sorted({sku for lst in grouped["last_skus"] for sku in lst})
    recurrent = grouped[grouped["orders_count"] >= int(min_orders)].copy()

    # Optional segment filters. Applied before the summary so the tiles
    # describe the segment being listed, and skipped entirely when not asked
    # for, which keeps the plain recurrent-customers figures unchanged.
    if emails:
        wanted = {str(e).strip().lower() for e in emails if str(e).strip()}
        recurrent = recurrent[recurrent.index.isin(wanted)].copy()

    excluded = 0
    if exclude_emails:
        before = len(recurrent)
        recurrent = recurrent[~recurrent.index.isin(list(exclude_emails))].copy()
        excluded = before - len(recurrent)

    # Attempts since the last order, then the limit on them. A purchase after
    # a contact changes last_order_utc, so those earlier contacts stop
    # counting without any bookkeeping.
    counts = contact_counts or {}
    recurrent["contacts_since_order"] = [
        int(counts.get((email, last_utc), 0))
        for email, last_utc in zip(recurrent.index, recurrent["last_order_utc"])
    ] if len(recurrent) else []
    exhausted = 0
    if max_contacts and counts:
        before = len(recurrent)
        recurrent = recurrent[recurrent["contacts_since_order"] < int(max_contacts)].copy()
        exhausted = before - len(recurrent)

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

    unsubscribed = 0
    if unsubscribed_emails:
        before = len(recurrent)
        recurrent = recurrent[~recurrent.index.isin(list(unsubscribed_emails))].copy()
        unsubscribed = before - len(recurrent)

    # Summary over ALL recurrent customers, before search/pagination.
    summary = {
        "total_customers": total_customers,
        "recurrent_customers": int(len(recurrent)),
        "recurrent_orders": int(recurrent["email_orders"].sum()) if len(recurrent) else 0,
        "recurrent_revenue": float(recurrent["total_spent"].sum()) if len(recurrent) else 0.0,
        "excluded": int(excluded),
        "exhausted": int(exhausted),
        "unsubscribed": int(unsubscribed),
    }

    search = (search or "").strip()
    if search:
        needle = search.lower()
        mask = (
            recurrent["email"].str.lower().str.contains(needle, na=False, regex=False)
            | recurrent["name"].str.lower().str.contains(needle, na=False, regex=False)
        )
        recurrent = recurrent[mask].copy()

    ascending = (direction == "asc") != (sort in INVERTED_SORTS)
    recurrent = recurrent.sort_values(column, ascending=ascending, kind="mergesort")

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
            "contacts_since_order": int(r["contacts_since_order"]),
            "email": r["email"],
            "orders_count": int(r["orders_count"]),
            "total_spent": float(r["total_spent"]),
            "avg_order_value": float(r["total_spent"]) / int(r["email_orders"]),
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
