# app/services/daily_sales.py
import os
import logging
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX


def _forecast_daily_series(series: pd.Series, periods: int = 14) -> pd.Series:
    series = series.astype(float)

    if len(series) < 10:
        last = float(series.iloc[-1]) if len(series) else 0.0
        return pd.Series([last] * periods)

    try:
        seasonal_periods = 7
        use_seasonal = len(series) >= (seasonal_periods * 3)

        model = ExponentialSmoothing(
            series,
            trend="add",
            seasonal="add" if use_seasonal else None,
            seasonal_periods=seasonal_periods if use_seasonal else None,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)
        return fit.forecast(periods).clip(lower=0.0)
    except Exception:
        pass

    try:
        seasonal_periods = 7
        use_seasonal = len(series) >= (seasonal_periods * 3)
        seasonal_order = (1, 0, 1, seasonal_periods) if use_seasonal else (0, 0, 0, 0)

        model = SARIMAX(
            series,
            order=(1, 1, 1),
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fit = model.fit(disp=False)
        return fit.forecast(steps=periods).clip(lower=0.0)
    except Exception:
        last = float(series.iloc[-1]) if len(series) else 0.0
        return pd.Series([last] * periods)


def get_daily_sales_trend(
    output_file: str = "daily_sales_trend.csv",
    forecast_periods: int = 14,
    return_forecast: bool = True,
    orders_csv_path: str = "data/all_orders.csv",
    utm_source_filter: str | None = None,
):
    logger = logging.getLogger(__name__)
    tz = "America/Bogota"

    logger.info("Building daily sales trend from %s", orders_csv_path)

    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    data = pd.read_csv(orders_csv_path)

    required_cols = {"order_date", "total_value"}
    missing = required_cols - set(data.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    data["order_date"] = pd.to_datetime(data["order_date"], errors="coerce")
    before = len(data)
    data = data.dropna(subset=["order_date"])
    after = len(data)
    if after < before:
        logger.warning("Dropped %s rows with invalid order_date", before - after)

    if getattr(data["order_date"].dt, "tz", None) is None:
        data["order_date_local"] = data["order_date"].dt.tz_localize(tz)
    else:
        data["order_date_local"] = data["order_date"].dt.tz_convert(tz)

    data["total_value"] = pd.to_numeric(data["total_value"], errors="coerce").fillna(0.0)

    now_bogota = pd.Timestamp.now(tz=tz).normalize()
    end_yesterday = now_bogota - pd.Timedelta(microseconds=1)
    data = data[data["order_date_local"] <= end_yesterday].copy()
    logger.info("Using orders up to %s (end of yesterday, Bogota)", end_yesterday)

    if utm_source_filter:
        if "utm_source" not in data.columns:
            raise ValueError("Orders file is missing utm_source column (required for utm_source_filter).")
        data["utm_source"] = (
            data["utm_source"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        data = data[data["utm_source"] == utm_source_filter.lower()].copy()
        logger.info("Applied utm_source filter: %s", utm_source_filter)

    output_dir = os.path.join(os.getcwd(), "data")
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, output_file)

    if data.empty:
        pd.DataFrame(columns=["Date", "Sales"]).to_csv(csv_path, index=False)
        return ([], []) if return_forecast else []

    data["day"] = data["order_date_local"].dt.floor("D")

    total_orders_per_day = (
        data.groupby("day")
        .size()
        .reset_index(name="Total Orders")
    )

    total_sales_per_day = (
        data.groupby("day")["total_value"]
        .sum()
        .reset_index(name="Total_Sales_Num")
    )

    summary = (
        total_orders_per_day
        .merge(total_sales_per_day, on="day", how="left")
        .sort_values("day")
    )

    summary["Total_Sales_Num"] = summary["Total_Sales_Num"].fillna(0.0)
    summary["Date"] = summary["day"].dt.strftime("%Y-%m-%d")
    summary["Total Sales"] = summary["Total_Sales_Num"].apply(lambda x: str(int(round(x))))

    summary_rows = summary[["Date", "Total Orders", "Total Sales"]].to_dict(orient="records")

    csv_summary = summary[["Date", "Total_Sales_Num"]].rename(columns={"Total_Sales_Num": "Sales"})
    csv_summary.to_csv(csv_path, index=False)

    forecast_rows = []
    if return_forecast:
        ts = csv_summary.copy()
        ts["Date_dt"] = pd.to_datetime(ts["Date"], errors="coerce")
        ts = ts.dropna(subset=["Date_dt"]).sort_values("Date_dt")

        if not ts.empty:
            s = ts.set_index("Date_dt")["Sales"].astype(float)

            full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
            s = s.reindex(full_idx, fill_value=0.0)

            fc = _forecast_daily_series(s, periods=forecast_periods)
            fc_idx = pd.date_range(s.index.max() + pd.Timedelta(days=1), periods=forecast_periods, freq="D")

            for d, v in zip(fc_idx, fc):
                forecast_rows.append({
                    "Date": d.strftime("%Y-%m-%d"),
                    "Projected Total Sales": float(v),
                })

    return (summary_rows, forecast_rows) if return_forecast else summary_rows


def get_daily_sales_trend_simple(*args, **kwargs):
    return get_daily_sales_trend(*args, **kwargs)
