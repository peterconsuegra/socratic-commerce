# app/services/monthly_sales.py
import os
import logging
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX


def _forecast_monthly_series(series: pd.Series, periods: int = 6) -> pd.Series:
    series = series.astype(float)

    if len(series) < 6:
        last = float(series.iloc[-1]) if len(series) else 0.0
        return pd.Series([last] * periods)

    last = float(series.iloc[-1]) if len(series) else 0.0

    def _fallback() -> pd.Series:
        # Prefer recent average to reduce overreaction to last-point noise
        tail = series.tail(min(6, len(series)))
        base = float(tail.mean()) if len(tail) else last
        if base < 0:
            base = 0.0
        return pd.Series([base] * periods)

    def _looks_broken(fc: pd.Series) -> bool:
        if fc is None or len(fc) == 0:
            return True

        f = pd.to_numeric(fc, errors="coerce").fillna(0.0).astype(float)

        if last <= 0:
            return False

        # 1) Previous check: if everything is tiny vs last, it is broken
        max_fc = float(f.max())
        if max_fc < (0.10 * last):
            return True

        # 2) New check: if the first steps collapse to near-zero vs last, treat as broken
        head_n = min(3, len(f))
        head = f.iloc[:head_n]
        head_med = float(head.median()) if head_n > 0 else 0.0

        # If first 2-3 months are below 25% of last, it is almost always a model artifact
        if head_med < (0.25 * last):
            return True

        # 3) New check: leading zeros (after clipping) often indicate negative forecasts
        # Count initial zeros in the forecast
        lead_zeros = 0
        for v in f.tolist():
            if float(v) <= 0.0:
                lead_zeros += 1
            else:
                break

        if lead_zeros >= 2 and last > 0:
            return True

        return False

    # Try ETS
    try:
        seasonal_periods = 12
        use_seasonal = len(series) >= (seasonal_periods * 2)

        model = ExponentialSmoothing(
            series,
            trend="add",
            seasonal="add" if use_seasonal else None,
            seasonal_periods=seasonal_periods if use_seasonal else None,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)
        fc = fit.forecast(periods).clip(lower=0.0)

        if not _looks_broken(fc):
            return fc

    except Exception:
        pass

    # Try SARIMAX
    try:
        seasonal_periods = 12
        seasonal_order = (1, 0, 1, seasonal_periods) if len(series) >= (seasonal_periods * 2) else (0, 0, 0, 0)

        model = SARIMAX(
            series,
            order=(1, 1, 1),
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fit = model.fit(disp=False)
        fc = fit.forecast(steps=periods).clip(lower=0.0)

        if not _looks_broken(fc):
            return fc

    except Exception:
        pass

    return _fallback()


def _build_daily_table(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    tmp["date"] = tmp["order_date"].dt.normalize()
    daily = tmp.groupby("date")["total_value"].sum().reset_index(name="sales")
    daily["weekday"] = daily["date"].dt.weekday
    return daily


def _weekday_weighted_projection(
    data_all: pd.DataFrame,
    start_current_month: pd.Timestamp,
    end_yesterday: pd.Timestamp,
    history_months: int = 6,
    logger: logging.Logger | None = None,
) -> dict:
    if logger is None:
        logger = logging.getLogger(__name__)

    df = data_all[data_all["order_date"] <= end_yesterday].copy()

    mtd = df[df["order_date"] >= start_current_month].copy()
    mtd_sales = float(mtd["total_value"].sum()) if not mtd.empty else 0.0

    days_in_month = int(start_current_month.days_in_month)

    today = end_yesterday.normalize() + pd.Timedelta(days=1)
    month_end = (start_current_month + pd.offsets.MonthEnd(0)).normalize()

    if today > month_end:
        return {
            "mtd_sales": mtd_sales,
            "projected_sales": mtd_sales,
            "remaining_days": 0,
            "days_in_month": days_in_month,
        }

    remaining_dates = pd.date_range(today, month_end, freq="D")
    remaining_days = int(len(remaining_dates))

    cur_weekday_avg = {}
    if not mtd.empty:
        daily_cur = _build_daily_table(mtd)
        if not daily_cur.empty:
            cur_weekday_avg = daily_cur.groupby("weekday")["sales"].mean().to_dict()

    hist_weekday_avg = {}
    if history_months and history_months > 0:
        end_prev_month = start_current_month - pd.Timedelta(microseconds=1)
        hist_start = (start_current_month - pd.offsets.MonthBegin(history_months)).normalize()

        hist = df[(df["order_date"] >= hist_start) & (df["order_date"] <= end_prev_month)].copy()
        if not hist.empty:
            daily_hist = _build_daily_table(hist)
            if not daily_hist.empty:
                hist_weekday_avg = daily_hist.groupby("weekday")["sales"].mean().to_dict()

    global_daily_avg = 0.0
    try:
        pool = None
        if hist_weekday_avg:
            end_prev_month = start_current_month - pd.Timedelta(microseconds=1)
            hist_start = (start_current_month - pd.offsets.MonthBegin(history_months)).normalize()
            pool = df[(df["order_date"] >= hist_start) & (df["order_date"] <= end_prev_month)].copy()
        else:
            pool = mtd.copy()

        if pool is not None and not pool.empty:
            daily_pool = _build_daily_table(pool)
            if not daily_pool.empty:
                global_daily_avg = float(daily_pool["sales"].mean())
    except Exception:
        global_daily_avg = 0.0

    expected_remaining = 0.0
    for d in remaining_dates:
        wd = int(d.weekday())
        if wd in cur_weekday_avg and pd.notna(cur_weekday_avg[wd]):
            expected = float(cur_weekday_avg[wd])
        elif wd in hist_weekday_avg and pd.notna(hist_weekday_avg[wd]):
            expected = float(hist_weekday_avg[wd])
        else:
            expected = float(global_daily_avg)

        if expected < 0:
            expected = 0.0

        expected_remaining += expected

    projected_sales = max(0.0, float(mtd_sales + expected_remaining))

    logger.info(
        "Weekday projection: mtd=%s remaining_days=%s projected=%s",
        mtd_sales, remaining_days, projected_sales
    )

    return {
        "mtd_sales": mtd_sales,
        "projected_sales": projected_sales,
        "remaining_days": remaining_days,
        "days_in_month": days_in_month,
    }


def get_monthly_sales_trend(
    output_file: str = "monthly_sales_trend.csv",
    forecast_periods: int = 6,
    return_forecast: bool = True,
    return_meta: bool = False,
    include_current_month_for_forecast: bool = True,
    projection_method: str = "weekday_weighted",
    weekday_history_months: int = 6,
    orders_csv_path: str = "data/all_orders.csv",
    utm_source_filter: str | None = None,
):
    logger = logging.getLogger(__name__)
    logger.info("Building monthly sales trend from %s", orders_csv_path)

    meta = {
        "forecast_includes_current_month_mtd": False,
        "projection_method": projection_method,
        "current_month_label": None,
        "current_month_mtd_sales": 0.0,
        "current_month_projected_sales": 0.0,
        "current_month_remaining_days": 0,
        "current_month_days_in_month": 0,
    }

    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    data = pd.read_csv(orders_csv_path)

    required_cols = {"order_date", "total_value"}
    missing = required_cols - set(data.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    data["order_date"] = pd.to_datetime(data["order_date"], errors="coerce")
    before = len(data)
    data = data.dropna(subset=["order_date"]).copy()
    after = len(data)
    if after < before:
        logger.warning("Dropped %s rows with invalid order_date", before - after)

    data["total_value"] = pd.to_numeric(data["total_value"], errors="coerce").fillna(0.0)

    if utm_source_filter:
        if "utm_source" not in data.columns:
            raise ValueError("Orders file is missing utm_source column (required for utm_source_filter).")

        u = (
            data["utm_source"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        data = data[u == utm_source_filter.strip().lower()].copy()
        logger.info("Applied utm_source filter: %s", utm_source_filter)

    today_bogota = pd.Timestamp.now(tz="America/Bogota").tz_localize(None).normalize()
    start_current_month = today_bogota.replace(day=1)
    end_prev_month = start_current_month - pd.Timedelta(microseconds=1)

    end_yesterday_bogota = today_bogota - pd.Timedelta(microseconds=1)

    meta["current_month_label"] = start_current_month.strftime("%Y-%m")

    output_dir = os.path.dirname(os.path.abspath(orders_csv_path))
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, output_file)

    # History: complete months only
    hist = data[data["order_date"] <= end_prev_month].copy()
    logger.info("History uses orders up to %s (end of previous month, Bogota clock)", end_prev_month)

    if hist.empty:
        pd.DataFrame(columns=["Month", "Sales"]).to_csv(csv_path, index=False)
        if return_forecast:
            if return_meta:
                return [], [], meta
            return [], []
        if return_meta:
            return [], meta
        return []

    hist["month_year"] = hist["order_date"].dt.to_period("M")

    total_sales_per_month = (
        hist.groupby("month_year")["total_value"]
        .sum()
        .reset_index(name="Total_Sales_Num")
        .sort_values("month_year")
    )

    total_sales_per_month["Month"] = total_sales_per_month["month_year"].apply(lambda x: x.strftime("%Y-%m"))
    total_sales_per_month["Total Sales"] = total_sales_per_month["Total_Sales_Num"].apply(lambda x: str(int(round(x))))

    summary_rows = total_sales_per_month[["Month", "Total Sales"]].to_dict(orient="records")

    csv_summary = total_sales_per_month[["Month", "Total_Sales_Num"]].rename(columns={"Total_Sales_Num": "Sales"})
    csv_summary.to_csv(csv_path, index=False)

    # Forecast
    forecast_rows = []
    if return_forecast:
        model_df = data[data["order_date"] <= end_yesterday_bogota].copy()
        model_df["month_year"] = model_df["order_date"].dt.to_period("M")

        model_monthly = (
            model_df.groupby("month_year")["total_value"]
            .sum()
            .reset_index(name="Sales")
            .sort_values("month_year")
        )

        if include_current_month_for_forecast and not model_monthly.empty:
            cur_period = start_current_month.to_period("M")
            cur_idx_list = model_monthly.index[model_monthly["month_year"] == cur_period].tolist()

            if cur_idx_list:
                idx = cur_idx_list[0]

                proj = _weekday_weighted_projection(
                    data_all=data,
                    start_current_month=start_current_month,
                    end_yesterday=end_yesterday_bogota,
                    history_months=weekday_history_months,
                    logger=logger,
                )

                model_monthly.loc[idx, "Sales"] = float(proj["projected_sales"])

                meta["forecast_includes_current_month_mtd"] = True
                meta["current_month_mtd_sales"] = float(proj["mtd_sales"])
                meta["current_month_projected_sales"] = float(proj["projected_sales"])
                meta["current_month_remaining_days"] = int(proj["remaining_days"])
                meta["current_month_days_in_month"] = int(proj["days_in_month"])

        model_monthly["Month"] = model_monthly["month_year"].apply(lambda x: x.strftime("%Y-%m"))
        model_monthly["Month_dt"] = pd.to_datetime(model_monthly["Month"] + "-01", errors="coerce")
        model_monthly = model_monthly.dropna(subset=["Month_dt"]).sort_values("Month_dt")

        if not model_monthly.empty:
            s = model_monthly.set_index("Month_dt")["Sales"].astype(float)
            full_idx = pd.date_range(s.index.min(), s.index.max(), freq="MS")
            s = s.reindex(full_idx, fill_value=0.0)

            fc = _forecast_monthly_series(s, periods=forecast_periods)
            fc_idx = pd.date_range(
                s.index.max() + pd.offsets.MonthBegin(1),
                periods=forecast_periods,
                freq="MS",
            )

            for d, v in zip(fc_idx, fc):
                forecast_rows.append({
                    "Month": d.strftime("%Y-%m"),
                    "Projected Total Sales": float(v),
                })

    if return_forecast:
        if return_meta:
            return summary_rows, forecast_rows, meta
        return summary_rows, forecast_rows

    if return_meta:
        return summary_rows, meta

    return summary_rows
