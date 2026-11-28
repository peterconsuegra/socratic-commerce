# app/services/facebook_stats.py
"""
/facebook_stats: the daily sales of the orders with utm_source facebook, all
together and per utm_campaign, over the last N complete days (Bogotá time),
and a warning for each campaign whose sales have fallen day after day for
more than DECLINE_WARNING_DAYS days in a row, up to the last complete day.

Only complete days count: the window ends yesterday, because a day still in
progress nearly always sells less than the day before and would flag every
campaign. A day without sales counts as 0, so a campaign that sells nothing
one day went down that day; 0 after 0 is not a decrease, so the run stops
there.

utm_source is trimmed and lowercased ("facebook", " Facebook " are one
source; "fb", "ig" and "facebook-sitelink" are other sources, as on /stats).
Campaign names are cleaned as /api/utm_campaign_summary cleans them (trimmed,
blank = "undefined"), so they read as in ROAS Link.

One campaign × day table and vectorized runs; Python only loops to shape the
response (one row per campaign that sold in the window).
"""
import pandas as pd

from app.services.periods import orders_cutoff
from app.services.utm_campaign_summary import _normalize_campaign

SOURCE = "facebook"
WINDOW_CHOICES = (14, 30, 60, 90)
DEFAULT_WINDOW_DAYS = 30
# Warn when sales fell on more than this many days in a row (4 or more).
DECLINE_WARNING_DAYS = 3
# The total chart's smoothing line.
AVERAGE_DAYS = 7


def facebook_orders(orders: pd.DataFrame) -> pd.DataFrame:
    """The load_orders rows whose utm_source is facebook (trimmed, any case)."""
    return orders[orders["utm_source"].str.strip().str.lower().eq(SOURCE)]


def decline_streaks(daily: pd.DataFrame) -> pd.Series:
    """
    daily: one row per series, one column per consecutive day, oldest first.
    For each row, on how many of its last days in a row sales were lower than
    the day before: 0 when the last day did not go down.
    """
    if daily.shape[1] < 2:
        return pd.Series(0, index=daily.index, dtype=int)
    down = daily.diff(axis=1).lt(0).astype(int)
    return down.iloc[:, ::-1].cumprod(axis=1).sum(axis=1).astype(int)


def _series(name: str, sales: pd.Series, orders: pd.Series, streak: int, days: list[str], window_sales: float) -> dict:
    """One chart's data: its daily figures, totals and the run of falling days, if any."""
    values = [round(float(v), 2) for v in sales.to_numpy()]
    total = float(sum(values))
    declining = streak > DECLINE_WARNING_DAYS
    last = len(values) - 1
    start = last - streak  # the day before the first fall: where the run starts from
    return {
        "name": name,
        "sales": values,
        "orders": [int(v) for v in orders.to_numpy()],
        "total_sales": total,
        "total_orders": int(orders.sum()),
        "share": total / window_sales * 100 if window_sales else 0.0,
        "decline_days": int(streak),
        "declining": declining,
        "decline_start_index": start if streak else None,
        "decline_from": {"date": days[start], "sales": values[start]} if streak else None,
        "decline_to": {"date": days[last], "sales": values[last]} if streak else None,
    }


def build_facebook_stats(orders: pd.DataFrame, days: int = DEFAULT_WINDOW_DAYS,
                         now: pd.Timestamp | None = None) -> dict:
    """
    orders: load_orders rows. days: the window's length in complete days,
    ending yesterday in Bogotá. Returns:

      days, start_date, end_date: the window's days (YYYY-MM-DD);
      total: the facebook orders' daily sales and orders, their AVERAGE_DAYS
             average (average), totals and run of falling days;
      campaigns: the same per utm_campaign with sales or orders in the
                 window, by sales; share is % of the window's facebook sales;
      warnings: the campaigns that fell more than DECLINE_WARNING_DAYS days
                in a row, longest run first;
      warning_days: DECLINE_WARNING_DAYS.
    """
    last_day = orders_cutoff(False, now).normalize()
    first_day = last_day - pd.Timedelta(days=days - 1)
    # The average starts AVERAGE_DAYS - 1 days early, so the window's first day has one too.
    lead_day = first_day - pd.Timedelta(days=AVERAGE_DAYS - 1)
    end = last_day + pd.Timedelta(days=1)
    window_index = pd.date_range(first_day, last_day, freq="D")
    lead_index = pd.date_range(lead_day, last_day, freq="D")
    day_labels = [f"{d:%Y-%m-%d}" for d in window_index]

    fb = facebook_orders(orders)
    fb = fb[(fb["order_date"] >= lead_day) & (fb["order_date"] < end)]
    fb = fb.assign(_day=fb["order_date"].dt.normalize(), _campaign=_normalize_campaign(fb["utm_campaign"]),
                   _value=fb["total_value"].astype(float))

    by_day = fb.groupby("_day")["_value"].agg(["sum", "size"]).reindex(lead_index, fill_value=0)
    average = by_day["sum"].rolling(AVERAGE_DAYS, min_periods=1).mean().reindex(window_index)
    total_sales = by_day["sum"].reindex(window_index)
    total_orders = by_day["size"].reindex(window_index)

    in_window = fb[fb["order_date"] >= first_day]
    grouped = in_window.groupby(["_campaign", "_day"])["_value"].agg(["sum", "size"])
    sales = grouped["sum"].unstack(fill_value=0.0).reindex(columns=window_index, fill_value=0.0)
    counts = grouped["size"].unstack(fill_value=0).reindex(columns=window_index, fill_value=0)
    ranking = (
        pd.DataFrame({"sales": sales.sum(axis=1), "orders": counts.sum(axis=1)})
        .rename_axis("campaign").reset_index()
        .sort_values(["sales", "orders", "campaign"], ascending=[False, False, True])
    )

    streaks = decline_streaks(sales)
    total_streak = int(decline_streaks(total_sales.to_frame().T).iloc[0])
    window_sales = float(total_sales.sum())

    total = _series(SOURCE, total_sales, total_orders, total_streak, day_labels, window_sales)
    total["average"] = [round(float(v), 2) for v in average.to_numpy()]

    campaigns = []
    for i, name in enumerate(ranking["campaign"]):
        row = _series(name, sales.loc[name], counts.loc[name], int(streaks.loc[name]), day_labels, window_sales)
        row["anchor"] = f"campaign-{i + 1}"
        campaigns.append(row)

    warnings = sorted((c for c in campaigns if c["declining"]),
                      key=lambda c: (-c["decline_days"], -c["total_sales"]))
    return {
        "days": day_labels,
        "window_days": days,
        "start_date": day_labels[0],
        "end_date": day_labels[-1],
        "total": total,
        "campaigns": campaigns,
        "warnings": warnings,
        "warning_days": DECLINE_WARNING_DAYS,
    }
