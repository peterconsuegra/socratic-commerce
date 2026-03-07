# app/services/top_cities_gender.py

import os
import re
import unicodedata
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX


def _slugify_city(city: str) -> str:
    s = str(city or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown_city"


def _clean_dim_value(value) -> str:
    v = "" if value is None else str(value).strip()
    if not v or v.lower() in {"nan", "none", "undefined", "null"}:
        return "Undefined"
    return v


def _normalize_gender(gender: str | None) -> str:
    g = str(gender or "").strip().lower()
    if not g:
        return "unknown"
    return g


def generate_city_csv_exports_top_cities_gender(
    input_file: str,
    cities: list[str],
    gender: str,
    output_dir: str = "data",
    prefix: str | None = None,
) -> dict:
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Input CSV not found: {input_file}")

    os.makedirs(output_dir, exist_ok=True)

    df = pd.read_csv(input_file)

    # Require these for correct export, including address_1
    required = {"city", "gender", "address_1"}
    missing = required - set(df.columns)

    # Allow common fallbacks for address column names, then re-check
    if "address_1" in missing:
        fallback_map = {
            "address1": "address_1",
            "shipping_address_1": "address_1",
            "shipping_address1": "address_1",
        }
        for src, dst in fallback_map.items():
            if src in df.columns and dst not in df.columns:
                df[dst] = df[src]
        missing = required - set(df.columns)

    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    gender_norm = _normalize_gender(gender)

    df["city"] = df["city"].astype(str).str.strip()
    df["gender"] = df["gender"].astype(str).str.strip().str.lower()
    df = df[df["gender"] == gender_norm]

    # Ensure address_1 is always a string column in the output
    df["address_1"] = df["address_1"].astype(str).fillna("").str.strip()

    if prefix is None:
        prefix = f"top_cities_{gender_norm}"

    out_map: dict[str, str] = {}
    for city in cities:
        city_str = str(city).strip()
        if not city_str:
            continue

        gdf = df[df["city"] == city_str].copy()
        if gdf.empty:
            continue

        slug = _slugify_city(city_str)
        filename = f"{prefix}_{slug}.csv"
        out_path = os.path.join(output_dir, filename)

        # This writes ALL columns (including address_1) to the per-city file
        gdf.to_csv(out_path, index=False, encoding="utf-8-sig")

        out_map[city_str] = filename

    return out_map


def _forecast_city_daily(series: pd.Series, periods: int = 14):
    series = series.astype(float)

    if len(series) < 10:
        last = float(series.iloc[-1]) if len(series) else 0.0
        return pd.Series([last] * periods)

    try:
        seasonal_periods = 7
        use_seasonal = len(series) >= (seasonal_periods * 2)

        model = ExponentialSmoothing(
            series,
            trend="add",
            seasonal="add" if use_seasonal else None,
            seasonal_periods=seasonal_periods if use_seasonal else None,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)
        fc = fit.forecast(periods)
        return fc.clip(lower=0.0)
    except Exception:
        pass

    try:
        seasonal_order = (1, 0, 1, 7) if len(series) >= 21 else (0, 0, 0, 0)
        model = SARIMAX(
            series,
            order=(1, 1, 1),
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fit = model.fit(disp=False)
        fc = fit.forecast(steps=periods)
        return fc.clip(lower=0.0)
    except Exception:
        last = float(series.iloc[-1]) if len(series) else 0.0
        return pd.Series([last] * periods)


def _pies_for_city_dimension(
    df: pd.DataFrame,
    cities: list[str],
    column_name: str,
    top_k: int = 8,
    other_label: str = "Other",
) -> dict:
    if column_name not in df.columns:
        return {c: {"labels": [], "values": []} for c in cities}

    tmp = df.copy()
    tmp[column_name] = tmp[column_name].apply(_clean_dim_value)

    out = {}
    for city in cities:
        city_df = tmp[tmp["city"] == city]
        if city_df.empty:
            out[city] = {"labels": [], "values": []}
            continue

        grp = (
            city_df.groupby(column_name, as_index=False)["total_value"]
            .sum()
            .sort_values("total_value", ascending=False)
        )

        labels = grp[column_name].astype(str).tolist()
        values = grp["total_value"].astype(float).tolist()

        if top_k and len(labels) > top_k:
            top_labels = labels[:top_k]
            top_values = values[:top_k]
            other_value = float(sum(values[top_k:]))

            labels = top_labels
            values = top_values
            if other_value > 0:
                labels.append(other_label)
                values.append(other_value)

        out[city] = {"labels": labels, "values": values}

    return out


def _hour_bucket_label(hour: int) -> str:
    start = (hour // 3) * 3
    end = start + 2
    return f"{start:02d}-{end:02d}"


def _pies_for_city_hour_buckets(df: pd.DataFrame, cities: list[str]) -> dict:
    # IMPORTANT: order_date is already Bogota time (GMT-5), so do not convert timezones here.
    if "order_date" not in df.columns:
        return {c: {"labels": [], "values": []} for c in cities}

    tmp = df.copy()
    tmp["order_date"] = pd.to_datetime(tmp["order_date"], errors="coerce")
    tmp = tmp.dropna(subset=["order_date"])
    if tmp.empty:
        return {c: {"labels": [], "values": []} for c in cities}

    tmp["hour"] = tmp["order_date"].dt.hour.astype(int)
    tmp["hour_bucket"] = tmp["hour"].apply(_hour_bucket_label)

    ordered_labels = [f"{h:02d}-{h+2:02d}" for h in range(0, 24, 3)]

    out = {}
    for city in cities:
        city_df = tmp[tmp["city"] == city]
        if city_df.empty:
            out[city] = {"labels": ordered_labels, "values": [0.0] * len(ordered_labels)}
            continue

        grp = city_df.groupby("hour_bucket", as_index=False)["total_value"].sum()
        map_values = {r["hour_bucket"]: float(r["total_value"]) for _, r in grp.iterrows()}

        values = [float(map_values.get(lbl, 0.0)) for lbl in ordered_labels]
        out[city] = {"labels": ordered_labels, "values": values}

    return out


def get_top_cities_gender_daily_trend_with_forecast(
    input_file: str,
    gender: str,
    top_n: int = 10,
    forecast_periods: int = 14,
    utm_campaign_filter: str | None = None,
    utm_source_filter: str | None = None,
    product_filter: str | None = None,
    campaign_top_k: int = 8,
    content_top_k: int = 8,
):
    df = pd.read_csv(input_file)

    required = {"order_date", "city", "total_value", "gender"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    gender_norm = _normalize_gender(gender)

    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df = df.dropna(subset=["order_date"])

    df["city"] = df["city"].astype(str).str.strip()
    df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

    df["gender"] = df["gender"].astype(str).str.strip().str.lower()
    df = df[df["gender"] == gender_norm]

    if utm_campaign_filter:
        if "utm_campaign" not in df.columns:
            raise ValueError("Column 'utm_campaign' is required to filter by campaign.")
        df["utm_campaign"] = df["utm_campaign"].astype(str).str.strip().str.lower()
        df = df[df["utm_campaign"] == utm_campaign_filter.strip().lower()]

    if utm_source_filter:
        if "utm_source" not in df.columns:
            raise ValueError("Column 'utm_source' is required to filter by source.")
        df["utm_source"] = df["utm_source"].astype(str).str.strip().str.lower()
        df = df[df["utm_source"] == utm_source_filter.strip().lower()]

    if product_filter:
        if "product" not in df.columns:
            raise ValueError("Column 'product' is required to filter by product.")
        df["product"] = df["product"].astype(str).str.strip()
        df = df[df["product"] == product_filter.strip()]

    df["Day"] = df["order_date"].dt.strftime("%Y-%m-%d")

    totals_all = (
        df.groupby("city", as_index=False)["total_value"]
        .sum()
        .sort_values("total_value", ascending=False)
    )
    city_totals_rows = [
        {"City": r["city"], "Total Sales": float(r["total_value"])}
        for _, r in totals_all.iterrows()
    ]

    top_cities = totals_all.head(top_n)["city"].tolist()

    city_campaign_pies = _pies_for_city_dimension(
        df=df,
        cities=top_cities,
        column_name="utm_campaign",
        top_k=campaign_top_k,
        other_label="Other campaigns",
    )

    city_content_pies = _pies_for_city_dimension(
        df=df,
        cities=top_cities,
        column_name="utm_content",
        top_k=content_top_k,
        other_label="Other content",
    )

    city_hour_pies = _pies_for_city_hour_buckets(df=df, cities=top_cities)

    trend = df[df["city"].isin(top_cities)]
    trend = (
        trend.groupby(["Day", "city"], as_index=False)["total_value"]
        .sum()
        .sort_values(["Day", "city"])
    )

    trend_rows = [
        {"Day": r["Day"], "City": r["city"], "Total Sales": float(r["total_value"])}
        for _, r in trend.iterrows()
    ]

    forecast_rows = []
    for city in top_cities:
        city_df = trend[trend["city"] == city].copy()
        if city_df.empty:
            continue

        city_df["Day"] = pd.to_datetime(city_df["Day"], errors="coerce")
        city_df = city_df.dropna(subset=["Day"]).sort_values("Day")

        s = city_df.set_index("Day")["total_value"]
        full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
        s = s.reindex(full_idx, fill_value=0.0)

        fc = _forecast_city_daily(s, periods=forecast_periods)
        fc_idx = pd.date_range(
            s.index.max() + pd.Timedelta(days=1),
            periods=forecast_periods,
            freq="D",
        )

        for d, v in zip(fc_idx, fc):
            forecast_rows.append(
                {"Day": d.strftime("%Y-%m-%d"), "City": city, "Forecast Sales": float(v)}
            )

    return (
        top_cities,
        trend_rows,
        forecast_rows,
        city_totals_rows,
        city_campaign_pies,
        city_content_pies,
        city_hour_pies,
    )
