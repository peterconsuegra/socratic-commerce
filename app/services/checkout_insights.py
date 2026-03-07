# app/services/checkout_insights.py
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

    # Try ETS with weekly seasonality when we have enough data
    try:
        seasonal_periods = 7
        use_seasonal = len(series) >= (seasonal_periods * 3)  # 21+ points

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

    # Fallback SARIMAX (weekly seasonality if enough length)
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


def _normalize_answer(val: str) -> str:
    s = "" if val is None else str(val)
    s = s.strip()
    if not s or s.lower() in {"nan", "none"}:
        return "unknown"
    return s


def _normalize_gender(val: str) -> str:
    s = "" if val is None else str(val)
    s = s.strip().lower()
    if not s or s in {"nan", "none"}:
        return "unknown"
    if s not in {"female", "male", "unknown", "other"}:
        return "unknown"
    return s


def _normalize_city(val: str) -> str:
    s = "" if val is None else str(val)
    s = s.strip()
    if not s or s.lower() in {"nan", "none", "n/a"}:
        return "unknown"

    if "(" in s:
        s = s.split("(", 1)[0].strip()

    s = " ".join(s.split())
    if not s:
        return "unknown"

    return s.title()


def _hour_bucket_label_from_hour(h: int) -> str:
    """
    3-hour buckets:
      0-2, 3-5, 6-8, 9-11, 12-14, 15-17, 18-20, 21-23
    Label format: "00:00-02:59"
    """
    try:
        h = int(h)
    except Exception:
        h = 0
    if h < 0:
        h = 0
    if h > 23:
        h = 23

    start = (h // 3) * 3
    end = start + 2
    return f"{start:02d}:00-{end:02d}:59"


def _apply_date_window_naive(
    df: pd.DataFrame,
    dt_col: str,
    start_date: str | None,
    end_date: str | None,
    logger: logging.Logger,
) -> pd.DataFrame:
    """
    Applies [start_date, end_date] inclusive window using DD/MM/YYYY.
    Assumes df[dt_col] is timezone-naive datetime already representing Bogota local time.
    """
    if not start_date or not end_date:
        return df

    try:
        start_dt = pd.to_datetime(start_date, format="%d/%m/%Y", errors="raise")
        end_dt = (
            pd.to_datetime(end_date, format="%d/%m/%Y", errors="raise")
            + pd.Timedelta(days=1)
            - pd.Timedelta(microseconds=1)
        )
        return df[(df[dt_col] >= start_dt) & (df[dt_col] <= end_dt)].copy()
    except Exception:
        logger.warning("Invalid custom date range: start_date=%s end_date=%s", start_date, end_date)
        return df


def _cutoff_end_of_yesterday_bogota_naive(df: pd.DataFrame, dt_col: str) -> pd.DataFrame:
    """
    Cuts data up to end of yesterday in Bogota, treating dt_col as naive local time.
    """
    today_bogota = pd.Timestamp.now(tz="America/Bogota").tz_localize(None).normalize()
    end_yesterday = today_bogota - pd.Timedelta(microseconds=1)
    return df[df[dt_col] <= end_yesterday].copy()


def _build_daily_series(df: pd.DataFrame, forecast_periods: int) -> tuple[list[dict], list[dict]]:
    if df.empty:
        return [], []

    df = df.copy()
    df["day"] = df["order_date"].dt.floor("D")

    daily = (
        df.groupby("day")["total_value"]
        .sum()
        .reset_index(name="Sales")
        .sort_values("day")
    )

    daily["Date"] = daily["day"].dt.strftime("%Y-%m-%d")
    daily["Total Sales"] = daily["Sales"].apply(lambda x: str(int(round(float(x)))))

    trend_rows = daily[["Date", "Total Sales"]].to_dict(orient="records")

    forecast_rows: list[dict] = []
    ts = daily[["Date", "Sales"]].copy()
    ts["Date_dt"] = pd.to_datetime(ts["Date"], errors="coerce")
    ts = ts.dropna(subset=["Date_dt"]).sort_values("Date_dt")

    if not ts.empty:
        s = ts.set_index("Date_dt")["Sales"].astype(float)
        full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
        s = s.reindex(full_idx, fill_value=0.0)

        fc = _forecast_daily_series(s, periods=forecast_periods)
        fc_idx = pd.date_range(s.index.max() + pd.Timedelta(days=1), periods=forecast_periods, freq="D")

        for d, v in zip(fc_idx, fc):
            forecast_rows.append({"Date": d.strftime("%Y-%m-%d"), "Projected Total Sales": float(v)})

    return trend_rows, forecast_rows


def _build_gender_pie(df: pd.DataFrame) -> tuple[list[str], list[float]]:
    if df.empty:
        return [], []
    gg = df.groupby("gender_norm")["total_value"].sum().sort_values(ascending=False)
    labels = [str(k) for k, v in gg.items() if float(v) > 0]
    values = [float(v) for v in gg.values if float(v) > 0]
    return labels, values


def _build_city_pie(
    df: pd.DataFrame,
    top_cities: int = 15,
    other_label_prefix: str = "Other",
) -> tuple[list[str], list[float], str | None]:
    if df.empty:
        return [], [], None

    gg = df.groupby("city_norm")["total_value"].sum().sort_values(ascending=False)
    gg = gg[gg > 0]
    if gg.empty:
        return [], [], None

    top = gg.head(int(top_cities))
    rest = gg.iloc[int(top_cities):]

    labels = [str(k) for k in top.index.tolist()]
    values = [float(v) for v in top.values.tolist()]

    other_label_used = None
    if not rest.empty:
        other_sum = float(rest.sum())
        if other_sum > 0:
            other_label_used = f"{other_label_prefix} ({len(rest)})"
            labels.append(other_label_used)
            values.append(other_sum)

    return labels, values, other_label_used


def _build_hour_bucket_pie(df: pd.DataFrame) -> tuple[list[str], list[float]]:
    """
    Sales share by time of day, grouped into 3-hour buckets.
    Returns fixed ordered buckets (only those with >0 sales).
    """
    if df.empty:
        return [], []

    tmp = df.copy()
    tmp = tmp.dropna(subset=["order_date"])
    if tmp.empty:
        return [], []

    tmp["hour"] = tmp["order_date"].dt.hour
    tmp["hour_bucket"] = tmp["hour"].apply(_hour_bucket_label_from_hour)

    gg = tmp.groupby("hour_bucket")["total_value"].sum()

    # Ensure buckets are in natural time order
    bucket_order = [
        "00:00-02:59",
        "03:00-05:59",
        "06:00-08:59",
        "09:00-11:59",
        "12:00-14:59",
        "15:00-17:59",
        "18:00-20:59",
        "21:00-23:59",
    ]

    labels: list[str] = []
    values: list[float] = []

    for b in bucket_order:
        v = float(gg.get(b, 0.0))
        if v > 0:
            labels.append(b)
            values.append(v)

    return labels, values


def get_checkout_insights_daily(
    orders_csv_path: str = "data/checkout_insights_orders.csv",
    forecast_periods: int = 30,
    min_share_percent: float = 2.0,
    start_date: str | None = None,
    end_date: str | None = None,
    top_cities: int = 20,
):
    """
    Builds:
      - Total daily sales trend + forecast
      - Pie: sales by utm_answer
      - Per-answer daily trend + forecast for answers >= min_share_percent
      - Gender pies (total + per answer) by sales value
      - City pies (total + per answer) by sales value (top_cities + other)
      - Hour-bucket pies (total + per answer) by sales value (3-hour chunks)

    Notes:
      - order_date is treated as Bogota (GMT-5) already and kept timezone-naive
      - data is cut to end of yesterday (Bogota) to avoid partial day
      - optional date window is applied after cutoff
    """
    logger = logging.getLogger(__name__)

    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    df = pd.read_csv(orders_csv_path)

    required_cols = {"order_date", "total_value", "utm_answer", "city"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    # Parse datetime (naive Bogota local)
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df = df.dropna(subset=["order_date"]).copy()

    df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce").fillna(0.0)

    df["utm_answer_norm"] = df["utm_answer"].apply(_normalize_answer)

    if "gender" in df.columns:
        df["gender_norm"] = df["gender"].apply(_normalize_gender)
    else:
        df["gender_norm"] = "unknown"

    df["city_norm"] = df["city"].apply(_normalize_city)

    # Cutoff then optional date window
    df = _cutoff_end_of_yesterday_bogota_naive(df, "order_date")
    df = _apply_date_window_naive(df, "order_date", start_date, end_date, logger)

    if df.empty:
        return {
            "total_daily_trend": [],
            "forecast_data": [],
            "pie_labels": [],
            "pie_values": [],
            "answer_charts": [],
            "other_answers_labels": [],
            "other_answers_pct": 0.0,
            "gender_labels_total": [],
            "gender_values_total": [],
            "city_labels_total": [],
            "city_values_total": [],
            "city_other_label_total": None,
            "hour_labels_total": [],
            "hour_values_total": [],
        }

    # Total trend + forecast
    total_daily_trend, forecast_data = _build_daily_series(df=df, forecast_periods=forecast_periods)

    # Pie by answer (sales)
    grouped_value = (
        df.groupby("utm_answer_norm")["total_value"]
        .sum()
        .sort_values(ascending=False)
    )

    pie_labels = [str(k) for k, v in grouped_value.items() if float(v) > 0]
    pie_values = [float(v) for v in grouped_value.values if float(v) > 0]
    total_sales_value = float(sum(pie_values)) if pie_values else 0.0

    included = []
    excluded = []

    if total_sales_value > 0:
        for ans, val in grouped_value.items():
            val = float(val)
            if val <= 0:
                continue
            pct = (val / total_sales_value) * 100.0
            item = {"key": str(ans), "pct": pct, "value": val}
            if pct >= float(min_share_percent):
                included.append(item)
            else:
                excluded.append(item)

    included.sort(key=lambda x: x["pct"], reverse=True)
    excluded.sort(key=lambda x: x["pct"], reverse=True)

    other_answers_labels = [x["key"] for x in excluded]
    other_answers_pct = float(sum(x["pct"] for x in excluded)) if excluded else 0.0

    # Gender pie (total)
    gender_labels_total, gender_values_total = _build_gender_pie(df)

    # City pie (total)
    city_labels_total, city_values_total, city_other_label_total = _build_city_pie(
        df, top_cities=top_cities, other_label_prefix="Other"
    )

    # Hour bucket pie (total)
    hour_labels_total, hour_values_total = _build_hour_bucket_pie(df)

    # Per-answer charts + per-answer pies
    answer_charts: list[dict] = []
    for item in included:
        answer_key = item["key"]
        pct = float(item["pct"])

        subset = df[df["utm_answer_norm"] == answer_key].copy()
        if subset.empty:
            continue

        trend_rows, fc_rows = _build_daily_series(df=subset, forecast_periods=forecast_periods)
        if not trend_rows:
            continue

        g_labels, g_values = _build_gender_pie(subset)
        c_labels, c_values, c_other_label = _build_city_pie(subset, top_cities=top_cities, other_label_prefix="Other")
        h_labels, h_values = _build_hour_bucket_pie(subset)

        safe = "".join(c if c.isalnum() else "_" for c in answer_key.lower())
        answer_charts.append({
            "key": answer_key,
            "label": f"{answer_key} ({pct:.1f}%)",
            "canvas_id": f"checkoutAnswerChart_{safe}",

            "gender_canvas_id": f"checkoutGenderPie_{safe}",
            "gender_labels": g_labels,
            "gender_values": g_values,

            "city_canvas_id": f"checkoutCityPie_{safe}",
            "city_labels": c_labels,
            "city_values": c_values,
            "city_other_label": c_other_label,

            "hour_canvas_id": f"checkoutHourPie_{safe}",
            "hour_labels": h_labels,
            "hour_values": h_values,

            "trend": trend_rows,
            "forecast": fc_rows,
            "pct": pct,
        })

    return {
        "total_daily_trend": total_daily_trend,
        "forecast_data": forecast_data,
        "pie_labels": pie_labels,
        "pie_values": pie_values,
        "answer_charts": answer_charts,
        "other_answers_labels": other_answers_labels,
        "other_answers_pct": other_answers_pct,
        "gender_labels_total": gender_labels_total,
        "gender_values_total": gender_values_total,
        "city_labels_total": city_labels_total,
        "city_values_total": city_values_total,
        "city_other_label_total": city_other_label_total,
        "hour_labels_total": hour_labels_total,
        "hour_values_total": hour_values_total,
    }
