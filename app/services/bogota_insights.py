# app/services/bogota_insights.py
import os
import re
import csv
import logging
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, Tuple, List
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


def _normalize_str(val: str, fallback: str = "") -> str:
    s = "" if val is None else str(val)
    s = s.strip()
    if not s or s.lower() in {"nan", "none"}:
        return fallback
    return s


def _normalize_city(val: str) -> str:
    s = _normalize_str(val, fallback="").strip()
    if not s or s.lower() in {"nan", "none", "n/a"}:
        return ""
    s = re.sub(r"\s+", " ", s)
    return s


def _normalize_gender(val: str) -> str:
    s = _normalize_str(val, fallback="").strip().lower()
    if s in {"female", "f", "mujer", "mujeres"}:
        return "female"
    if s in {"male", "m", "hombre", "hombres"}:
        return "male"
    return ""


def _normalize_campaign(val: str) -> str:
    s = _normalize_str(val, fallback="").strip()
    if not s:
        return "Unknown"

    low = s.lower()
    if low in {"nan", "none", "n/a", "undefined"}:
        return "Unknown"

    if low == "unknow":
        return "Unknown"

    s = re.sub(r"\s+", " ", s)
    return s


def _campaign_key(val: str) -> str:
    label = _normalize_campaign(val)
    if label == "Unknown":
        return "unknown"

    k = label.lower()
    k = k.replace("+", "").replace(" ", "")
    k = k.replace("%20", "").replace("%2b", "").replace("%2B", "")
    k = re.sub(r"[^a-z0-9]", "", k)

    return k or "unknown"


def _is_numeric_campaign(name: str) -> bool:
    if not name:
        return False
    return bool(re.fullmatch(r"\d+", name.strip()))


def _choose_display_name(display_name_counts: Dict[str, int], fallback_key: str) -> str:
    if not display_name_counts:
        return fallback_key

    best = sorted(
        display_name_counts.items(),
        key=lambda kv: (kv[1], len(kv[0]), kv[0]),
        reverse=True,
    )[0][0]
    return best


def _sum_trend_rows(trend_rows: List[Dict[str, Any]]) -> float:
    total = 0.0
    for r in trend_rows or []:
        try:
            total += float(r.get("Total Sales", "0") or 0)
        except Exception:
            continue
    return total


def _to_local(dt_series: pd.Series, tz: str) -> pd.Series:
    s = pd.to_datetime(dt_series, errors="coerce")
    if s.isna().all():
        return s

    try:
        if getattr(s.dt, "tz", None) is not None:
            return s.dt.tz_convert(tz)
    except Exception:
        pass

    # If naive timestamps, treat them as already in Bogota time and localize
    return s.dt.tz_localize(tz)


def _apply_time_cutoff_rules(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    dfx = df.copy()

    dfx["order_date"] = pd.to_datetime(dfx["order_date"], errors="coerce")
    dfx = dfx.dropna(subset=["order_date"]).copy()

    # order_date is already Bogota timezone (per your note), we still localize safely
    dfx["order_date_local"] = _to_local(dfx["order_date"], tz=tz)
    dfx = dfx.dropna(subset=["order_date_local"]).copy()

    dfx["total_value"] = pd.to_numeric(dfx["total_value"], errors="coerce").fillna(0.0)

    now_local = pd.Timestamp.now(tz=tz).normalize()
    end_yesterday = now_local - pd.Timedelta(microseconds=1)

    return dfx[dfx["order_date_local"] <= end_yesterday].copy()


def _build_daily_series(df: pd.DataFrame, tz: str, forecast_periods: int) -> Tuple[list[dict], list[dict]]:
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

    if forecast_periods <= 0:
        return trend_rows, []

    ts = daily[["Date", "Sales"]].copy()
    ts["Date_dt"] = pd.to_datetime(ts["Date"], errors="coerce")
    ts = ts.dropna(subset=["Date_dt"]).sort_values("Date_dt")
    if ts.empty:
        return trend_rows, []

    s = ts.set_index("Date_dt")["Sales"].astype(float)

    full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
    s = s.reindex(full_idx)
    s = s.interpolate(limit=2).fillna(0.0)

    fc = _forecast_daily_series(s, periods=forecast_periods)
    fc_idx = pd.date_range(s.index.max() + pd.Timedelta(days=1), periods=forecast_periods, freq="D")

    forecast_rows = [{"Date": d.strftime("%Y-%m-%d"), "Projected Total Sales": float(v)} for d, v in zip(fc_idx, fc)]
    return trend_rows, forecast_rows


def get_bogota_insights_daily(
    orders_csv_path: str = "data/facebook_sales_orders.csv",
    forecast_periods: int = 30,
    city_value: str = "BOGOTA (C/MARCA)",
    gender: Optional[str] = None,
) -> dict:
    tz = "America/Bogota"

    if not os.path.exists(orders_csv_path):
        raise FileNotFoundError(f"Orders data file not found: {orders_csv_path}")

    df = pd.read_csv(orders_csv_path)

    required_cols = {"order_date", "total_value", "city"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Orders file is missing required columns: {sorted(missing)}")

    target_city = _normalize_city(city_value).strip().lower()
    df["city_norm"] = df["city"].apply(_normalize_city).str.strip().str.lower()
    df = df[df["city_norm"] == target_city].copy()

    if gender:
        gender_norm = _normalize_gender(gender)
        if not gender_norm:
            raise ValueError("Invalid gender. Use 'female' or 'male'.")

        if "gender" not in df.columns:
            raise ValueError("Orders file is missing required column: gender")

        df["gender_norm"] = df["gender"].apply(_normalize_gender)
        df = df[df["gender_norm"] == gender_norm].copy()

    total_daily_trend, forecast_data = _build_daily_series(df=df, tz=tz, forecast_periods=forecast_periods)

    return {
        "total_daily_trend": total_daily_trend,
        "forecast_data": forecast_data,
    }


def read_saved_date_options(OptionModel, file_name: str, logger: Optional[logging.Logger] = None) -> Dict[str, Optional[str]]:
    out = {"date_range": None, "start_date": None, "end_date": None}

    try:
        q = OptionModel.query
        rec = q.filter_by(meta_key=f"date_range_{file_name}").first()
        out["date_range"] = rec.meta_value if rec else None

        rec = q.filter_by(meta_key=f"start_date_{file_name}").first()
        out["start_date"] = rec.meta_value if rec else None

        rec = q.filter_by(meta_key=f"end_date_{file_name}").first()
        out["end_date"] = rec.meta_value if rec else None

    except Exception:
        if logger:
            logger.exception("Reading date range options failed (bogota_insights)")
    return out


def build_city_filtered_csv(
    src_path: str,
    dst_path: str,
    target_city: str,
    gender: Optional[str] = None,
) -> Dict[str, Any]:
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"Source CSV not found: {src_path}")

    target_city_norm = _normalize_city(target_city).strip().lower()
    if not target_city_norm:
        raise ValueError("target_city is empty")

    gender_norm = None
    if gender:
        gender_norm = _normalize_gender(gender)
        if not gender_norm:
            raise ValueError("Invalid gender. Use 'female' or 'male'.")

    is_bogota = (target_city_norm == "bogota (c/marca)")

    get_localidad_info = None
    if is_bogota:
        try:
            from app.services.localidades import get_localidad_info as _get_localidad_info
            get_localidad_info = _get_localidad_info
        except Exception:
            get_localidad_info = None

    def _to_float(x):
        try:
            if x is None:
                return None
            s = str(x).strip()
            if not s or s.lower() in {"nan", "none", "null", "undefined"}:
                return None
            return float(s)
        except Exception:
            return None

    kept = 0
    removed = 0
    bad_rows = 0

    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)

    with open(src_path, "r", newline="", encoding="utf-8-sig", errors="replace") as src, open(
        dst_path, "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)

        if not reader.fieldnames:
            raise ValueError("CSV has no header row")

        base_fieldnames = [fn.lstrip("\ufeff") if isinstance(fn, str) else fn for fn in reader.fieldnames]

        if "city" not in base_fieldnames:
            raise ValueError(f"CSV is missing required column: city. Found columns: {base_fieldnames}")

        if gender_norm and "gender" not in base_fieldnames:
            raise ValueError(f"CSV is missing required column: gender. Found columns: {base_fieldnames}")

        fieldnames = list(base_fieldnames)
        if is_bogota:
            if "order_lat" not in fieldnames:
                fieldnames.append("order_lat")
            if "order_lng" not in fieldnames:
                fieldnames.append("order_lng")

            for col in ["localidad", "estrato", "nivel_socioeconomico"]:
                if col not in fieldnames:
                    fieldnames.append(col)

        writer = csv.DictWriter(dst, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row in reader:
            try:
                if None in row:
                    row.pop(None, None)

                row_city = _normalize_city(row.get("city")).strip().lower()
                if row_city != target_city_norm:
                    removed += 1
                    continue

                if gender_norm:
                    row_gender = _normalize_gender(row.get("gender"))
                    if row_gender != gender_norm:
                        removed += 1
                        continue

                if is_bogota:
                    row.setdefault("order_lat", "")
                    row.setdefault("order_lng", "")

                    row["localidad"] = row.get("localidad") or ""
                    row["estrato"] = row.get("estrato") or ""
                    row["nivel_socioeconomico"] = row.get("nivel_socioeconomico") or ""

                    if get_localidad_info:
                        lat = _to_float(row.get("order_lat"))
                        lng = _to_float(row.get("order_lng"))
                        if lat is not None and lng is not None:
                            try:
                                info = get_localidad_info(lat, lng) or {}
                                row["localidad"] = (info.get("localidad") or "").strip()
                                row["estrato"] = (info.get("estrato") or "").strip()
                                row["nivel_socioeconomico"] = (info.get("nivel_socioeconomico") or "").strip()
                            except Exception:
                                pass

                writer.writerow(row)
                kept += 1

            except Exception:
                bad_rows += 1
                continue

    return {
        "kept": kept,
        "removed": removed,
        "bad_rows": bad_rows,
        "output_file": dst_path,
    }


def _localidad_breakdown(df: pd.DataFrame, top_n: int = 10) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []

    if "localidad" not in df.columns or "total_value" not in df.columns:
        return []

    dfx = df.copy()
    dfx["total_value"] = pd.to_numeric(dfx["total_value"], errors="coerce").fillna(0.0)

    loc = dfx["localidad"].fillna("").astype(str).str.strip()
    loc = loc.replace({"": "Unknown", "nan": "Unknown", "None": "Unknown"})
    dfx["localidad_norm"] = loc

    totals = (
        dfx.groupby("localidad_norm")["total_value"]
        .sum()
        .sort_values(ascending=False)
    )

    if totals.empty:
        return []

    labels = totals.index.tolist()
    values = totals.values.tolist()

    if len(labels) <= top_n:
        return [{"label": str(l), "value": float(v)} for l, v in zip(labels, values)]

    top_labels = labels[:top_n]
    top_values = values[:top_n]
    other_sum = float(sum(values[top_n:]))

    out = [{"label": str(l), "value": float(v)} for l, v in zip(top_labels, top_values)]
    if other_sum > 0:
        out.append({"label": "Other", "value": other_sum})
    return out


def _estrato_breakdown(df: pd.DataFrame, top_n: int = 10) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []

    if "estrato" not in df.columns or "total_value" not in df.columns:
        return []

    dfx = df.copy()
    dfx["total_value"] = pd.to_numeric(dfx["total_value"], errors="coerce").fillna(0.0)

    estr = dfx["estrato"].fillna("").astype(str).str.strip()
    estr = estr.replace({"": "Unknown", "nan": "Unknown", "None": "Unknown"})
    dfx["estrato_norm"] = estr

    totals = (
        dfx.groupby("estrato_norm")["total_value"]
        .sum()
        .sort_values(ascending=False)
    )

    if totals.empty:
        return []

    labels = totals.index.tolist()
    values = totals.values.tolist()

    if len(labels) <= top_n:
        return [{"label": str(l), "value": float(v)} for l, v in zip(labels, values)]

    top_labels = labels[:top_n]
    top_values = values[:top_n]
    other_sum = float(sum(values[top_n:]))

    out = [{"label": str(l), "value": float(v)} for l, v in zip(top_labels, top_values)]
    if other_sum > 0:
        out.append({"label": "Other", "value": other_sum})
    return out


def _nivel_socioeconomico_breakdown(df: pd.DataFrame, top_n: int = 10) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []

    if "nivel_socioeconomico" not in df.columns or "total_value" not in df.columns:
        return []

    dfx = df.copy()
    dfx["total_value"] = pd.to_numeric(dfx["total_value"], errors="coerce").fillna(0.0)

    ns = dfx["nivel_socioeconomico"].fillna("").astype(str).str.strip()
    ns = ns.replace({"": "Unknown", "nan": "Unknown", "None": "Unknown"})
    dfx["nivel_norm"] = ns

    totals = (
        dfx.groupby("nivel_norm")["total_value"]
        .sum()
        .sort_values(ascending=False)
    )

    if totals.empty:
        return []

    labels = totals.index.tolist()
    values = totals.values.tolist()

    if len(labels) <= top_n:
        return [{"label": str(l), "value": float(v)} for l, v in zip(labels, values)]

    top_labels = labels[:top_n]
    top_values = values[:top_n]
    other_sum = float(sum(values[top_n:]))

    out = [{"label": str(l), "value": float(v)} for l, v in zip(top_labels, top_values)]
    if other_sum > 0:
        out.append({"label": "Other", "value": other_sum})
    return out


def _time_chunk_label(hour: int) -> str:
    """
    3-hour chunks:
    00:00-02:59, 03:00-05:59, ..., 21:00-23:59
    """
    h = int(hour) if hour is not None else 0
    start = (h // 3) * 3
    end = start + 2
    return f"{start:02d}:00-{end:02d}:59"


def _time_of_day_3h_breakdown(df: pd.DataFrame, tz: str) -> List[Dict[str, Any]]:
    """
    Pie chart input:
    [{label: "00:00-02:59", value: <sales>}, ...]
    Uses order_date in Bogota time (already), via order_date_local.
    """
    if df is None or df.empty:
        return []

    needed = {"order_date", "total_value"}
    if not needed.issubset(set(df.columns)):
        return []

    dfx = _apply_time_cutoff_rules(df, tz=tz)
    if dfx.empty:
        return []

    # order_date_local is tz-aware Bogota time
    dfx["hour"] = dfx["order_date_local"].dt.hour
    dfx["chunk"] = dfx["hour"].apply(_time_chunk_label)

    totals = (
        dfx.groupby("chunk")["total_value"]
        .sum()
        .sort_values(ascending=False)
    )

    if totals.empty:
        return []

    # Keep natural chronological order for display
    ordered_labels = [f"{h:02d}:00-{h+2:02d}:59" for h in range(0, 24, 3)]
    totals = totals.reindex(ordered_labels).fillna(0.0)

    out = []
    for label, val in totals.items():
        v = float(val) if val is not None else 0.0
        if v <= 0:
            continue
        out.append({"label": str(label), "value": v})

    return out


def _utm_content_by_localidad_stacked(
    df: pd.DataFrame,
    *,
    top_contents: int = 6,
    top_localidades: int = 12,
) -> Dict[str, Any]:
    if df is None or df.empty:
        return {"labels": [], "datasets": []}

    needed = {"total_value", "localidad", "utm_content"}
    if not needed.issubset(set(df.columns)):
        return {"labels": [], "datasets": []}

    dfx = df.copy()
    dfx["total_value"] = pd.to_numeric(dfx["total_value"], errors="coerce").fillna(0.0)

    loc = dfx["localidad"].fillna("").astype(str).str.strip()
    loc = loc.replace({"": "Unknown", "nan": "Unknown", "None": "Unknown"})
    dfx["localidad_norm"] = loc

    cont = dfx["utm_content"].fillna("").astype(str).str.strip()
    cont = cont.replace({"": "Unknown", "nan": "Unknown", "None": "Unknown", "undefined": "Unknown"})
    dfx["utm_content_norm"] = cont

    loc_totals = (
        dfx.groupby("localidad_norm")["total_value"]
        .sum()
        .sort_values(ascending=False)
    )
    if loc_totals.empty:
        return {"labels": [], "datasets": []}

    top_loc_labels = loc_totals.index.tolist()[:max(1, int(top_localidades))]
    dfx = dfx[dfx["localidad_norm"].isin(top_loc_labels)].copy()
    if dfx.empty:
        return {"labels": [], "datasets": []}

    content_totals = (
        dfx.groupby("utm_content_norm")["total_value"]
        .sum()
        .sort_values(ascending=False)
    )
    top_contents_labels = content_totals.index.tolist()[:max(1, int(top_contents))]
    dfx = dfx[dfx["utm_content_norm"].isin(top_contents_labels)].copy()
    if dfx.empty:
        return {"labels": [], "datasets": []}

    pivot = (
        dfx.pivot_table(
            index="localidad_norm",
            columns="utm_content_norm",
            values="total_value",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reindex(index=top_loc_labels, fill_value=0.0)
    )

    labels = [str(x) for x in pivot.index.tolist()]
    datasets: List[Dict[str, Any]] = []

    for content_label in top_contents_labels:
        if content_label not in pivot.columns:
            continue
        datasets.append(
            {
                "label": str(content_label),
                "data": [float(v) for v in pivot[content_label].tolist()],
            }
        )

    return {"labels": labels, "datasets": datasets}


def _build_campaign_charts_from_csv(
    filtered_file: str,
    tz: str,
    forecast_periods: int,
    logger: Optional[logging.Logger] = None,
) -> List[Dict[str, Any]]:
    if not os.path.exists(filtered_file):
        return []

    try:
        df = pd.read_csv(filtered_file)
    except Exception:
        if logger:
            logger.exception("Failed to read filtered CSV for campaign charts: %s", filtered_file)
        return []

    required_cols = {"order_date", "total_value"}
    missing = required_cols - set(df.columns)
    if missing:
        if logger:
            logger.warning("Campaign charts skipped. Missing columns: %s", sorted(missing))
        return []

    if "utm_campaign" not in df.columns:
        if logger:
            logger.warning("Campaign charts skipped. utm_campaign column not found in %s", filtered_file)
        return []

    dfx = _apply_time_cutoff_rules(df, tz=tz)
    if dfx.empty:
        return []

    dfx["utm_campaign_display"] = dfx["utm_campaign"].apply(_normalize_campaign)
    dfx["utm_campaign_key"] = dfx["utm_campaign"].apply(_campaign_key)

    charts: List[Dict[str, Any]] = []

    for campaign_key, g in dfx.groupby("utm_campaign_key"):
        counts = g["utm_campaign_display"].value_counts(dropna=False).to_dict()
        display_name = _choose_display_name(counts, fallback_key=campaign_key)

        trend_rows, forecast_rows = _build_daily_series(g, tz=tz, forecast_periods=forecast_periods)
        if not trend_rows and not forecast_rows:
            continue

        total_hist = _sum_trend_rows(trend_rows)

        loc_breakdown = _localidad_breakdown(g, top_n=10)
        estr_breakdown = _estrato_breakdown(g, top_n=10)
        nivel_breakdown = _nivel_socioeconomico_breakdown(g, top_n=10)

        # NEW: time-of-day breakdown (3-hour chunks)
        time_of_day_breakdown = _time_of_day_3h_breakdown(g, tz=tz)

        # Existing: utm_content totals by localidad (stacked bar data)
        utm_content_localidad = _utm_content_by_localidad_stacked(
            g,
            top_contents=6,
            top_localidades=12,
        )

        charts.append(
            {
                "campaign": display_name,
                "campaign_key": campaign_key,
                "total_daily_trend": trend_rows,
                "forecast_data": forecast_rows,
                "total_hist": float(total_hist),
                "localidad_breakdown": loc_breakdown,
                "estrato_breakdown": estr_breakdown,
                "nivel_socioeconomico_breakdown": nivel_breakdown,
                # NEW
                "time_of_day_breakdown": time_of_day_breakdown,
                # Existing
                "utm_content_localidad": utm_content_localidad,
            }
        )

    charts.sort(key=lambda x: float(x.get("total_hist", 0.0)), reverse=True)
    return charts


def _group_campaign_charts(charts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wati_items: List[Dict[str, Any]] = []
    unknown_items: List[Dict[str, Any]] = []
    google_items: List[Dict[str, Any]] = []
    meta_items: List[Dict[str, Any]] = []

    for c in charts:
        key = (c.get("campaign_key") or "").strip().lower()
        display = (c.get("campaign") or "").strip()

        if key == "wati" or display.lower() == "wati":
            wati_items.append(c)
            continue

        if key in {"unknown", "unknow"} or display.lower() in {"unknown", "unknow"}:
            unknown_items.append(c)
            continue

        if _is_numeric_campaign(display):
            google_items.append(c)
            continue

        meta_items.append(c)

    chart_id = 0
    for bucket in (wati_items, unknown_items, google_items, meta_items):
        for c in bucket:
            c["chart_id"] = chart_id
            chart_id += 1

    groups = [
        {"key": "wati", "title": "wati", "items": wati_items, "open_by_default": True},
        {"key": "unknown", "title": "Unknown", "items": unknown_items, "open_by_default": False},
        {"key": "google", "title": "Google", "items": google_items, "open_by_default": False},
        {"key": "meta", "title": "Meta", "items": meta_items, "open_by_default": False},
    ]

    for g in groups:
        g["total_hist"] = float(sum(float(x.get("total_hist", 0.0) or 0.0) for x in (g.get("items") or [])))

    grand_total = float(sum(float(g.get("total_hist", 0.0) or 0.0) for g in groups))
    for g in groups:
        if grand_total > 0:
            g["percent"] = round((float(g.get("total_hist", 0.0) or 0.0) / grand_total) * 100.0, 1)
        else:
            g["percent"] = 0.0

    return groups


def get_bogota_insights_view_data(
    *,
    OptionModel,
    source_file: str,
    filtered_file: str,
    city_value: str,
    forecast_periods: int = 30,
    gender: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    tz = "America/Bogota"
    file_name = os.path.basename(source_file)

    opts = read_saved_date_options(OptionModel, file_name=file_name, logger=logger)

    payload: Dict[str, Any] = {
        "error": None,
        "date_range": opts["date_range"],
        "start_date": opts["start_date"],
        "end_date": opts["end_date"],
        "total_daily_trend": [],
        "forecast_data": [],
        "campaign_groups": [],
        "gender": _normalize_gender(gender) if gender else None,
    }

    if not os.path.exists(source_file):
        payload["error"] = f"{source_file} not found. Use the date selector above to fetch data first."
        return payload

    if not os.path.exists(filtered_file):
        payload["error"] = f"{filtered_file} not found. Could not build filtered data."
        return payload

    result = get_bogota_insights_daily(
        orders_csv_path=filtered_file,
        forecast_periods=forecast_periods,
        city_value=city_value,
        gender=gender,
    )

    payload["total_daily_trend"] = result.get("total_daily_trend", [])
    payload["forecast_data"] = result.get("forecast_data", [])

    charts = _build_campaign_charts_from_csv(
        filtered_file=filtered_file,
        tz=tz,
        forecast_periods=forecast_periods,
        logger=logger,
    )

    payload["campaign_groups"] = _group_campaign_charts(charts)

    if logger:
        counts = {g["key"]: len(g.get("items") or []) for g in payload["campaign_groups"]}
        logger.info("Campaign groups built: %s", counts)

    return payload
