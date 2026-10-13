# app/services/orders_by_kind.py
"""
Where each kind of order comes from - first-time orders (a customer's first
paid purchase, the store's purchase_number 1) or repurchases (every later one,
the store's is_repurchase) - by month, by utm_source, utm_campaign and
utm_content.

Views of one kind of order: a monthly trend by source since UTM tracking
began; for any month since the first sale, the breakdown source > campaign >
content, the month day by day (weekends and Colombian holidays set apart), and
where those customers live, what they bought and when; and two periods side
by side ("VS") by one of the UTM fields. UTM values are shown as the store
recorded them: "undefined" means the order carried no UTM, and "n/a" marks
orders from before the store recorded UTMs.
"""
import logging
import os
import threading
from functools import lru_cache

import holidays
import pandas as pd

from app.services.get_data import BOGOTA, load_orders
from app.services.recurrent_customers import _file_key

logger = logging.getLogger(__name__)

# The trend chart's series: these sources by name, then every other source
# together, then the orders without a UTM. Fixed rather than ranked, so a
# source keeps its place and colour as the shares move.
TREND_SOURCES = ["wati", "facebook", "google", "ig"]
OTHER_SERIES = "other sources"
NO_UTM_SERIES = "no UTM (undefined, n/a)"
NO_UTM = {"undefined", "n/a", "(not set)"}
NOT_SET = "(not set)"
# The trend starts after August 2023: the store began recording UTMs in July
# and August 2023, and orders before carry "n/a". The month selector and the
# breakdown still reach back to the first sale.
TREND_START = pd.Period("2023-09", freq="M")
UTM_COLUMNS = ["utm_source", "utm_campaign", "utm_content"]
# The kinds of order, as the store numbers each customer's paid purchases.
KINDS = {
    "first": lambda data: data["purchase_number"].eq(1),
    "repeat": lambda data: data["is_repurchase"],
}
# The city and product charts show this many bars, then one for the rest.
TOP_BARS = 10
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# The day-by-day chart's kinds of day. A holiday that falls on a weekend
# counts as a holiday.
DAY_TYPES = {"weekday": "Weekdays", "weekend": "Weekends", "holiday": "Holidays"}

_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "kinds": None, "orders_by_month": None}


def _load(orders_csv_path: str, kind: str) -> tuple[pd.DataFrame, pd.Series]:
    """(the orders of one kind, all orders per month), cached per orders-file version."""
    key = _file_key(orders_csv_path)
    if _CACHE["key"] == key:
        return _CACHE["kinds"][kind], _CACHE["orders_by_month"]
    with _LOCK:
        if _CACHE["key"] != key:
            data = load_orders(orders_csv_path)
            month = data["order_date"].dt.to_period("M")
            kinds = {name: _prepare(data.loc[picks(data)], month) for name, picks in KINDS.items()}
            _CACHE.update(key=key, kinds=kinds, orders_by_month=month.value_counts())
            logger.info("orders by kind rebuilt from %s (%s)", orders_csv_path,
                        ", ".join(f"{len(orders)} {name}" for name, orders in kinds.items()))
        return _CACHE["kinds"][kind], _CACHE["orders_by_month"]


def _prepare(orders: pd.DataFrame, month: pd.Series) -> pd.DataFrame:
    """The columns the views read: blank UTMs and cities as "(not set)", sources in lower case."""
    orders = orders[["order_date", "total_value", *UTM_COLUMNS, "city", "state", "sku", "product"]].copy()
    orders["month"] = month[orders.index]
    orders["day"] = orders["order_date"].dt.normalize()
    for col in [*UTM_COLUMNS, "city"]:
        orders[col] = orders[col].str.strip().replace("", NOT_SET)
    for col in ["state", "sku", "product"]:
        orders[col] = orders[col].str.strip()
    orders["utm_source"] = orders["utm_source"].str.lower()
    return orders


def _row(name: str, orders: int, revenue: float, total: int) -> dict:
    return {"name": name, "orders": int(orders), "share": orders / total if total else 0.0,
            "revenue": float(revenue), "avg_value": revenue / orders if orders else 0.0}


def _ranked(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (-r["orders"], -r["revenue"], r["name"]))


def _breakdown(orders: pd.DataFrame) -> list[dict]:
    """The month's orders as source > campaign > content, largest first."""
    total = len(orders)
    leaves = orders.groupby(UTM_COLUMNS).agg(orders=("total_value", "size"), revenue=("total_value", "sum"))
    sources = []
    for source, by_source in leaves.groupby(level="utm_source"):
        campaigns = []
        for campaign, by_campaign in by_source.groupby(level="utm_campaign"):
            contents = [_row(content, r.orders, r.revenue, total)
                        for (_s, _c, content), r in by_campaign.iterrows()]
            campaigns.append({**_row(campaign, by_campaign["orders"].sum(), by_campaign["revenue"].sum(), total),
                              "contents": _ranked(contents)})
        sources.append({**_row(source, by_source["orders"].sum(), by_source["revenue"].sum(), total),
                        "campaigns": _ranked(campaigns)})
    return _ranked(sources)


def _with_rest(rows: list[dict], rest_label: str) -> list[dict]:
    """The first TOP_BARS rows for a chart, then one row for all the others."""
    rest = rows[TOP_BARS:]
    if not rest:
        return rows
    return rows[:TOP_BARS] + [{"name": rest_label.format(n=len(rest)), "orders": sum(r["orders"] for r in rest),
                               "share": sum(r["share"] for r in rest), "other": True}]


def _cities(orders: pd.DataFrame) -> list[dict]:
    """Where the customers live: one row per city of the order, with its department, largest first."""
    total = len(orders)
    by_city = orders.groupby("city").agg(orders=("total_value", "size"), revenue=("total_value", "sum"))
    department = orders.groupby("city")["state"].agg(lambda s: s.mode().iat[0])
    return _ranked([{**_row(city, r.orders, r.revenue, total), "state": department[city]}
                    for city, r in by_city.iterrows()])


def _products(orders: pd.DataFrame, of_kind: pd.DataFrame) -> list[dict]:
    """
    What the customers bought: the month's orders that included each product,
    by SKU. An order with several products counts once for each, so
    the shares can add up to more than 100%. Orders from before the store set
    SKUs (most of them before September 2023) count by product name instead.
    Revenue belongs to the order, not to its products, so it is not split here.
    """
    total = len(orders)
    # A SKU is named as the store showed it that month, else by its latest
    # name, both from the orders holding that SKU alone: "product" names one
    # arbitrary item of a multi-item order.
    single = of_kind[of_kind["sku"].ne("") & ~of_kind["sku"].str.contains(",", regex=False)].sort_values("order_date")
    names = {**single.groupby("sku")["product"].last().to_dict(),
             **single[single.index.isin(orders.index)].groupby("sku")["product"].last().to_dict()}
    skus = orders["sku"].str.split(",").explode().str.strip()
    counts = skus[skus.ne("")].reset_index().drop_duplicates()["sku"].value_counts()
    by_name = orders.loc[orders["sku"].eq(""), "product"].replace("", NOT_SET).value_counts()
    rows = [{"name": names.get(sku, sku), "sku": sku, "orders": int(n)} for sku, n in counts.items()]
    rows += [{"name": name, "sku": "", "orders": int(n)} for name, n in by_name.items()]
    return sorted(({**r, "share": r["orders"] / total} for r in rows), key=lambda r: (-r["orders"], r["name"]))


def _times(orders: pd.DataFrame) -> dict:
    """
    When the customers bought: orders by hour of the day and by day of the
    week, Colombia time (the store's order_date). peak_hour and
    peak_day index the busiest row of each, None without orders.
    """
    total = len(orders)
    hours = orders["order_date"].dt.hour.value_counts().reindex(range(24), fill_value=0)
    days = orders["order_date"].dt.weekday.value_counts().reindex(range(7), fill_value=0)
    return {
        "hours": [{"label": f"{h:02d}:00–{h:02d}:59", "orders": int(n), "share": n / total if total else 0.0}
                  for h, n in hours.items()],
        "weekdays": [{"label": WEEKDAYS[d], "orders": int(n), "share": n / total if total else 0.0}
                     for d, n in days.items()],
        "peak_hour": int(hours.idxmax()) if total else None,
        "peak_day": int(days.idxmax()) if total else None,
    }


@lru_cache(maxsize=None)
def _colombian_holidays(year: int) -> dict:
    """
    Colombia's public holidays in a year, {date: name}: the days the law
    sets, most of them moved to the Monday after (Ley Emiliani).
    """
    return dict(holidays.country_holidays("CO", years=year, language="en_US"))


def _daily(orders: pd.DataFrame, month: pd.Period, through: pd.Timestamp) -> dict:
    """
    The month day by day, each day a weekday, a weekend day or a public
    holiday in Colombia, with its orders and revenue up to the last
    complete day (None after it: a month under way still shows the weekends
    and holidays to come), and the average complete day of each kind,
    weekends and holidays against weekdays.
    """
    days = pd.date_range(month.start_time, month.end_time.normalize(), freq="D")
    per_day = (orders.groupby("day").agg(orders=("total_value", "size"), revenue=("total_value", "sum"))
               .reindex(days, fill_value=0))
    rows = []
    for day, r in per_day.iterrows():
        weekday, holiday = WEEKDAYS[day.weekday()], _colombian_holidays(day.year).get(day.date())
        complete = day <= through
        rows.append({
            "date": f"{day:%Y-%m-%d}", "label": f"{weekday} {day.day} {day:%b %Y}",
            "short": f"{weekday[:3]} {day.day} {day:%b}", "tick": [str(day.day), weekday[:2]],
            "type": "holiday" if holiday else "weekend" if day.weekday() >= 5 else "weekday",
            "holiday": holiday, "complete": complete,
            "orders": int(r.orders) if complete else None, "revenue": float(r.revenue) if complete else None,
        })
    complete = [r for r in rows if r["complete"]]
    averages = []
    for kind, label in DAY_TYPES.items():
        of_kind = [r for r in complete if r["type"] == kind]
        n = len(of_kind)
        averages.append({"type": kind, "label": label, "days": n,
                         "orders": sum(r["orders"] for r in of_kind) / n if n else None,
                         "revenue": sum(r["revenue"] for r in of_kind) / n if n else None})
    weekdays = averages[0]
    for a in averages[1:]:
        a["vs_weekdays"] = _change(a["orders"], weekdays["orders"]) if a["days"] and weekdays["days"] else None
    return {
        "days": rows,
        "complete_days": len(complete),
        "averages": averages,
        "holidays": [r for r in rows if r["holiday"]],
        "through": _label(through, through) if complete and not rows[-1]["complete"] else None,
    }


def _trend(of_kind: pd.DataFrame, months: pd.PeriodIndex) -> dict:
    """Orders per month in the fixed series order, for a stacked chart."""
    source = of_kind["utm_source"]
    series = source.where(source.isin(TREND_SOURCES), OTHER_SERIES).mask(source.isin(NO_UTM), NO_UTM_SERIES)
    counts = (
        of_kind.groupby([of_kind["month"], series]).size().unstack(fill_value=0)
        .reindex(index=months, columns=[*TREND_SOURCES, OTHER_SERIES, NO_UTM_SERIES], fill_value=0)
    )
    return {
        "months": [str(m) for m in months],
        "series": [{"name": name, "counts": [int(v) for v in counts[name]]} for name in counts.columns],
    }


def orders_report(orders_csv_path: str, kind: str, month: str | None = None) -> dict:
    """
    The report on one kind of order ("first" or "repeat", see KINDS) for one
    month (YYYY-MM): by default the last complete month, or the current one
    while it is the only month on record.
    """
    of_kind, orders_by_month = _load(orders_csv_path, kind)
    current = pd.Timestamp.now(tz=BOGOTA).tz_localize(None).to_period("M")
    if of_kind.empty:
        return {"months": [], "selected": None, "trend": {"months": [], "series": []}, "breakdown": [], "kpis": None}

    months = pd.period_range(of_kind["month"].min(), current, freq="M")
    per_month = of_kind["month"].value_counts().reindex(months, fill_value=0)

    try:
        selected = pd.Period(month, freq="M") if month else None
    except (TypeError, ValueError):
        selected = None
    if selected not in months:
        selected = current - 1 if current - 1 in months else current

    orders = of_kind[of_kind["month"] == selected]
    total = len(orders)
    revenue = float(orders["total_value"].sum())
    all_orders = int(orders_by_month.get(selected, 0))
    cities = _cities(orders) if total else []
    products = _products(orders, of_kind) if total else []

    return {
        "months": [{"month": str(m), "orders": int(per_month[m]), "partial": m == current}
                   for m in reversed(months)],
        "selected": {"month": str(selected), "partial": selected == current},
        "kpis": {
            "orders": total,
            "share_of_orders": total / all_orders if all_orders else 0.0,
            "all_orders": all_orders,
            "revenue": revenue,
            "avg_value": revenue / total if total else 0.0,
        },
        "trend": _trend(of_kind, months[months >= TREND_START]),
        "breakdown": _breakdown(orders),
        "daily": _daily(orders, selected, _last_complete_day(orders_csv_path)),
        "cities": cities,
        "cities_chart": _with_rest(cities, "{n} other cities"),
        "products": products,
        "products_chart": _with_rest(products, "{n} other products"),
        "times": _times(orders),
    }


# ---- Two periods side by side ("VS") ------------------------------------------

COMPARE_MODES = ("week", "month", "custom")
COMPARE_BY = {"source": "utm_source", "campaign": "utm_campaign", "content": "utm_content"}
# Campaigns and contents run to dozens a period: the comparison lists the top
# ones by either period and rolls the rest into one row.
COMPARE_TOP = 10
CUSTOM_DAYS = 30
ONE_DAY = pd.Timedelta(days=1)


def _label(start: pd.Timestamp, end: pd.Timestamp) -> str:
    """'September 2026', '1 – 9 Oct 2026', '28 Sep – 4 Oct 2026' or '28 Dec 2025 – 3 Jan 2026'."""
    if start == end:
        return f"{start.day} {start:%b %Y}"
    if start.day == 1 and end == start + pd.offsets.MonthEnd(0):
        return f"{start:%B %Y}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day} – {end.day} {end:%b %Y}"
    if start.year == end.year:
        return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"
    return f"{start.day} {start:%b %Y} – {end.day} {end:%b %Y}"


def _period(start: pd.Timestamp, end: pd.Timestamp, partial: bool = False) -> dict:
    return {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"),
            "label": _label(start, end) + (" (to date)" if partial else ""), "partial": partial}


def _change(a: float, b: float) -> dict:
    return {"a": a, "b": b, "pct": (a - b) / b if b else None}


def _compare_rows(a_orders: pd.DataFrame, b_orders: pd.DataFrame, column: str, top: int | None) -> list[dict]:
    """One row per value: orders in A and B, the largest in either period first."""
    counts = pd.concat([a_orders[column].value_counts().rename("a"), b_orders[column].value_counts().rename("b")],
                       axis=1).fillna(0).astype(int)
    counts = counts.assign(peak=counts.max(axis=1), name=counts.index).sort_values(
        ["peak", "a", "name"], ascending=[False, False, True])
    shown, rest = (counts.iloc[:top], counts.iloc[top:]) if top else (counts, counts.iloc[0:0])
    rows = [{"name": str(name), **_change(int(r.a), int(r.b))} for name, r in shown.iterrows()]
    rows.sort(key=lambda r: (-r["a"], -r["b"], r["name"]))
    if len(rest):
        rows.append({"name": f"{len(rest)} more", "other": True, **_change(int(rest["a"].sum()), int(rest["b"].sum()))})
    return rows


def _week_and_month_options(days: pd.Series, yesterday: pd.Timestamp) -> tuple[list[dict], list[dict]]:
    """The weeks (Monday to Sunday) and months since TREND_START with at least one complete day."""
    first = max(days.min(), TREND_START.start_time)
    weeks, start = [], first - pd.Timedelta(days=first.weekday())
    while start <= yesterday:
        end = start + pd.Timedelta(days=6)
        weeks.append({"value": start.strftime("%Y-%m-%d"),
                      "label": f"{start:%G-W%V} · {_label(start, min(end, yesterday))}"
                               + (" (to date)" if end > yesterday else "")})
        start += pd.Timedelta(days=7)
    months = [{"value": str(m), "label": f"{m.start_time:%B %Y}" + (" (to date)" if m.end_time.normalize() > yesterday else "")}
              for m in pd.period_range(first.to_period("M"), yesterday.to_period("M"), freq="M")]
    return weeks[::-1], months[::-1]


def _last_complete_day(orders_csv_path: str) -> pd.Timestamp:
    """
    The last day whose orders are all in the file: the day before it was
    synced (Colombia time), which is yesterday when the sync is current. A
    period "to date" never counts a day that was only partly synced.
    """
    synced = pd.Timestamp(os.path.getmtime(orders_csv_path), unit="s", tz="UTC").tz_convert(BOGOTA)
    today = pd.Timestamp.now(tz=BOGOTA)
    return min(synced, today).tz_localize(None).normalize() - ONE_DAY


def _parse_day(value: str | None) -> pd.Timestamp | None:
    try:
        return pd.Timestamp(value).normalize() if value else None
    except (TypeError, ValueError):
        return None


def compare_periods(orders_csv_path: str, kind: str, mode: str = "month", by: str = "source",
                    week: str | None = None, month: str | None = None, custom: dict | None = None) -> dict:
    """
    The orders of one kind ("first" or "repeat", see KINDS) of two periods
    side by side, by utm_source, utm_campaign or utm_content: period A (the
    one looked at) against period B.

    mode "week": A is a week (Monday to Sunday), B the week before.
    mode "month": A is a calendar month, B the month before.
    mode "custom": A and B are any two date ranges (custom: a_start, a_end,
    b_start, b_end as YYYY-MM-DD); by default the last 30 complete days and
    the 30 before.
    Only complete days count - those before the file's last sync, see
    _last_complete_day: a week or month still under way is compared on its
    days so far against the same number of days of the one before. By
    default A is the last complete week or month.
    """
    of_kind, _orders_by_month = _load(orders_csv_path, kind)
    mode = mode if mode in COMPARE_MODES else "month"
    by = by if by in COMPARE_BY else "source"
    yesterday = _last_complete_day(orders_csv_path)
    out = {"mode": mode, "by": by, "error": None, "week": None, "month": None, "custom": None,
           "through": _label(yesterday, yesterday)}
    if of_kind.empty:
        return {**out, "a": None, "b": None, "kpis": None, "rows": [], "weeks": [], "months": []}
    out["weeks"], out["months"] = _week_and_month_options(of_kind["day"], yesterday)

    if mode == "week":
        values = [w["value"] for w in out["weeks"]]
        # The last complete week: yesterday's, if yesterday was a Sunday.
        current_start = yesterday - pd.Timedelta(days=yesterday.weekday())
        default = (current_start - pd.Timedelta(days=0 if yesterday.weekday() == 6 else 7)).strftime("%Y-%m-%d")
        out["week"] = week if week in values else (default if default in values else values[0])
        a_start = pd.Timestamp(out["week"])
        a_end = min(a_start + pd.Timedelta(days=6), yesterday)
        b_start = a_start - pd.Timedelta(days=7)
        b_end = b_start + (a_end - a_start)
    elif mode == "month":
        values = [m["value"] for m in out["months"]]
        # The last complete month: yesterday's, if yesterday ended it.
        latest = yesterday.to_period("M")
        default = str(latest if yesterday == latest.end_time.normalize() else latest - 1)
        out["month"] = month if month in values else (default if default in values else values[0])
        a = pd.Period(out["month"], freq="M")
        a_start, a_end = a.start_time, min(a.end_time.normalize(), yesterday)
        b = a - 1
        b_start = b.start_time
        # A month still under way meets the same number of days of the one
        # before; complete months meet complete months.
        b_end = b.end_time.normalize() if a_end == a.end_time.normalize() else min(
            b_start + (a_end - a_start), b.end_time.normalize())
    else:
        custom = custom or {}
        days = [_parse_day(custom.get(k)) for k in ("a_start", "a_end", "b_start", "b_end")]
        if all(d is None for d in days):
            a_end = yesterday
            a_start = a_end - pd.Timedelta(days=CUSTOM_DAYS - 1)
            b_end = a_start - ONE_DAY
            b_start = b_end - pd.Timedelta(days=CUSTOM_DAYS - 1)
        elif any(d is None for d in days) or days[0] > days[1] or days[2] > days[3]:
            out["error"] = "Enter a start and an end date for both periods, each start on or before its end."
            return {**out, "a": None, "b": None, "kpis": None, "rows": [],
                    "custom": {k: custom.get(k, "") for k in ("a_start", "a_end", "b_start", "b_end")}}
        else:
            a_start, a_end, b_start, b_end = days
    a_partial = mode != "custom" and a_end == yesterday and (
        (mode == "week" and a_end < a_start + pd.Timedelta(days=6))
        or (mode == "month" and a_end < pd.Period(a_start, freq="M").end_time.normalize()))
    out["custom"] = {"a_start": f"{a_start:%Y-%m-%d}", "a_end": f"{a_end:%Y-%m-%d}",
                     "b_start": f"{b_start:%Y-%m-%d}", "b_end": f"{b_end:%Y-%m-%d}"}

    a_orders = of_kind[of_kind["day"].between(a_start, a_end)]
    b_orders = of_kind[of_kind["day"].between(b_start, b_end)]
    a_revenue, b_revenue = float(a_orders["total_value"].sum()), float(b_orders["total_value"].sum())
    return {
        **out,
        "a": _period(a_start, a_end, a_partial),
        "b": _period(b_start, b_end),
        "kpis": {
            "orders": _change(len(a_orders), len(b_orders)),
            "revenue": _change(a_revenue, b_revenue),
            "avg_value": _change(a_revenue / len(a_orders) if len(a_orders) else 0.0,
                                 b_revenue / len(b_orders) if len(b_orders) else 0.0),
        },
        "rows": _compare_rows(a_orders, b_orders, COMPARE_BY[by], None if by == "source" else COMPARE_TOP),
    }
