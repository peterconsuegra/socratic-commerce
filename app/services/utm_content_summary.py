# app/services/utm_content_summary.py
"""
Total sales and repurchase percentage grouped by utm_content, for a single
utm_campaign, across the trailing time windows (Today, Last 7/30/90/180 days)
or a custom range of days (see periods.py).

This is the drill-down counterpart of utm_campaign_summary: that endpoint lists
campaigns, this one breaks a single campaign down by its utm_content values.
A "repurchase" is an order the store flags is_repurchase, as there.

A campaign is matched the way ROAS Link matches it (campaign_key): one Meta
campaign may be tagged "Ad System Sudoración", "AdSystemSudoracin" or
"adsystemsudoracion", and every tag that collapses to the same key is summed.
get_utm_campaign_content_summary answers every campaign in one call.
"""
import logging
import unicodedata

import pandas as pd

from app.services.get_data import load_orders, orders_as_of
from app.services.periods import TIMEZONE, now_bogota_naive, resolve_windows

from app.services.facebook_insights import _normalize_gender

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50


def campaign_key(name) -> str:
    """
    The key a campaign name and its UTM tags are matched on, as ROAS Link's
    _normalize_campaign_key: no whitespace, no '+', lowercase, and no
    non-ASCII characters, because PixelYourSite drops them when it records a
    UTM value ("Ad System Sudoración" was tracked as "AdSystemSudoracin").
    """
    if name is None:
        return ""
    collapsed = "".join(str(name).split()).replace("+", "").lower()
    return collapsed.encode("ascii", "ignore").decode("ascii")


def campaign_keys(name) -> list[str]:
    """
    The keys a requested name may be tagged under, most likely first: the
    tracked form drops accented letters ("sudoracin"), a tag typed by hand
    usually keeps the base letter ("sudoracion").
    """
    keys: list[str] = []
    for candidate in (campaign_key(name),
                      campaign_key(unicodedata.normalize("NFKD", str(name or "")))):
        if candidate and candidate not in keys:
            keys.append(candidate)
    return keys


def _normalize_label(series: pd.Series) -> pd.Series:
    """Trim; treat blank/nan/none as 'undefined'. Keeps original casing."""
    norm = series.fillna("").astype(str).str.strip()
    lowered = norm.str.lower()
    return norm.where(~lowered.isin(["", "nan", "none"]), "undefined")


def _round2(x: float) -> float:
    return round(float(x), 2)


def _load_orders(orders_csv_path: str) -> pd.DataFrame:
    # is_repurchase comes with each order from the store.
    data = load_orders(orders_csv_path)

    data["utm_campaign_norm"] = _normalize_label(data["utm_campaign"])
    data["utm_campaign_key"] = data["utm_campaign_norm"].map(campaign_key)
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


def _match_campaign(data: pd.DataFrame, campaign_name: str | None,
                    campaign_id: str | None) -> pd.Series:
    """
    The orders of the requested campaign, as a mask over data: every tag whose
    campaign_key is the name's (its NFKD form only when the first key has no
    orders at all), plus the orders tagged exactly with campaign_id. An order
    both select counts once.
    """
    mask = pd.Series(False, index=data.index)
    for key in campaign_keys(campaign_name):
        by_key = data["utm_campaign_key"] == key
        if by_key.any():
            mask |= by_key
            break
    wanted_id = (campaign_id or "").strip()
    if wanted_id:
        mask |= data["utm_campaign_norm"] == wanted_id
    return mask


def _suggestions(data: pd.DataFrame, campaign_name: str | None) -> list[str]:
    """Close-ish campaign tags (one key inside the other), ranked by sales."""
    wanted = campaign_key(campaign_name)
    if not wanted:
        return []
    by_sales = (
        data.groupby("utm_campaign_norm")["total_value"].sum().sort_values(ascending=False)
    )
    return [
        str(name) for name in by_sales.index
        if (key := campaign_key(name)) and (wanted in key or key in wanted)
    ][:10]


def _content_rows(window: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Sales, orders and repurchases per group of `by`, top sales first."""
    agg = window.groupby(by).agg(
        total_sales=("total_value", "sum"),
        total_orders=("total_value", "size"),
    )
    rep = window[window["is_repurchase"]].groupby(by).agg(
        repurchase_sales=("total_value", "sum"),
        repurchase_orders=("total_value", "size"),
    )
    agg = agg.join(rep, how="left")
    agg["repurchase_sales"] = agg["repurchase_sales"].fillna(0.0)
    agg["repurchase_orders"] = agg["repurchase_orders"].fillna(0).astype(int)
    return agg.sort_values("total_sales", ascending=False)


def _window_totals(window: pd.DataFrame) -> dict:
    return {
        **_metrics(
            window["total_value"].sum(),
            len(window),
            window.loc[window["is_repurchase"], "total_value"].sum(),
            int(window["is_repurchase"].sum()),
        ),
        "gender_share_sales": _gender_share_sales(window),
    }


def _summarize_window(window: pd.DataFrame, limit: int) -> dict:
    agg = _content_rows(window, ["utm_content_norm"])

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

    totals = _window_totals(window)
    totals["distinct_contents"] = distinct_contents

    return {
        "totals": totals,
        "limit": int(limit) if limit and limit > 0 else None,
        "by_utm_content": by_content,
        "others": others,
    }


def get_utm_content_summary(
    campaign_name: str | None = None,
    period: str = "all",
    orders_csv_path: str = "data/all_orders.csv",
    limit: int = DEFAULT_LIMIT,
    since: str | None = None,
    until: str | None = None,
    campaign_id: str | None = None,
) -> dict:
    """
    Returns total sales and repurchase percentage grouped by utm_content for a
    single campaign.

    Args:
        campaign_name: the campaign to break down, matched on campaign_key
            against every utm_campaign tag; all the tags that match are summed.
        campaign_id: a Meta campaign id, matched exactly against utm_campaign
            (orders tagged with the id rather than the name). With
            campaign_name too, both are summed, each order once. One of the
            two is required.
        period: one of periods.PERIODS keys, "all", or "custom" for since to
            until (YYYY-MM-DD Bogotá days, both included).
        orders_csv_path: path to the all-orders CSV.
        limit: max utm_content rows per period (top N by sales); the rest are
            rolled up into "others". Pass 0 to return every value.

    A campaign no order carries is not an error: campaign_found is false and
    every period comes back empty, with zero totals.

    Raises:
        periods.PeriodError: for a period it cannot answer.
    """
    now = now_bogota_naive()
    windows = resolve_windows(period, since, until, now)
    data = _load_orders(orders_csv_path)
    campaign_data = data[_match_campaign(data, campaign_name, campaign_id)]

    tags = (
        campaign_data.groupby("utm_campaign_norm")["total_value"].sum()
        .sort_values(ascending=False)
    )
    resolved = [str(tag) for tag in tags.index]
    found = bool(resolved)

    out: dict = {
        "generated_at": now.isoformat(),
        "timezone": TIMEZONE,
        "orders_as_of": orders_as_of(orders_csv_path),
        "campaign_found": found,
        "campaign_name": campaign_name or None,
        "campaign_id": (campaign_id or "").strip() or None,
        "utm_campaign": resolved[0] if found else (campaign_name or campaign_id),
        "utm_campaign_key": campaign_key(campaign_name) or None,
        "resolved_utm_campaigns": resolved,
        "periods": {},
    }
    if not found:
        out["suggestions"] = _suggestions(data, campaign_name)
    for window in windows:
        out["periods"][window.key] = {
            **window.describe(),
            **_summarize_window(window.orders(campaign_data, now), limit),
        }

    return out


def get_utm_campaign_content_summary(
    period: str = "all",
    orders_csv_path: str = "data/all_orders.csv",
    limit: int = 0,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """
    Every campaign's utm_content breakdown in one pass over the orders: one
    row per (utm_campaign, utm_content), with the fields of
    get_utm_content_summary's rows plus utm_campaign and utm_campaign_key.
    Summing a key's rows gives get_utm_content_summary's totals for a
    campaign_name with that key.

    Args:
        period, since, until: as get_utm_content_summary.
        limit: max rows per period (top N by sales); the rest are rolled up
            into "others". Default 0: every row, so no campaign is cut short.

    Raises:
        periods.PeriodError: for a period it cannot answer.
    """
    now = now_bogota_naive()
    windows = resolve_windows(period, since, until, now)
    data = _load_orders(orders_csv_path)

    out: dict = {
        "generated_at": now.isoformat(),
        "timezone": TIMEZONE,
        "orders_as_of": orders_as_of(orders_csv_path),
        "periods": {},
    }
    by = ["utm_campaign_norm", "utm_content_norm"]
    for window in windows:
        orders = window.orders(data, now)
        agg = _content_rows(orders, by)
        if limit and limit > 0:
            top, rest = agg.iloc[:limit], agg.iloc[limit:]
        else:
            top, rest = agg, agg.iloc[0:0]

        groups = dict(iter(orders.groupby(by))) if len(top) else {}
        rows = [
            {"utm_campaign": campaign,
             "utm_campaign_key": campaign_key(campaign),
             "utm_content": content,
             **_metrics(row["total_sales"], row["total_orders"],
                        row["repurchase_sales"], row["repurchase_orders"]),
             "gender_share_sales": _gender_share_sales(groups[(campaign, content)])}
            for (campaign, content), row in top.iterrows()
        ]

        others = None
        if len(rest) > 0:
            rest_keys = set(rest.index)
            rest_subset = orders[[k in rest_keys for k in zip(orders["utm_campaign_norm"],
                                                             orders["utm_content_norm"])]]
            others = {
                "rows_count": int(len(rest)),
                **_metrics(rest["total_sales"].sum(), rest["total_orders"].sum(),
                           rest["repurchase_sales"].sum(), rest["repurchase_orders"].sum()),
                "gender_share_sales": _gender_share_sales(rest_subset),
            }

        totals = _window_totals(orders)
        totals["distinct_campaigns"] = int(orders["utm_campaign_norm"].nunique())
        totals["distinct_rows"] = int(len(agg))
        out["periods"][window.key] = {
            **window.describe(),
            "totals": totals,
            "limit": int(limit) if limit and limit > 0 else None,
            "by_campaign_content": rows,
            "others": others,
        }

    return out
