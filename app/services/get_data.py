# app/services/get_data.py
"""
The orders API, and the order CSVs the app keeps from it.

Every order file (all_orders.csv and the date-range files the pages ask for)
is written by write_orders_csv with ORDER_COLUMNS, and read back by
load_orders.

The store works out each order's gender and its place in the customer's
purchase history (purchase_number, is_repurchase), matching customers by phone
and email across their whole history. Those values are copied as given: the
app never guesses a gender or matches customers to decide a repurchase. They
can change for past orders (a cancelled earlier order renumbers the later
ones, a new order can merge two customers), so a sync always overwrites.
"""
import csv
import os
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BOGOTA = ZoneInfo("America/Bogota")

# The store's first paid order is from November 2021. A full re-sync asks for
# one calendar year at a time from here: the whole history in one response is
# ~61k orders and 40 MB.
FIRST_ORDER_YEAR = 2021

# The API's date parameters: whole Colombia days, both ends included.
API_DATE_FORMAT = "%d/%m/%Y"


def _clean_str(v):
    if v is None:
        return ""
    return str(v).strip()


# The orders API uses the literal string "N/A" as its missing-value sentinel
# (it never sends null or ""). Treat it as absent so it never reaches a report.
MISSING_SENTINELS = {"n/a", "na", "none", "null"}


def _clean_optional(v):
    """Like _clean_str, but maps the API's "N/A" sentinel to an empty string."""
    s = _clean_str(v)
    return "" if s.lower() in MISSING_SENTINELS else s


COLOMBIA_COUNTRY_CODE = "57"

# Separators that indicate the field holds more than one number.
_MULTI_NUMBER_SEPARATORS = ("/", ",", ";")


def sanitize_phone(v):
    """
    Normalise a Colombian phone number to E.164, e.g. "310 479 2445" and
    "3104792445" both become "+573104792445".

    Rules, in order:
      - the "N/A" sentinel and blanks become "".
      - a field holding several numbers ("300... / 310...") is left alone;
        picking one of them would be a guess.
      - a value that already starts with "+" keeps its country code and only
        loses its formatting: "+57 300-510-0205" -> "+573005100205". A foreign
        number such as "+447575061132" is therefore never rewritten as
        Colombian.
      - otherwise, digits only. A leading national trunk "0" is dropped, then:
          12 digits starting 57  -> "+" + digits          (country code, no +)
          10 digits starting 3   -> "+57" + digits        (mobile)
          10 digits starting 6   -> "+57" + digits        (landline, post-2022)

    Anything else - truncated numbers, two numbers concatenated into 20 digits,
    unexpected lengths - is returned unchanged rather than coerced, so a bad
    value stays visibly bad instead of becoming a plausible wrong number.

    Note this is formatting only, for display and for WhatsApp sends. The app
    never matches customers by phone: the store does, and reports the result
    as purchase_number.
    """
    s = _clean_optional(v)
    if not s:
        return ""

    if any(sep in s for sep in _MULTI_NUMBER_SEPARATORS):
        return s

    has_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return s

    if has_plus:
        return "+" + digits

    core = digits.lstrip("0")

    if len(core) == 12 and core.startswith(COLOMBIA_COUNTRY_CODE):
        return "+" + core

    if len(core) == 10 and core[0] in ("3", "6"):
        return "+" + COLOMBIA_COUNTRY_CODE + core

    return s


ORDER_COLUMNS = [
    "order_id",
    "order_date",
    "order_date_utc",
    "name",
    # last_name arrived later than name and is optional: the API sends "N/A"
    # when the order has no billing last name stored.
    "last_name",
    # The store's guess from the billing first name: "female", "male" or
    # "unknown" (unisex names, surnames or companies typed as a name, names it
    # does not know). Shown as given, never re-guessed here.
    "gender",
    "email",
    "phone",
    "city",
    "state",
    "order_lat",
    "order_lng",
    "total_value",
    "product",
    # sku can hold several comma-separated values for a multi-item order
    # ("una_unidad, pack_ecostand"), so it is not safe as a grouping key
    # without splitting and exploding first. It is also not a second view of
    # "product": product reports one arbitrary line item for a multi-item
    # order, sku reports all of them, so the two must not be zipped together.
    "sku",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_answer",
    "address_1",
    # Which paid purchase of the customer this order is, across their whole
    # history (1 = first purchase), and whether that number is above 1. Both
    # are blank when the store's index had not caught up ("N/A"); the next
    # sync fills them in.
    "purchase_number",
    "is_repurchase",
]


def _gender(v) -> str:
    s = _clean_str(v).lower()
    return s if s in ("female", "male") else "unknown"


def _purchase_number(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return ""  # "N/A": not numbered yet
    return str(n) if n >= 1 else ""


def _is_repurchase(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    s = _clean_str(v).lower()
    return s if s in ("true", "false") else ""  # "N/A": not numbered yet


def _order_row(item: dict) -> dict:
    name_raw = _clean_str(item.get("name"))
    # Compound surnames arrive whole ("Guzmán García") and must stay whole.
    last_name_raw = _clean_optional(item.get("last_name"))

    return {
        "order_id": _clean_str(item.get("order_id")),
        "order_date": _clean_str(item.get("order_date")),
        "order_date_utc": _clean_optional(item.get("order_date_utc")),
        "name": name_raw.title() if name_raw else "",
        "last_name": last_name_raw.title() if last_name_raw else "",
        "gender": _gender(item.get("gender")),
        "email": _clean_str(item.get("email")),
        "phone": sanitize_phone(item.get("phone")),
        "city": _clean_str(item.get("city")),
        "state": _clean_str(item.get("state")),
        "order_lat": _clean_str(item.get("order_lat")),
        "order_lng": _clean_str(item.get("order_lng")),
        "total_value": _clean_str(item.get("total_value")),
        "product": _clean_str(item.get("product")),
        # _clean_optional, not _clean_str: the API sends the literal "N/A"
        # when an order resolves to no SKU, and that sentinel must not reach
        # the CSV as if it were a real SKU.
        "sku": _clean_optional(item.get("sku")),
        "utm_source": _clean_str(item.get("utm_source")).lower(),
        "utm_medium": _clean_str(item.get("utm_medium")),
        "utm_campaign": _clean_str(item.get("utm_campaign")),
        "utm_term": _clean_str(item.get("utm_term")),
        "utm_content": _clean_str(item.get("utm_content")),
        "utm_answer": _clean_str(item.get("utm_answer")),
        "address_1": _clean_str(item.get("address_1")),
        "purchase_number": _purchase_number(item.get("purchase_number")),
        "is_repurchase": _is_repurchase(item.get("is_repurchase")),
    }


def fetch_orders(
    *,
    orders_url: str,
    api_key: str,
    start_date: str | None = None,
    end_date: str | None = None,
    timeout: int = 120,
) -> list[dict]:
    """
    Every paid order from start_date to end_date (DD/MM/YYYY, Colombia days,
    both included), newest first. Without dates: the store's whole history,
    up to today.

    One request per calendar year in the window, so a long window never asks
    the API for one huge response. Raises on any failed request: a partial
    history must never replace a complete one.
    """
    today = datetime.now(BOGOTA).date()
    start = datetime.strptime(start_date, API_DATE_FORMAT).date() if start_date else date(FIRST_ORDER_YEAR, 1, 1)
    end = datetime.strptime(end_date, API_DATE_FORMAT).date() if end_date else today

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    orders: list[dict] = []
    for year in range(end.year, start.year - 1, -1):
        params = {
            "start_date": max(start, date(year, 1, 1)).strftime(API_DATE_FORMAT),
            "end_date": min(end, date(year, 12, 31)).strftime(API_DATE_FORMAT),
        }
        response = requests.get(orders_url, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
        chunk = response.json()
        if not isinstance(chunk, list):
            raise ValueError("Expected the orders API to return a list of orders.")
        orders.extend(item for item in chunk if isinstance(item, dict))
    return orders


def write_orders_csv(orders: list[dict], csv_path: str) -> None:
    """
    Write the orders beside the live file and swap it in with one rename, so
    a request served while a refresh runs (refreshes run in the background)
    sees either the old complete file or the new one, never a missing or
    half-written CSV.
    """
    tmp_path = f"{csv_path}.tmp"
    try:
        with open(tmp_path, mode="w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=ORDER_COLUMNS)
            writer.writeheader()
            writer.writerows(_order_row(item) for item in orders)
        os.replace(tmp_path, csv_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def fetch_orders_and_write_csv(
    *,
    orders_url: str,
    api_key: str,
    file_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
    cwd: str | None = None,
    timeout: int = 120,
) -> tuple[bool, dict]:
    """
    Fetch the orders (the whole history without dates) and write
    data/<file_name>.

    Returns: (ok, payload)
      - ok=True: payload has message + csv_url
      - ok=False: payload has message
    """
    try:
        orders = fetch_orders(
            orders_url=orders_url,
            api_key=api_key,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
        )
        if not orders:
            return False, {"message": "No data returned from the API."}

        data_dir = os.path.join(cwd or os.getcwd(), "data")
        os.makedirs(data_dir, exist_ok=True)

        # Pages derive "<name>_filtered.csv" files from some order files;
        # drop the old one so it is rebuilt from the new orders.
        filtered_csv_path = os.path.join(data_dir, f"{os.path.splitext(file_name)[0]}_filtered.csv")
        if os.path.exists(filtered_csv_path):
            os.remove(filtered_csv_path)

        write_orders_csv(orders, os.path.join(data_dir, file_name))
        return True, {
            "message": f"data/{file_name} created successfully!",
            "csv_url": f"/static/data/{file_name}",
        }

    except requests.exceptions.RequestException as e:
        return False, {"message": f"Request error occurred: {e}"}
    except Exception as e:
        return False, {"message": f"An unexpected error occurred: {e}"}


def filter_by_location(orders: pd.DataFrame, state: str | None = None, city: str | None = None) -> pd.DataFrame:
    """
    The orders of one department (state) and/or one city, matched exactly on
    the store's labels ("Antioquia", "MEDELLIN (ANT)"). None keeps every order.
    """
    if state:
        orders = orders[orders["state"] == state]
    if city:
        orders = orders[orders["city"] == city]
    return orders


def location_choices(csv_path: str, state: str | None = None) -> tuple[list[str], list[str]]:
    """
    The departments and the cities found in an order file, sorted, for a
    location filter. With a state that is among them, only its cities.
    Missing values ("N/A", blank) are left out.
    """
    if not os.path.exists(csv_path):
        return [], []
    places = pd.read_csv(csv_path, usecols=["state", "city"], dtype=str, keep_default_na=False)
    places = places.apply(lambda col: col.str.strip())
    places = places.mask(places.apply(lambda col: col.str.lower().isin(MISSING_SENTINELS | {""})))

    states = sorted(places["state"].dropna().unique())
    if state in states:
        places = places[places["state"] == state]
    cities = sorted(places["city"].dropna().unique())
    return states, cities


class OutdatedOrdersFile(ValueError):
    """An order CSV written before the store sent gender and repurchase fields."""


def orders_file_is_current(csv_path: str) -> bool:
    """Whether the file carries the store's repurchase fields (header check only)."""
    try:
        with open(csv_path, newline="", encoding="utf-8") as file:
            header = next(csv.reader(file), [])
    except OSError:
        return False
    return {"purchase_number", "is_repurchase"} <= set(header)


def load_orders(csv_path: str) -> pd.DataFrame:
    """
    An order CSV as a DataFrame, one row per paid order:

      - text columns as strings, "" where missing (a column absent from the
        file reads as all "");
      - order_date parsed (Colombia time, tz-naive); rows without one dropped;
      - total_value as a float;
      - purchase_number as a float, NaN where the store had not numbered the
        order yet;
      - is_repurchase as a bool, False where not numbered yet - so such an
        order counts as neither a repurchase nor (purchase_number == 1) a
        first purchase until the next sync.

    Raises OutdatedOrdersFile for a file written before those fields existed:
    the refresh that replaces it is already due (see orders_file_is_current).
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Orders data file not found: {csv_path}")
    if not orders_file_is_current(csv_path):
        raise OutdatedOrdersFile(
            f"{os.path.basename(csv_path)} was saved before the store sent each order's "
            "gender and purchase number. It is being re-synced: reload in a minute, or "
            "pick the date range again."
        )

    data = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    data = data.assign(**{col: "" for col in ORDER_COLUMNS if col not in data.columns})
    data["order_date"] = pd.to_datetime(data["order_date"], errors="coerce")
    data = data.dropna(subset=["order_date"]).copy()
    data["total_value"] = pd.to_numeric(data["total_value"], errors="coerce").fillna(0.0)
    data["purchase_number"] = pd.to_numeric(data["purchase_number"], errors="coerce")
    data["is_repurchase"] = data["is_repurchase"].str.lower().eq("true")
    return data
