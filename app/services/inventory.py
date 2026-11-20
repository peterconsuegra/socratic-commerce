# app/services/inventory.py
"""
The tubes of deodorant sold in a date range, for /inventory: every pack in
an order times the tubes that pack holds.

A pack is known by its SKU (TUBES_PER_SKU). An order can hold several packs,
its sku column then lists them all ("una_unidad, pack_favorito"), and each
counts. Orders from before the store had SKUs (up to September 2023, and the
odd one since) carry only a product name; those are counted from the name
when it states the tubes ("(10 unidades)", "Dos (2) Unidades", "+ 4 tubos",
"Promoción: (2X1)"), or from LEGACY_TUBES_BY_PRODUCT. Anything else is listed
as unmatched, never guessed, so a new pack shows up there until it is added
to TUBES_PER_SKU.

The SKU wins over the product name: three pack_believer orders in July 2023
are named "Promoción: (2X1)" but were paid at the 12-tube price.

The orders API sends no line-item quantity, so two of the same pack in one
order count as one pack.
"""
import re

import pandas as pd

from app.services.get_data import load_orders
from app.services.periods import orders_cutoff

TUBES_PER_SKU: dict[str, int] = {
    "una_unidad": 1,
    "pack_valentin": 2,
    "pack_favorito": 3,
    "pack_familiar": 5,
    "pack_regala_salud": 10,
    "pack_believer": 12,
    "pack_ecostand": 24,
    "pack_ecohotel": 24,
}

# The store's launch product (November 2021), sold one tube at a time before
# it had a SKU or a pack size in its name.
LEGACY_TUBES_BY_PRODUCT: dict[str, int] = {
    "Desodorante Save A Playa": 1,
    "Desodorante Natural Save A Playa": 1,
}

# "(24 unidades + ecostand)", "Dos (2) Unidades", "+ 4 tubos de desodorante"
_UNITS_IN_NAME = re.compile(r"(\d+)\)?\s*(?:unidades|tubos)\b", re.IGNORECASE)
# "Promoción: (2X1)", "(3X2)": the first number is the tubes delivered.
_N_FOR_M_IN_NAME = re.compile(r"\b(\d+)\s*X\s*\d+\b", re.IGNORECASE)


def tubes_from_product_name(name: str) -> int | None:
    """The tubes a product name states, or None when it does not say."""
    name = (name or "").strip()
    if name in LEGACY_TUBES_BY_PRODUCT:
        return LEGACY_TUBES_BY_PRODUCT[name]
    for pattern in (_UNITS_IN_NAME, _N_FOR_M_IN_NAME):
        match = pattern.search(name)
        if match and int(match.group(1)) > 0:
            return int(match.group(1))
    return None


def _pack_lines(orders: pd.DataFrame) -> pd.DataFrame:
    """
    One row per pack in an order: order_id, pack (the SKU, or the product name
    for an order without one), source ("sku" or "product") and tubes_per_pack
    (NaN when unknown).
    """
    lines = orders[["order_id", "sku", "product"]].assign(
        sku=orders["sku"].str.split(",")
    ).explode("sku")
    lines["sku"] = lines["sku"].fillna("").str.strip()

    by_sku = lines["sku"] != ""
    lines["pack"] = lines["sku"].where(by_sku, lines["product"].str.strip())
    lines["source"] = by_sku.map({True: "sku", False: "product"})
    lines["tubes_per_pack"] = pd.Series(
        [TUBES_PER_SKU.get(s) if s else tubes_from_product_name(p)
         for s, p in zip(lines["sku"], lines["product"])],
        index=lines.index, dtype="float",
    )
    return lines[["order_id", "pack", "source", "tubes_per_pack"]]


def build_inventory(csv_path: str, include_today: bool = False) -> dict | None:
    """
    The tubes sold in the orders of csv_path (the page's date-range file), up
    to the dashboards' cutoff (orders_cutoff). None when there are no orders.

      summary:   tubes, packs, orders, tubes per order and per day, the
                 first and last day with orders, and how many packs were
                 left out as unmatched;
      packs:     per pack, tubes_per_pack x packs = tubes, largest first;
      unmatched: packs whose tubes are unknown, with their order count.
    """
    orders = load_orders(csv_path)
    orders = orders[orders["order_date"] <= orders_cutoff(include_today)]
    if orders.empty:
        return None

    lines = _pack_lines(orders)
    known = lines.dropna(subset=["tubes_per_pack"])
    unknown = lines[lines["tubes_per_pack"].isna()]

    per_pack = (
        known.groupby(["pack", "source", "tubes_per_pack"], sort=False)
        .agg(packs=("order_id", "size"), orders=("order_id", "nunique"))
        .reset_index()
    )
    per_pack["tubes"] = per_pack["tubes_per_pack"] * per_pack["packs"]
    tubes = int(per_pack["tubes"].sum())
    per_pack = per_pack.sort_values(["tubes", "packs"], ascending=False)

    first_day = orders["order_date"].min().normalize()
    last_day = orders["order_date"].max().normalize()
    days = int((last_day - first_day).days) + 1
    counted_orders = int(known["order_id"].nunique())

    return {
        "summary": {
            "tubes": tubes,
            "packs": int(per_pack["packs"].sum()),
            "orders": len(orders),
            "counted_orders": counted_orders,
            "tubes_per_order": tubes / counted_orders if counted_orders else 0.0,
            "tubes_per_day": tubes / days,
            "days": days,
            "first_day": f"{first_day:%Y-%m-%d}",
            "last_day": f"{last_day:%Y-%m-%d}",
            "unmatched_packs": len(unknown),
        },
        "packs": [
            {
                "pack": r.pack,
                "source": r.source,
                "tubes_per_pack": int(r.tubes_per_pack),
                "packs": int(r.packs),
                "orders": int(r.orders),
                "tubes": int(r.tubes),
                "share": r.tubes / tubes * 100.0 if tubes else 0.0,
            }
            for r in per_pack.itertuples(index=False)
        ],
        "unmatched": [
            {"pack": pack or "(no SKU or product name)", "source": source, "packs": int(n)}
            for (pack, source), n in unknown.groupby(["pack", "source"]).size()
            .sort_values(ascending=False).items()
        ],
    }
