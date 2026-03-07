# app/services/top_cities.py
import pandas as pd

from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX


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


def get_top_cities_daily_trend_with_forecast(
    input_file: str,
    top_n: int = 10,
    forecast_periods: int = 14,
    utm_campaign_filter: str | None = None,
    utm_source_filter: str | None = None,
    product_filter: str | None = None,          # <-- NEW
):
    df = pd.read_csv(input_file)

    required = {"order_date", "city", "total_value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df = df.dropna(subset=["order_date"])

    df["city"] = df["city"].astype(str).str.strip()
    df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

    # Optional filter: utm_campaign == value
    if utm_campaign_filter:
        if "utm_campaign" not in df.columns:
            raise ValueError("Column 'utm_campaign' is required to filter by campaign.")
        df["utm_campaign"] = df["utm_campaign"].astype(str).str.strip().str.lower()
        df = df[df["utm_campaign"] == utm_campaign_filter.strip().lower()]

    # Optional filter: utm_source == value
    if utm_source_filter:
        if "utm_source" not in df.columns:
            raise ValueError("Column 'utm_source' is required to filter by source.")
        df["utm_source"] = df["utm_source"].astype(str).str.strip().str.lower()
        df = df[df["utm_source"] == utm_source_filter.strip().lower()]

    # Optional filter: product == value
    if product_filter:
        if "product" not in df.columns:
            raise ValueError("Column 'product' is required to filter by product.")
        df["product"] = df["product"].astype(str).str.strip()
        df = df[df["product"] == product_filter.strip()]

    # Normalize to day
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

    return top_cities, trend_rows, forecast_rows, city_totals_rows
