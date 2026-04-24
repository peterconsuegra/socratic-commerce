# app/services/daily_sales.py
import os
import logging
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX
from typing import Any


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

def get_empty_daily_sales_context(error: str | None = None) -> dict[str, Any]:
    return {
        "daily_sales_trend": [],
        "forecast_data": [],
        "pie_labels": [],
        "pie_values": [],
        "channel_charts": [],

        "other_channels_labels": [],
        "other_channels_pct": 0.0,

        "gender_labels_total": [],
        "gender_values_total": [],
        "gender_pies_by_channel": {},

        "city_labels_total": [],
        "city_values_total": [],
        "city_pies_by_channel": {},

        "time_labels_total": [],
        "time_values_total": [],
        "time_pies_by_channel": {},

        "error": error,
    }


def _top_n_with_other(series: pd.Series, n: int = 20, other_label: str = "Other") -> tuple[list[str], list[float]]:
    """
    series: pandas Series indexed by label, values numeric sales.
    Returns top N labels plus one aggregated Other bucket.
    """
    if series is None or len(series) == 0:
        return [], []

    s = series.copy()
    s = s[s > 0].sort_values(ascending=False)

    if len(s) == 0:
        return [], []

    top = s.head(n)
    rest_sum = float(s.iloc[n:].sum()) if len(s) > n else 0.0

    labels = [str(x) for x in top.index.tolist()]
    values = [float(x) for x in top.values.tolist()]

    if rest_sum > 0:
        labels.append(other_label)
        values.append(rest_sum)

    return labels, values


def _time_bucket_label(hour: int) -> str:
    """
    Returns a 3-hour bucket label for a given hour.
    """
    h = int(hour) if hour is not None else 0
    start = (h // 3) * 3
    end = start + 3

    return f"{start:02d}-{end:02d}" if end < 24 else "21-24"


def _time_pie_from_df(df_in: pd.DataFrame) -> tuple[list[str], list[float]]:
    """
    Builds labels and values for 3-hour buckets.

    The CSV order_date is treated as Bogota local wall-clock time.
    If timezone-aware values sneak in, the timezone is stripped without shifting.
    """
    if df_in is None or df_in.empty:
        return [], []

    if "order_date" not in df_in.columns:
        return [], []

    df_tmp = df_in.copy()

    df_tmp["order_date"] = pd.to_datetime(df_tmp["order_date"], errors="coerce")
    df_tmp = df_tmp.dropna(subset=["order_date"]).copy()

    try:
        if getattr(df_tmp["order_date"].dt, "tz", None) is not None:
            df_tmp["order_date"] = df_tmp["order_date"].dt.tz_localize(None)
    except Exception:
        pass

    df_tmp["hour"] = df_tmp["order_date"].dt.hour
    df_tmp["time_bucket"] = df_tmp["hour"].apply(_time_bucket_label)

    grouped = df_tmp.groupby("time_bucket")["total_value"].sum()

    bucket_order = [
        "00-03",
        "03-06",
        "06-09",
        "09-12",
        "12-15",
        "15-18",
        "18-21",
        "21-24",
    ]

    labels = []
    values = []

    for bucket in bucket_order:
        value = float(grouped.get(bucket, 0.0))
        if value > 0:
            labels.append(bucket)
            values.append(value)

    return labels, values


def _safe_slug(value: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in str(value))
    return safe.strip("_") or "unknown"


def _normalize_sales_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    required_cols = {"order_date", "total_value", "utm_source"}
    missing = required_cols - set(df.columns)

    if missing:
        raise ValueError(f"Orders file is missing required columns for charts: {sorted(missing)}")

    df = df.copy()

    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df = df.dropna(subset=["order_date"]).copy()

    df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

    df["utm_source"] = (
        df["utm_source"]
        .fillna("unknown")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    df.loc[df["utm_source"] == "", "utm_source"] = "unknown"

    if "gender" in df.columns:
        df["gender"] = (
            df["gender"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        df.loc[df["gender"] == "", "gender"] = "unknown"
    else:
        df["gender"] = "unknown"

    if "city" in df.columns:
        df["city"] = (
            df["city"]
            .fillna("unknown")
            .astype(str)
            .str.strip()
        )
        df.loc[df["city"] == "", "city"] = "unknown"
    else:
        df["city"] = "unknown"

    return df


def _build_channel_breakdown(
    grouped: pd.Series,
    min_share_percent: float = 2.0,
) -> tuple[list[dict[str, Any]], list[str], float]:
    total_sales = float(grouped.sum()) if len(grouped) else 0.0

    included = []
    excluded = []

    if total_sales <= 0:
        return included, [], 0.0

    for channel, value in grouped.items():
        value = float(value)

        if value <= 0:
            continue

        pct = (value / total_sales) * 100.0
        channel_key = str(channel)

        # Unknown is charted in the main source pie, but not expanded as a channel chart.
        if channel_key == "unknown":
            continue

        item = {
            "key": channel_key,
            "pct": pct,
            "value": value,
        }

        if pct >= min_share_percent:
            included.append(item)
        else:
            excluded.append(item)

    included.sort(key=lambda x: x["pct"], reverse=True)
    excluded.sort(key=lambda x: x["pct"], reverse=True)

    other_channels_labels = [x["key"] for x in excluded]
    other_channels_pct = float(sum(x["pct"] for x in excluded)) if excluded else 0.0

    return included, other_channels_labels, other_channels_pct


def build_daily_sales_dashboard_context(
    input_file: str,
    forecast_periods: int = 30,
    min_channel_share_percent: float = 2.0,
) -> dict[str, Any]:
    """
    Builds the complete context needed by templates/daily_sales.html.

    This keeps the Flask route focused on request orchestration only.
    """
    logger = logging.getLogger(__name__)

    if not os.path.exists(input_file):
        return get_empty_daily_sales_context(
            error=f"{input_file} not found. Use the date selector above to fetch data first."
        )

    context = get_empty_daily_sales_context()

    try:
        daily_sales_trend, forecast_data = get_daily_sales_trend_simple(
            output_file="daily_sales_trend.csv",
            forecast_periods=forecast_periods,
            return_forecast=True,
            orders_csv_path=input_file,
            utm_source_filter=None,
        )

        context["daily_sales_trend"] = daily_sales_trend
        context["forecast_data"] = forecast_data

        df = pd.read_csv(input_file)
        df = _normalize_sales_dataframe(df)

        # UTM source pie
        grouped = (
            df.groupby("utm_source")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )

        context["pie_labels"] = [str(k) for k, v in grouped.items() if float(v) > 0]
        context["pie_values"] = [float(v) for v in grouped.values if float(v) > 0]

        included_channels, other_channels_labels, other_channels_pct = _build_channel_breakdown(
            grouped,
            min_share_percent=min_channel_share_percent,
        )

        context["other_channels_labels"] = other_channels_labels
        context["other_channels_pct"] = other_channels_pct

        # Total gender pie
        gender_group_total = (
            df.groupby("gender")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )
        context["gender_labels_total"] = [
            str(k) for k, v in gender_group_total.items() if float(v) > 0
        ]
        context["gender_values_total"] = [
            float(v) for v in gender_group_total.values if float(v) > 0
        ]

        # Total city pie
        city_group_total = (
            df.groupby("city")["total_value"]
            .sum()
            .sort_values(ascending=False)
        )
        city_labels_total, city_values_total = _top_n_with_other(
            city_group_total,
            n=20,
            other_label="Other",
        )
        context["city_labels_total"] = city_labels_total
        context["city_values_total"] = city_values_total

        # Total time pie
        time_labels_total, time_values_total = _time_pie_from_df(df)
        context["time_labels_total"] = time_labels_total
        context["time_values_total"] = time_values_total

        # Channel-specific charts and pies
        channel_charts = []
        gender_pies_by_channel = {}
        city_pies_by_channel = {}
        time_pies_by_channel = {}

        for item in included_channels:
            channel = item["key"]
            pct = item["pct"]
            safe = _safe_slug(channel)

            trend_rows, forecast_rows = get_daily_sales_trend_simple(
                output_file=f"daily_sales_trend_{safe}.csv",
                forecast_periods=forecast_periods,
                return_forecast=True,
                orders_csv_path=input_file,
                utm_source_filter=channel,
            )

            if not trend_rows:
                continue

            df_ch = df[df["utm_source"] == channel].copy()

            gender_group_ch = (
                df_ch.groupby("gender")["total_value"]
                .sum()
                .sort_values(ascending=False)
            )
            gender_pies_by_channel[channel] = {
                "labels": [str(k) for k, v in gender_group_ch.items() if float(v) > 0],
                "values": [float(v) for v in gender_group_ch.values if float(v) > 0],
            }

            city_group_ch = (
                df_ch.groupby("city")["total_value"]
                .sum()
                .sort_values(ascending=False)
            )
            c_labels, c_values = _top_n_with_other(
                city_group_ch,
                n=20,
                other_label="Other",
            )
            city_pies_by_channel[channel] = {
                "labels": c_labels,
                "values": c_values,
            }

            t_labels, t_values = _time_pie_from_df(df_ch)
            time_pies_by_channel[channel] = {
                "labels": t_labels,
                "values": t_values,
            }

            channel_charts.append({
                "key": channel,
                "label": f"{channel.upper()} ({pct:.1f}%)",
                "canvas_id": f"dailySalesChart_{safe}",
                "gender_canvas_id": f"genderPie_{safe}",
                "city_canvas_id": f"cityPie_{safe}",
                "time_canvas_id": f"timePie_{safe}",
                "trend": trend_rows,
                "forecast": forecast_rows,
                "pct": pct,
            })

        context["channel_charts"] = channel_charts
        context["gender_pies_by_channel"] = gender_pies_by_channel
        context["city_pies_by_channel"] = city_pies_by_channel
        context["time_pies_by_channel"] = time_pies_by_channel

    except Exception as exc:
        logger.exception("Failed building daily sales dashboard context")
        context["error"] = str(exc)

    return context