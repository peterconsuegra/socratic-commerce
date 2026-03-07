# app/services/daily_repurchases.py
import os
import logging
import pandas as pd

from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX


def _now_bogota_naive() -> pd.Timestamp:
    """
    Returns current Bogota time as timezone-naive Timestamp.
    Data is treated as Bogota time (GMT-5), so we keep everything naive.
    """
    return pd.Timestamp.now(tz="America/Bogota").tz_localize(None)


def _forecast_daily_series(series: pd.Series, periods: int = 30) -> pd.Series:
    """
    Forecast a daily series using Holt-Winters first, then SARIMAX, then fallback.
    """
    series = series.astype(float)

    if len(series) < 14:
        last = float(series.iloc[-1]) if len(series) else 0.0
        return pd.Series([last] * periods)

    try:
        seasonal_periods = 7
        use_seasonal = len(series) >= (seasonal_periods * 4)

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
        seasonal_periods = 7
        seasonal_order = (1, 0, 1, seasonal_periods) if len(series) >= (seasonal_periods * 6) else (0, 0, 0, 0)

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


def _normalize_utm_source(series: pd.Series) -> pd.Series:
    norm = (
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    norm = norm.replace({"nan": "", "none": ""})
    norm = norm.where(norm != "", "undefined")
    return norm


def get_daily_repurchases_trend(
    output_file: str = "daily_repurchases_trend.csv",
    forecast_periods: int = 30,
    return_forecast: bool = True,
    orders_csv_path: str = "data/all_orders.csv",
    start_date: str | None = None,
    end_date: str | None = None,
    utm_source_filter: str | None = None,
):
    """
    Builds daily repurchases summary and (optionally) produces a daily forecast.

    Repurchase classification is computed from ALL orders up to end of yesterday (Bogota),
    then the date window is applied for the output rows only.

    Selector window (optional):
      If start_date and end_date are provided (DD/MM/YYYY), output is filtered to that window.

    Cutoff:
      Always excludes today's partial day by cutting to end of yesterday (Bogota time).

    Optional filter:
      If utm_source_filter is provided, only repurchase orders with that utm_source are included
      in the repurchase aggregation, but repurchase classification still uses ALL orders.

    Returns:
      - if return_forecast is False: summary_rows
      - if return_forecast is True: (summary_rows, forecast_rows)

    Also writes a CSV to /data/<output_file> with columns: Day, Sales (repurchase totals)
    """
    logger = logging.getLogger(__name__)
    logger.info("Building daily repurchases trend from %s", orders_csv_path)

    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    data = pd.read_csv(orders_csv_path)

    required_cols = {"email", "order_date", "total_value"}
    missing = required_cols - set(data.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    # Types
    data["order_date"] = pd.to_datetime(data["order_date"], errors="coerce")
    before = len(data)
    data = data.dropna(subset=["order_date", "email"]).copy()
    after = len(data)
    if after < before:
        logger.warning("Dropped %s rows with invalid order_date or email", before - after)

    data["total_value"] = pd.to_numeric(data["total_value"], errors="coerce").fillna(0.0)

    # Cut to end of yesterday in Bogota (avoid partial day)
    today_bogota = _now_bogota_naive().normalize()
    end_yesterday = today_bogota - pd.Timedelta(microseconds=1)
    data = data[data["order_date"] <= end_yesterday].copy()
    logger.info("Using orders up to %s (end of yesterday, Bogota)", end_yesterday)

    output_dir = os.path.join(os.getcwd(), "data")
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, output_file)

    if data.empty:
        pd.DataFrame(columns=["Day", "Sales"]).to_csv(csv_path, index=False)
        return ([], []) if return_forecast else []

    # Repurchase classification computed from ALL orders (up to cutoff)
    email_counts = data["email"].value_counts(dropna=True)
    repeat_emails = set(email_counts[email_counts > 1].index)

    first_order_dt = data.groupby("email")["order_date"].min()
    data = data.join(first_order_dt, on="email", rsuffix="_first")

    data["is_repurchase"] = (data["email"].isin(repeat_emails)) & (data["order_date"] > data["order_date_first"])

    # Apply selector date window AFTER classification (output-only filter)
    if start_date and end_date:
        try:
            start_dt = pd.to_datetime(start_date, format="%d/%m/%Y", errors="raise")
            end_dt = (
                pd.to_datetime(end_date, format="%d/%m/%Y", errors="raise")
                + pd.Timedelta(days=1)
                - pd.Timedelta(microseconds=1)
            )
            data = data[(data["order_date"] >= start_dt) & (data["order_date"] <= end_dt)].copy()
            logger.info("Applied output date filter: %s to %s", start_dt, end_dt)
        except Exception:
            logger.warning(
                "Invalid custom date range provided: start_date=%s end_date=%s",
                start_date,
                end_date,
            )

    if data.empty:
        pd.DataFrame(columns=["Day", "Sales"]).to_csv(csv_path, index=False)
        return ([], []) if return_forecast else []

    # Day bucket
    data["day"] = data["order_date"].dt.date

    # Daily totals (all orders within the output window)
    total_orders_per_day = data.groupby("day").size().reset_index(name="Total Orders")
    total_sales_per_day = data.groupby("day")["total_value"].sum().reset_index(name="Total_Sales_Num")

    # Daily repurchases within the output window
    repurchase_data = data[data["is_repurchase"]].copy()

    # Optional utm_source filter applies to repurchase rows only
    if utm_source_filter:
        if "utm_source" not in repurchase_data.columns:
            raise ValueError("utm_source_filter was provided but column 'utm_source' does not exist in orders CSV.")

        repurchase_data["_utm_source_norm"] = _normalize_utm_source(repurchase_data["utm_source"])

        wanted = utm_source_filter.strip().lower()
        if wanted in {"", "nan", "none"}:
            wanted = "undefined"

        repurchase_data = repurchase_data[repurchase_data["_utm_source_norm"] == wanted].copy()

    repurchase_orders_per_day = repurchase_data.groupby("day").size().reset_index(name="Repurchases")
    repurchase_value_per_day = repurchase_data.groupby("day")["total_value"].sum().reset_index(name="Repurchase_Total_Value")

    # Combine summary
    summary = (
        total_orders_per_day
        .merge(repurchase_orders_per_day, on="day", how="left")
        .merge(total_sales_per_day, on="day", how="left")
        .merge(repurchase_value_per_day, on="day", how="left")
        .sort_values("day")
    )

    summary["Repurchases"] = summary["Repurchases"].fillna(0).astype(int)
    summary["Repurchase_Total_Value"] = summary["Repurchase_Total_Value"].fillna(0.0)
    summary["Total_Sales_Num"] = summary["Total_Sales_Num"].fillna(0.0)

    summary["Repurchase Sales Percentage (%)"] = summary.apply(
        lambda row: (row["Repurchase_Total_Value"] / row["Total_Sales_Num"]) * 100 if row["Total_Sales_Num"] > 0 else 0.0,
        axis=1,
    ).map(lambda x: f"{x:.2f}%")

    summary["Day"] = pd.to_datetime(summary["day"]).dt.strftime("%Y-%m-%d")

    summary["Total Sales"] = summary["Total_Sales_Num"].apply(lambda x: str(int(round(x))))
    summary["Repurchase Total Value"] = summary["Repurchase_Total_Value"].apply(lambda x: str(int(round(x))))

    summary_rows = summary.to_dict(orient="records")

    # Save CSV (Day, Sales)
    csv_summary = summary[["Day", "Repurchase Total Value"]].rename(columns={"Repurchase Total Value": "Sales"})
    csv_summary.to_csv(csv_path, index=False)
    logger.info("Daily repurchases trend CSV saved at %s", csv_path)

    # Forecast based on the repurchase series we just produced (windowed)
    forecast_rows = []
    if return_forecast:
        ts = csv_summary.copy()
        ts["Day_dt"] = pd.to_datetime(ts["Day"], errors="coerce")
        ts["Sales_num"] = pd.to_numeric(ts["Sales"], errors="coerce").fillna(0.0)
        ts = ts.dropna(subset=["Day_dt"]).sort_values("Day_dt")

        if not ts.empty:
            s = ts.set_index("Day_dt")["Sales_num"]

            full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
            s = s.reindex(full_idx, fill_value=0.0)

            fc = _forecast_daily_series(s, periods=forecast_periods)
            fc_idx = pd.date_range(s.index.max() + pd.Timedelta(days=1), periods=forecast_periods, freq="D")

            for d, v in zip(fc_idx, fc):
                forecast_rows.append({"Day": d.strftime("%Y-%m-%d"), "Projected Repurchase Sales": float(v)})

    return (summary_rows, forecast_rows) if return_forecast else summary_rows
