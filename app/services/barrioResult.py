# app/services/barrioResult.py
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Tuple, Dict, Any

import geopandas as gpd
from shapely.geometry import Point
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable


@dataclass(frozen=True)
class BarrioResult:
    lat: float
    lon: float
    barrio: Optional[str]
    raw_fields: dict
    debug: dict


def _is_missing(v) -> bool:
    """True for None, NaN, NaT."""
    if v is None:
        return True
    try:
        import pandas as pd
        return bool(pd.isna(v))
    except Exception:
        return False


def _json_safe(obj):
    """
    Make values JSON serializable:
      - None/NaN/NaT -> None
      - datetime/date -> isoformat
      - dict/list/tuple -> recurse
      - everything else -> keep if simple, else str()
    """
    if _is_missing(obj):
        return None

    if isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]

    try:
        from datetime import datetime, date
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
    except Exception:
        pass

    return str(obj)


@lru_cache(maxsize=256)
def geocode_bogota_address(address: str) -> Tuple[float, float]:
    """
    Best-effort geocoder for Bogotá addresses.
    Tries a few normalized variants because Nominatim frequently fails on Colombian '#' formats.
    """
    geolocator = Nominatim(
        user_agent="bogota_barrio_lookup",
        timeout=10,
    )

    def _norm(s: str) -> str:
        s = (s or "").strip()
        s = " ".join(s.split())
        # common normalization for Colombia addresses
        s = s.replace("#", " No ")
        s = s.replace("  ", " ")
        return s.strip()

    raw = _norm(address)

    # Build variants (ordered from most specific to more general)
    variants = []

    # Full, with Bogotá and country
    variants.append(f"{raw}, Bogotá, Colombia")

    # Force "Bogotá D.C." wording sometimes helps
    variants.append(f"{raw}, Bogotá D.C., Colombia")

    # Remove common directional words that hurt matching
    lowered = raw.lower()
    for bad in [" sur", " norte", " este", " oeste"]:
        lowered = lowered.replace(bad, "")
    lowered = _norm(lowered)
    if lowered and lowered != raw:
        variants.append(f"{lowered}, Bogotá, Colombia")
        variants.append(f"{lowered}, Bogotá D.C., Colombia")

    # Super fallback: just try the cleaned address without city suffix duplication
    variants.append(raw)

    last_err: Optional[Exception] = None

    for attempt in range(3):
        for q in variants:
            try:
                loc = geolocator.geocode(
                    q,
                    exactly_one=True,
                    addressdetails=False,
                    country_codes="co",  # restrict to Colombia
                )
                if loc:
                    return float(loc.latitude), float(loc.longitude)
            except (GeocoderTimedOut, GeocoderUnavailable) as e:
                last_err = e
                time.sleep(1 + attempt)
                continue
            except Exception as e:
                last_err = e
                continue

        # backoff between rounds
        time.sleep(1 + attempt)

    # If we get here, nothing matched
    raise ValueError(
        f"Could not geocode address. Tried variants: {variants[:4]}{'...' if len(variants) > 4 else ''}"
        + (f" Last error: {last_err}" if last_err else "")
    )


def _ensure_crs(gdf: gpd.GeoDataFrame, shp_path: str, env_var: str = "BOGOTA_SHP_CRS") -> gpd.GeoDataFrame:
    """
    If CRS is missing, set it from env var.
    If not set, try a small list of common Bogotá CRSs.
    """
    if gdf.crs is not None:
        return gdf

    base = os.path.splitext(shp_path)[0]
    prj_path = base + ".prj"

    env_crs = os.getenv(env_var, "").strip()

    candidates = []
    if env_crs:
        candidates.append(env_crs)

    candidates.extend(["EPSG:4686", "EPSG:4326", "EPSG:3116", "EPSG:3857"])

    last_err: Optional[Exception] = None
    for crs in candidates:
        try:
            gdf2 = gdf.set_crs(crs, allow_override=True)

            gdf_ll = gdf2.to_crs(epsg=4326)
            minx, miny, maxx, maxy = gdf_ll.total_bounds

            if (-80 < minx < -60) and (-80 < maxx < -60) and (-5 < miny < 15) and (-5 < maxy < 15):
                return gdf2
        except Exception as e:
            last_err = e
            continue

    missing_prj_note = ""
    if not os.path.exists(prj_path):
        missing_prj_note = f" Missing .prj file: {prj_path}."

    raise ValueError(
        "Shapefile CRS is missing and could not be inferred safely."
        f"{missing_prj_note} "
        f"Set env var {env_var} to the correct CRS (example: EPSG:3116) "
        "or add the correct .prj next to the shapefile."
        + (f" Last error: {last_err}" if last_err else "")
    )


@lru_cache(maxsize=16)
def _load_shp_cached(shp_path: str, env_var: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(shp_path)
    gdf = _ensure_crs(gdf, shp_path=shp_path, env_var=env_var)

    # Drop empty geometries
    try:
        gdf = gdf[gdf.geometry.notna()].copy()
        gdf = gdf[~gdf.geometry.is_empty].copy()
    except Exception:
        pass

    return gdf


def _find_name_from_row(row: dict, columns: list, candidates: Tuple[str, ...]) -> Optional[str]:
    def clean(v):
        if _is_missing(v):
            return None
        s = str(v).strip()
        if not s or s.lower() in ("nan", "none"):
            return None
        return s

    # Exact matches first
    for col in candidates:
        if col in columns:
            val = clean(row.get(col))
            if val:
                return val

    # Substring match
    upper_cols = [c.upper() for c in columns]
    for want in candidates:
        w = want.upper()
        for i, c in enumerate(upper_cols):
            if w in c:
                real_col = columns[i]
                val = clean(row.get(real_col))
                if val:
                    return val

    # Heuristic fallback: pick any string field whose column suggests a name
    preferred_keys = []
    for c in columns:
        cu = c.upper()
        if ("NOM" in cu) or ("BARR" in cu) or ("NAME" in cu):
            preferred_keys.append(c)

    for c in preferred_keys:
        val = clean(row.get(c))
        if val:
            return val

    return None


def _has_match_index_right(idx_val) -> bool:
    """True only if index_right is present AND not NaN/NaT."""
    try:
        import pandas as pd
        return bool(pd.notna(idx_val))
    except Exception:
        return (idx_val is not None)


def _point_join_with_fallbacks(
    lat: float,
    lon: float,
    shp_path: str,
    env_var: str,
    buffer_meters: float = 25.0,
) -> Tuple[Optional[dict], Dict[str, Any], gpd.GeoDataFrame]:
    """
    Returns (row_dict_or_none, debug, joined_gdf).

    Fallback order:
      1) within
      2) intersects
      3) buffer (meters) + intersects (EPSG:3116)
      4) nearest polygon (EPSG:3116) with distance
    """
    debug: Dict[str, Any] = {}

    gdf_raw = _load_shp_cached(shp_path, env_var=env_var)
    debug["shp_path"] = shp_path
    debug["features"] = int(len(gdf_raw))
    debug["crs"] = str(gdf_raw.crs) if gdf_raw.crs is not None else None

    try:
        minx, miny, maxx, maxy = gdf_raw.to_crs(epsg=4326).total_bounds
        debug["bounds_wgs84"] = {"minx": float(minx), "miny": float(miny), "maxx": float(maxx), "maxy": float(maxy)}
    except Exception as e:
        debug["bounds_wgs84_error"] = str(e)

    gdf = gdf_raw.to_crs(epsg=4326)
    pt = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326")

    # 1) within
    try:
        joined = gpd.sjoin(pt, gdf, how="left", predicate="within")
        debug["join_within_rows"] = int(len(joined))
        if not joined.empty:
            row = joined.iloc[0].to_dict()
            idx = row.get("index_right")
            debug["within_index_right"] = _json_safe(idx)
            debug["within_matched"] = bool(_has_match_index_right(idx))
            if _has_match_index_right(idx):
                debug["matched_by"] = "within"
                return row, debug, joined
    except Exception as e:
        debug["join_within_error"] = str(e)

    # 2) intersects
    try:
        joined = gpd.sjoin(pt, gdf, how="left", predicate="intersects")
        debug["join_intersects_rows"] = int(len(joined))
        if not joined.empty:
            row = joined.iloc[0].to_dict()
            idx = row.get("index_right")
            debug["intersects_index_right"] = _json_safe(idx)
            debug["intersects_matched"] = bool(_has_match_index_right(idx))
            if _has_match_index_right(idx):
                debug["matched_by"] = "intersects"
                return row, debug, joined
    except Exception as e:
        debug["join_intersects_error"] = str(e)

    # 3) buffered point in meters (EPSG:3116) then intersects
    try:
        gdf_3116 = gdf_raw.to_crs(epsg=3116)
        pt_3116 = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326").to_crs(epsg=3116)
        buf_geom = pt_3116.geometry.iloc[0].buffer(float(buffer_meters))
        buf = gpd.GeoDataFrame(geometry=[buf_geom], crs="EPSG:3116")

        joined_3116 = gpd.sjoin(buf, gdf_3116, how="left", predicate="intersects")
        debug["join_buffer_intersects_rows"] = int(len(joined_3116))
        if not joined_3116.empty:
            row = joined_3116.iloc[0].to_dict()
            idx = row.get("index_right")
            debug["buffer_index_right"] = _json_safe(idx)
            debug["buffer_matched"] = bool(_has_match_index_right(idx))
            debug["buffer_meters"] = float(buffer_meters)
            if _has_match_index_right(idx):
                debug["matched_by"] = "buffer_intersects"
                return row, debug, joined_3116
    except Exception as e:
        debug["join_buffer_intersects_error"] = str(e)

    # 4) nearest polygon (EPSG:3116)
    try:
        gdf_3116 = gdf_raw.to_crs(epsg=3116)
        pt_3116 = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326").to_crs(epsg=3116)

        nearest = gpd.sjoin_nearest(pt_3116, gdf_3116, how="left", distance_col="__distance_m")
        debug["nearest_rows"] = int(len(nearest))
        if not nearest.empty:
            row = nearest.iloc[0].to_dict()
            idx = row.get("index_right")
            dist = row.get("__distance_m")
            debug["nearest_index_right"] = _json_safe(idx)
            debug["nearest_distance_m"] = float(dist) if not _is_missing(dist) else None
            debug["matched_by"] = "nearest"
            return row, debug, nearest
    except Exception as e:
        debug["nearest_error"] = str(e)

    return None, debug, pt


def barrio_from_point(
    lat: float,
    lon: float,
    sector_catastral_shp_path: str,
    barrio_field_candidates: Tuple[str, ...] = ("BARRIO", "NOMBRE_BARRIO", "NOM_BARRIO", "BARRIO_C", "NOMBRE"),
) -> BarrioResult:
    row, debug, joined = _point_join_with_fallbacks(
        lat=lat,
        lon=lon,
        shp_path=sector_catastral_shp_path,
        env_var="BOGOTA_SECTOR_SHP_CRS",
        buffer_meters=20.0,
    )

    if row is None:
        return BarrioResult(lat=lat, lon=lon, barrio=None, raw_fields={}, debug=_json_safe(debug))

    cols = list(joined.columns)
    barrio_value = _find_name_from_row(row, cols, barrio_field_candidates)

    raw_fields = {k: row.get(k) for k in cols if k not in ("geometry", "index_right")}
    return BarrioResult(
        lat=lat,
        lon=lon,
        barrio=barrio_value,
        raw_fields=_json_safe(raw_fields),
        debug=_json_safe(debug),
    )


def barrio_from_address(address: str, sector_catastral_shp_path: str) -> BarrioResult:
    lat, lon = geocode_bogota_address(address)
    return barrio_from_point(lat, lon, sector_catastral_shp_path)


def barrio_legalizado_from_point(
    lat: float,
    lon: float,
    barrio_legalizado_shp_path: str,
    barrio_field_candidates: Tuple[str, ...] = (
        "BARRIO",
        "BARRIO_LEG",
        "BARRIOLEGAL",
        "NOMBRE_BARRIO",
        "NOM_BARRIO",
        "NOMBRE",
        "NOMBRE_BAR",
    ),
) -> BarrioResult:
    row, debug, joined = _point_join_with_fallbacks(
        lat=lat,
        lon=lon,
        shp_path=barrio_legalizado_shp_path,
        env_var="BOGOTA_BARRIO_SHP_CRS",
        buffer_meters=35.0,
    )

    if row is None:
        return BarrioResult(lat=lat, lon=lon, barrio=None, raw_fields={}, debug=_json_safe(debug))

    cols = list(joined.columns)
    barrio_value = _find_name_from_row(row, cols, barrio_field_candidates)

    raw_fields = {k: row.get(k) for k in cols if k not in ("geometry", "index_right")}
    return BarrioResult(
        lat=lat,
        lon=lon,
        barrio=barrio_value,
        raw_fields=_json_safe(raw_fields),
        debug=_json_safe(debug),
    )
