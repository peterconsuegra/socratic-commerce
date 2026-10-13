# app/services/periods.py
"""
The time windows the JSON summaries answer for: the trailing presets (today,
last 7/30/90/180 days) and a custom range of Bogotá calendar days.

Order dates are Bogotá local time (tz-naive), and so are the windows. A window
runs from its first day at 00:00 to the day after its last (exclusive), and
never past now: a preset is [today - N days, now], a custom range
[since 00:00, until + 1 day 00:00). So a custom range given a preset's own
start_date and end_date selects exactly the preset's orders.
"""
import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

TIMEZONE = "America/Bogota"

# period key -> days subtracted from today for the window start: window is
# [today - N, now], today-inclusive (PYS "last N days" convention). "today" = 0.
PERIODS: dict[str, int] = {
    "today": 0,
    "last_7d": 7,
    "last_30d": 30,
    "last_90d": 90,
    "last_180d": 180,
}

LABELS = {
    "today": "Today",
    "last_7d": "Last 7 days",
    "last_30d": "Last 30 days",
    "last_90d": "Last 90 days",
    "last_180d": "Last 180 days",
}

ALL = "all"
CUSTOM = "custom"
VALID_PERIODS = [ALL, *PERIODS, CUSTOM]
# The longest custom range, both ends counted: the cap ROAS Link, the store's
# UTM plugin and Revive share.
MAX_CUSTOM_DAYS = 366
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class PeriodError(ValueError):
    """A period, since or until the summaries cannot answer; the message says why."""


@dataclass(frozen=True)
class Window:
    key: str
    label: str
    start: pd.Timestamp  # the first day, 00:00
    end: pd.Timestamp  # the day after the last, 00:00 (exclusive)

    def describe(self) -> dict:
        """The label and the first and last days, as every period of a response carries them."""
        return {
            "label": self.label,
            "start_date": f"{self.start:%Y-%m-%d}",
            "end_date": f"{self.end - pd.Timedelta(days=1):%Y-%m-%d}",
        }

    def orders(self, data: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
        """The orders placed in the window, up to now."""
        when = data["order_date"]
        return data[(when >= self.start) & (when < self.end) & (when <= now)]


def now_bogota_naive() -> pd.Timestamp:
    """Current Bogota time as a timezone-naive Timestamp (data is Bogota GMT-5)."""
    return pd.Timestamp.now(tz=TIMEZONE).tz_localize(None)


def _day(name: str, value: str) -> pd.Timestamp:
    if not _ISO_DAY.fullmatch(value):
        raise PeriodError(f"{name} must be a date as YYYY-MM-DD, got '{value}'.")
    try:
        return pd.Timestamp(date.fromisoformat(value))
    except ValueError:
        raise PeriodError(f"{name} '{value}' is not a real date.") from None


def resolve_windows(period: str | None = ALL, since: str | None = None, until: str | None = None,
                    now: pd.Timestamp | None = None) -> list[Window]:
    """
    The windows a request asks for: every preset for "all" (the default), one
    preset, or "custom" from since to until (YYYY-MM-DD, Bogotá days, both
    included). Raises PeriodError, its message meant for the caller, for an
    unknown period; since or until with any period but custom; custom without
    both dates; a malformed or impossible date; since after until; until after
    today in Bogotá; or a range longer than MAX_CUSTOM_DAYS.
    """
    period = (period or ALL).strip().lower()
    since, until = (since or "").strip(), (until or "").strip()
    if period not in VALID_PERIODS:
        raise PeriodError(f"Invalid period '{period}'. Valid values: {VALID_PERIODS}")

    today = (now if now is not None else now_bogota_naive()).normalize()
    tomorrow = today + pd.Timedelta(days=1)
    if period != CUSTOM:
        if since or until:
            raise PeriodError(f"since and until go with period=custom only, not period={period}.")
        keys = list(PERIODS) if period == ALL else [period]
        return [Window(key, LABELS[key], today - pd.Timedelta(days=PERIODS[key]), tomorrow) for key in keys]

    if not since or not until:
        raise PeriodError("period=custom needs both since and until, as YYYY-MM-DD.")
    first, last = _day("since", since), _day("until", until)
    if first > last:
        raise PeriodError(f"since ({since}) is after until ({until}).")
    if last > today:
        raise PeriodError(f"until ({until}) is after today in Bogotá ({today:%Y-%m-%d}).")
    days = (last - first).days + 1
    if days > MAX_CUSTOM_DAYS:
        raise PeriodError(f"The period spans {days} days; the most is {MAX_CUSTOM_DAYS}, both ends counted.")
    return [Window(CUSTOM, f"{first:%Y-%m-%d} – {last:%Y-%m-%d}", first, last + pd.Timedelta(days=1))]
