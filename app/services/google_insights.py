# app/services/google_insights.py
import os
import re
import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing


def _forecast_daily_series(series: pd.Series, periods: int = 14) -> pd.Series:
    s = pd.Series(series).astype(float).copy()
    s = s.replace([np.inf, -np.inf], np.nan).dropna()

    if periods <= 0:
        return pd.Series([], dtype=float)

    if len(s) == 0:
        return pd.Series([0.0] * periods, dtype=float)

    s = s.clip(lower=0.0)
    last = float(s.iloc[-1]) if len(s) else 0.0
    last = 0.0 if np.isnan(last) else max(last, 0.0)

    nonzero_days = int((s > 0).sum())

    if nonzero_days < 7:
        decay = 0.90
        return pd.Series([max(last * (decay ** i), 0.0) for i in range(1, periods + 1)], dtype=float)

    if nonzero_days < 14:
        window = min(10, len(s))
        recent = s.iloc[-window:].astype(float)

        recent_nz = recent[recent > 0]
        if len(recent_nz) >= 3:
            level_med = float(recent_nz.median())
            level_mean = float(recent_nz.mean())
            recent_max = float(recent_nz.max())
        else:
            level_med = float(recent.median())
            level_mean = float(recent.mean())
            recent_max = float(recent.max())

        diffs = recent.diff().dropna()
        slope = float(diffs.median()) if len(diffs) else 0.0
        if np.isnan(slope):
            slope = 0.0

        base_level = max(level_med, level_mean, 0.0)

        abs_cap = max(1000.0, base_level * 0.08)
        slope = max(min(slope, abs_cap), -abs_cap)

        spike_factor = 1.0
        if base_level > 0 and last > 1.7 * base_level:
            spike_factor = 0.25
        slope *= spike_factor

        ceiling = max(recent_max * 1.15, base_level * 1.25, last * 1.10)
        floor = 0.0

        forecasts = []
        for i in range(1, periods + 1):
            val = last + slope * i
            val *= (0.90 ** i)
            val = min(val, ceiling)
            val = max(val, floor)
            forecasts.append(float(val))

        return pd.Series(forecasts, dtype=float)

    try:
        model = ExponentialSmoothing(
            s,
            trend="add",
            damped_trend=True,
            seasonal="add",
            seasonal_periods=7,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)
        fc = fit.forecast(periods)
        return pd.Series(fc).astype(float).clip(lower=0.0)
    except Exception:
        return pd.Series([last] * periods, dtype=float)


def _normalize_str(val: str, fallback: str = "unknown") -> str:
    s = "" if val is None else str(val)
    s = s.strip()
    if not s or s.lower() in {"nan", "none"}:
        return fallback
    return s


def _normalize_utm_source(val: str) -> str:
    return _normalize_str(val, fallback="").lower()


def _normalize_campaign_key(val: str) -> str:
    s = _normalize_str(val, fallback="unknown").lower()
    s = s.replace("+", "")
    s = re.sub(r"[\s\-_]+", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s or "unknown"


def _normalize_gender(val: str) -> str:
    s = _normalize_str(val, fallback="other").lower()
    if s in {"f", "female", "mujer", "femenino"}:
        return "female"
    if s in {"m", "male", "hombre", "masculino"}:
        return "male"
    return "other"


def _normalize_city(val: str) -> str:
    s = _normalize_str(val, fallback="Unknown").strip()
    if not s or s.lower() in {"nan", "none", "n/a"}:
        return "Unknown"
    s = re.sub(r"\s+", " ", s)
    return s.title()


def _normalize_content(val: str) -> str:
    s = _normalize_str(val, fallback="Unknown").strip()
    if not s or s.lower() in {"nan", "none", "n/a", "undefined"}:
        return "Unknown"
    s = re.sub(r"\s+", " ", s)
    return s


def _apply_time_cutoff_rules(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    dfx = df.copy()
    dfx["order_date"] = pd.to_datetime(dfx["order_date"], errors="coerce")
    dfx = dfx.dropna(subset=["order_date"]).copy()

    dfx["order_date_local"] = dfx["order_date"].dt.tz_localize(tz)
    dfx["total_value"] = pd.to_numeric(dfx["total_value"], errors="coerce").fillna(0.0)

    now_local = pd.Timestamp.now(tz=tz).normalize()
    end_yesterday = now_local - pd.Timedelta(microseconds=1)
    dfx = dfx[dfx["order_date_local"] <= end_yesterday].copy()
    return dfx


def _build_daily_series(df: pd.DataFrame, tz: str, forecast_periods: int) -> tuple[list[dict], list[dict]]:
    if df.empty:
        return [], []

    dfx = _apply_time_cutoff_rules(df, tz=tz)
    if dfx.empty:
        return [], []

    dfx["day"] = dfx["order_date_local"].dt.floor("D")

    daily = (
        dfx.groupby("day")["total_value"]
        .sum()
        .reset_index(name="Sales")
        .sort_values("day")
    )

    daily["Date"] = daily["day"].dt.strftime("%Y-%m-%d")
    daily["Total Sales"] = daily["Sales"].apply(lambda x: str(int(round(float(x)))))

    trend_rows = daily[["Date", "Total Sales"]].to_dict(orient="records")

    forecast_rows: list[dict] = []
    if forecast_periods <= 0:
        return trend_rows, forecast_rows

    ts = daily[["Date", "Sales"]].copy()
    ts["Date_dt"] = pd.to_datetime(ts["Date"], errors="coerce")
    ts = ts.dropna(subset=["Date_dt"]).sort_values("Date_dt")

    if not ts.empty:
        s = ts.set_index("Date_dt")["Sales"].astype(float)

        full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
        s = s.reindex(full_idx)
        s = s.interpolate(limit=2).fillna(0.0)

        fc = _forecast_daily_series(s, periods=forecast_periods)
        fc_idx = pd.date_range(s.index.max() + pd.Timedelta(days=1), periods=forecast_periods, freq="D")

        for d, v in zip(fc_idx, fc):
            forecast_rows.append({"Date": d.strftime("%Y-%m-%d"), "Projected Total Sales": float(v)})

    return trend_rows, forecast_rows


def _count_nonzero_sales_days(df: pd.DataFrame, tz: str) -> int:
    if df.empty:
        return 0

    dfx = _apply_time_cutoff_rules(df, tz=tz)
    if dfx.empty:
        return 0

    dfx["day"] = dfx["order_date_local"].dt.floor("D")
    daily = dfx.groupby("day")["total_value"].sum()
    return int((daily > 0).sum())


def _build_hour_bucket_pie(df: pd.DataFrame, tz: str) -> tuple[list[str], list[float]]:
    if df.empty:
        return [], []

    dfx = _apply_time_cutoff_rules(df, tz=tz)
    if dfx.empty:
        return [], []

    hours = dfx["order_date_local"].dt.hour.astype(int)
    dfx["hour_bucket_start"] = (hours // 3) * 3

    grouped = dfx.groupby("hour_bucket_start")["total_value"].sum().sort_index()

    labels: list[str] = []
    values: list[float] = []
    for start_hour, val in grouped.items():
        vf = float(val)
        if vf <= 0:
            continue
        end_hour = int(start_hour) + 2
        labels.append(f"{int(start_hour):02d}:00-{end_hour:02d}:59")
        values.append(vf)

    return labels, values


def _build_gender_pie(df: pd.DataFrame, tz: str) -> tuple[list[str], list[float]]:
    if df.empty:
        return [], []

    dfx = _apply_time_cutoff_rules(df, tz=tz)
    if dfx.empty:
        return [], []

    if "gender" not in dfx.columns:
        return [], []

    dfx["gender_norm"] = dfx["gender"].apply(_normalize_gender)
    grouped = dfx.groupby("gender_norm")["total_value"].sum().sort_values(ascending=False)

    labels: list[str] = []
    values: list[float] = []
    for g, v in grouped.items():
        vf = float(v)
        if vf <= 0:
            continue
        if g == "female":
            labels.append("Female")
        elif g == "male":
            labels.append("Male")
        else:
            labels.append("Other/Unknown")
        values.append(vf)

    return labels, values


def _build_city_pie(df: pd.DataFrame, tz: str, top_n: int = 12) -> tuple[list[str], list[float]]:
    if df.empty:
        return [], []

    dfx = _apply_time_cutoff_rules(df, tz=tz)
    if dfx.empty:
        return [], []

    if "city" not in dfx.columns:
        return [], []

    dfx["city_norm"] = dfx["city"].apply(_normalize_city)

    grouped = dfx.groupby("city_norm")["total_value"].sum().sort_values(ascending=False)
    grouped = grouped[grouped > 0]
    if grouped.empty:
        return [], []

    if top_n is not None and int(top_n) > 0 and len(grouped) > int(top_n):
        top = grouped.iloc[: int(top_n)]
        rest = grouped.iloc[int(top_n) :]
        other_sum = float(rest.sum()) if len(rest) else 0.0

        labels = [str(x) for x in top.index.tolist()]
        values = [float(x) for x in top.values.tolist()]

        if other_sum > 0:
            labels.append("Other")
            values.append(other_sum)

        return labels, values

    return [str(x) for x in grouped.index.tolist()], [float(x) for x in grouped.values.tolist()]


def _build_content_pie(df: pd.DataFrame, tz: str, top_n: int = 12) -> tuple[list[str], list[float]]:
    """
    Pie distribution by utm_content based on SALES (sum total_value).
    Keeps top_n contents, aggregates the rest into "Other".
    """
    if df.empty:
        return [], []

    dfx = _apply_time_cutoff_rules(df, tz=tz)
    if dfx.empty:
        return [], []

    if "utm_content" not in dfx.columns:
        return [], []

    dfx["content_norm"] = dfx["utm_content"].apply(_normalize_content)

    grouped = dfx.groupby("content_norm")["total_value"].sum().sort_values(ascending=False)
    grouped = grouped[grouped > 0]
    if grouped.empty:
        return [], []

    if top_n is not None and int(top_n) > 0 and len(grouped) > int(top_n):
        top = grouped.iloc[: int(top_n)]
        rest = grouped.iloc[int(top_n) :]
        other_sum = float(rest.sum()) if len(rest) else 0.0

        labels = [str(x) for x in top.index.tolist()]
        values = [float(x) for x in top.values.tolist()]

        if other_sum > 0:
            labels.append("Other")
            values.append(other_sum)

        return labels, values

    return [str(x) for x in grouped.index.tolist()], [float(x) for x in grouped.values.tolist()]


def get_google_insights_daily(
    orders_csv_path: str = "data/google_sales_orders.csv",
    forecast_periods: int = 30,
    min_share_percent: float = 2.0,
):
    tz = "America/Bogota"

    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    df = pd.read_csv(orders_csv_path)

    required_cols = {"order_date", "total_value", "utm_source", "utm_campaign"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    df["utm_source_norm"] = df["utm_source"].apply(_normalize_utm_source)
    df = df[df["utm_source_norm"] == "google"].copy()

    df["utm_campaign_key"] = df["utm_campaign"].apply(_normalize_campaign_key)
    df["utm_campaign_display"] = df["utm_campaign"].apply(lambda x: _normalize_str(x, fallback="unknown"))

    total_daily_trend, forecast_data = _build_daily_series(df=df.copy(), tz=tz, forecast_periods=forecast_periods)

    if not total_daily_trend:
        return {
            "total_daily_trend": [],
            "forecast_data": [],
            "pie_labels": [],
            "pie_keys": [],
            "pie_values": [],
            "campaign_charts": [],
            "other_campaigns_labels": [],
            "other_campaigns_pct": 0.0,
            "total_hour_pie_labels": [],
            "total_hour_pie_values": [],
            "total_gender_pie_labels": [],
            "total_gender_pie_values": [],
            "total_city_pie_labels": [],
            "total_city_pie_values": [],
            "total_content_pie_labels": [],
            "total_content_pie_values": [],
        }

    total_hour_pie_labels, total_hour_pie_values = _build_hour_bucket_pie(df=df.copy(), tz=tz)
    total_gender_pie_labels, total_gender_pie_values = _build_gender_pie(df=df.copy(), tz=tz)
    total_city_pie_labels, total_city_pie_values = _build_city_pie(df=df.copy(), tz=tz, top_n=12)

    # NEW
    total_content_pie_labels, total_content_pie_values = _build_content_pie(df=df.copy(), tz=tz, top_n=12)

    df2 = _apply_time_cutoff_rules(df.copy(), tz=tz)
    if df2.empty:
        return {
            "total_daily_trend": total_daily_trend,
            "forecast_data": forecast_data,
            "pie_labels": [],
            "pie_keys": [],
            "pie_values": [],
            "campaign_charts": [],
            "other_campaigns_labels": [],
            "other_campaigns_pct": 0.0,
            "total_hour_pie_labels": total_hour_pie_labels,
            "total_hour_pie_values": total_hour_pie_values,
            "total_gender_pie_labels": total_gender_pie_labels,
            "total_gender_pie_values": total_gender_pie_values,
            "total_city_pie_labels": total_city_pie_labels,
            "total_city_pie_values": total_city_pie_values,
            "total_content_pie_labels": total_content_pie_labels,
            "total_content_pie_values": total_content_pie_values,
        }

    display_map = (
        df2.groupby("utm_campaign_key")["utm_campaign_display"]
        .agg(lambda s: s.value_counts().index[0] if len(s) else "unknown")
        .to_dict()
    )

    grouped_value = df2.groupby("utm_campaign_key")["total_value"].sum().sort_values(ascending=False)

    pie_keys = [str(k) for k, v in grouped_value.items() if float(v) > 0]
    pie_labels = [display_map.get(k, str(k)) for k in pie_keys]
    pie_values = [float(grouped_value.loc[k]) for k in pie_keys]
    total_sales_value = float(sum(pie_values)) if pie_values else 0.0

    included = []
    excluded = []

    if total_sales_value > 0:
        for camp_key, val in grouped_value.items():
            val = float(val)
            if val <= 0:
                continue
            pct = (val / total_sales_value) * 100.0
            item = {"key": str(camp_key), "label": display_map.get(camp_key, str(camp_key)), "pct": pct, "value": val}
            if pct >= float(min_share_percent):
                included.append(item)
            else:
                excluded.append(item)

    included.sort(key=lambda x: x["pct"], reverse=True)
    excluded.sort(key=lambda x: x["pct"], reverse=True)

    other_campaigns_labels = [x["label"] for x in excluded]
    other_campaigns_pct = float(sum(x["pct"] for x in excluded)) if excluded else 0.0

    SHORT_CAMPAIGN_NONZERO_DAYS_THRESHOLD = 12
    SHORT_CAMPAIGN_FORECAST_DAYS = 14

    campaign_charts: list[dict] = []
    for item in included:
        camp_key = item["key"]
        camp_label = item["label"]
        pct = float(item["pct"])

        subset = df[df["utm_campaign_key"] == camp_key].copy()

        nonzero_days = _count_nonzero_sales_days(subset, tz=tz)

        campaign_forecast_periods = forecast_periods
        if nonzero_days <= SHORT_CAMPAIGN_NONZERO_DAYS_THRESHOLD:
            campaign_forecast_periods = SHORT_CAMPAIGN_FORECAST_DAYS

        trend_rows, fc_rows = _build_daily_series(df=subset, tz=tz, forecast_periods=campaign_forecast_periods)
        if not trend_rows:
            continue

        hour_pie_labels, hour_pie_values = _build_hour_bucket_pie(df=subset, tz=tz)
        gender_pie_labels, gender_pie_values = _build_gender_pie(df=subset, tz=tz)
        city_pie_labels, city_pie_values = _build_city_pie(df=subset, tz=tz, top_n=12)

        # NEW
        content_pie_labels, content_pie_values = _build_content_pie(df=subset, tz=tz, top_n=12)

        safe = "".join(c if c.isalnum() else "_" for c in str(camp_key).lower())

        label_suffix = ""
        if campaign_forecast_periods != forecast_periods:
            label_suffix = f" (forecast {campaign_forecast_periods}d)"

        campaign_charts.append({
            "key": camp_key,
            "label": f"{camp_label} ({pct:.1f}%){label_suffix}",
            "canvas_id": f"googleCampaignChart_{safe}",
            "trend": trend_rows,
            "forecast": fc_rows,
            "pct": pct,
            "hour_pie_canvas_id": f"googleCampaignHourPie_{safe}",
            "hour_pie_labels": hour_pie_labels,
            "hour_pie_values": hour_pie_values,
            "gender_pie_canvas_id": f"googleCampaignGenderPie_{safe}",
            "gender_pie_labels": gender_pie_labels,
            "gender_pie_values": gender_pie_values,
            "city_pie_canvas_id": f"googleCampaignCityPie_{safe}",
            "city_pie_labels": city_pie_labels,
            "city_pie_values": city_pie_values,
            # NEW
            "content_pie_canvas_id": f"googleCampaignContentPie_{safe}",
            "content_pie_labels": content_pie_labels,
            "content_pie_values": content_pie_values,
        })

    return {
        "total_daily_trend": total_daily_trend,
        "forecast_data": forecast_data,
        "pie_labels": pie_labels,
        "pie_keys": pie_keys,
        "pie_values": pie_values,
        "campaign_charts": campaign_charts,
        "other_campaigns_labels": other_campaigns_labels,
        "other_campaigns_pct": other_campaigns_pct,
        "total_hour_pie_labels": total_hour_pie_labels,
        "total_hour_pie_values": total_hour_pie_values,
        "total_gender_pie_labels": total_gender_pie_labels,
        "total_gender_pie_values": total_gender_pie_values,
        "total_city_pie_labels": total_city_pie_labels,
        "total_city_pie_values": total_city_pie_values,
        # NEW
        "total_content_pie_labels": total_content_pie_labels,
        "total_content_pie_values": total_content_pie_values,
    }
