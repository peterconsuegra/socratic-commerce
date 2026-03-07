# app/services/wati_insights.py
import logging
import os
import pandas as pd

from .daily_sales import get_daily_sales_trend


def _compute_wati_kpis(
    orders_csv_path: str,
    tz: str = "America/Bogota",
) -> dict:
    """
    KPIs are based on utm_source == 'wati' and computed up to end of yesterday (Bogota).
    Returns:
      - mtd_sales
      - avg_per_day_mtd
      - last_7_days_sales
      - days_elapsed_mtd
    """
    if not os.path.exists(orders_csv_path):
        return {
            "mtd_sales": 0.0,
            "avg_per_day_mtd": 0.0,
            "last_7_days_sales": 0.0,
            "days_elapsed_mtd": 0,
        }

    df = pd.read_csv(orders_csv_path)

    required_cols = {"order_date", "total_value", "utm_source"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df = df.dropna(subset=["order_date"]).copy()
    df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

    df["utm_source"] = (
        df["utm_source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    df = df[df["utm_source"] == "wati"].copy()

    if df.empty:
        return {
            "mtd_sales": 0.0,
            "avg_per_day_mtd": 0.0,
            "last_7_days_sales": 0.0,
            "days_elapsed_mtd": 0,
        }

    # Treat order_date as local Bogota time (naive -> localize)
    df["order_date_local"] = df["order_date"].dt.tz_localize(tz)

    # End of yesterday (Bogota)
    today_bogota = pd.Timestamp.now(tz=tz).normalize()
    end_yesterday = today_bogota - pd.Timedelta(microseconds=1)

    df = df[df["order_date_local"] <= end_yesterday].copy()
    if df.empty:
        return {
            "mtd_sales": 0.0,
            "avg_per_day_mtd": 0.0,
            "last_7_days_sales": 0.0,
            "days_elapsed_mtd": 0,
        }

    start_month = today_bogota.replace(day=1)
    mtd_df = df[(df["order_date_local"] >= start_month) & (df["order_date_local"] <= end_yesterday)].copy()
    mtd_sales = float(mtd_df["total_value"].sum()) if not mtd_df.empty else 0.0

    yesterday_day = (today_bogota - pd.Timedelta(days=1)).normalize()
    days_elapsed = int((yesterday_day - start_month).days + 1) if yesterday_day >= start_month else 0
    avg_per_day_mtd = (mtd_sales / days_elapsed) if days_elapsed > 0 else 0.0

    start_last_7 = today_bogota - pd.Timedelta(days=7)
    last_7_df = df[(df["order_date_local"] >= start_last_7) & (df["order_date_local"] <= end_yesterday)].copy()
    last_7_sales = float(last_7_df["total_value"].sum()) if not last_7_df.empty else 0.0

    return {
        "mtd_sales": mtd_sales,
        "avg_per_day_mtd": avg_per_day_mtd,
        "last_7_days_sales": last_7_sales,
        "days_elapsed_mtd": days_elapsed,
    }


def get_wati_sales_trend(
    orders_csv_path: str = "data/daily_sales_orders.csv",
    forecast_periods: int = 14,
):
    """
    Returns (trend_rows, forecast_rows, kpis) for utm_source == 'wati'
    using the same daily_sales service logic.
    """
    logger = logging.getLogger(__name__)
    logger.info("Building Wati insights from %s", orders_csv_path)

    summary_rows, forecast_rows = get_daily_sales_trend(
        output_file="wati_sales_trend.csv",
        forecast_periods=forecast_periods,
        return_forecast=True,
        orders_csv_path=orders_csv_path,
        utm_source_filter="wati",
    )

    kpis = _compute_wati_kpis(orders_csv_path=orders_csv_path)

    return summary_rows, forecast_rows, kpis
