# app/services/localidades.py
import json
import os
from functools import lru_cache
from typing import Optional, Dict

from flask import current_app
from shapely.geometry import Point, Polygon

# Small mapping hardcoded in the file (no external JSON needed)
LOCALIDADES_ESTRATOS: Dict[str, Dict[str, str]] = {
    "USAQUEN": {"estrato": "3 y 4", "nivel_socioeconomico": "Medio"},
    "CHAPINERO": {"estrato": "4 y 6", "nivel_socioeconomico": "Alto"},
    "SANTA FE": {"estrato": "2 y 3", "nivel_socioeconomico": "Bajo"},
    "SAN CRISTOBAL": {"estrato": "1 y 2", "nivel_socioeconomico": "Bajo"},
    "USME": {"estrato": "1", "nivel_socioeconomico": "Bajo"},
    "TUNJUELITO": {"estrato": "2", "nivel_socioeconomico": "Bajo"},
    "BOSA": {"estrato": "2", "nivel_socioeconomico": "Bajo"},
    "KENNEDY": {"estrato": "2", "nivel_socioeconomico": "Bajo"},
    "FONTIBON": {"estrato": "3", "nivel_socioeconomico": "Medio"},
    "ENGATIVA": {"estrato": "3", "nivel_socioeconomico": "Medio"},
    "SUBA": {"estrato": "2 y 3", "nivel_socioeconomico": "Bajo"},
    "BARRIOS UNIDOS": {"estrato": "3", "nivel_socioeconomico": "Medio"},
    "TEUSAQUILLO": {"estrato": "4", "nivel_socioeconomico": "Medio"},
    "LOS MARTIRES": {"estrato": "3", "nivel_socioeconomico": "Bajo"},
    "ANTONIO NARIÑO": {"estrato": "3", "nivel_socioeconomico": "Medio"},
    "PUENTE ARANDA": {"estrato": "3", "nivel_socioeconomico": "Medio"},
    "CANDELARIA": {"estrato": "3", "nivel_socioeconomico": "Medio"},
    "RAFAEL URIBE URIBE": {"estrato": "2", "nivel_socioeconomico": "Bajo"},
    "CIUDAD BOLIVAR": {"estrato": "1", "nivel_socioeconomico": "Bajo"},
    "SUMAPAZ": {"estrato": "1", "nivel_socioeconomico": "Bajo"},
}


def _rings_to_polygon(rings) -> Optional[Polygon]:
    if not rings or not isinstance(rings, list):
        return None
    exterior = rings[0]
    holes = rings[1:] if len(rings) > 1 else []
    if not exterior or len(exterior) < 4:
        return None
    try:
        return Polygon(exterior, holes=holes)
    except Exception:
        return None


def get_localidades_path() -> str:
    """
    Resolve bogota.json path relative to the Flask app.
    Put the file at: app/static/geo/bogota.json (recommended)
    """
    return os.path.join(current_app.root_path, "static", "geo", "bogota.json")


@lru_cache(maxsize=1)
def load_bogota_data() -> dict:
    path = get_localidades_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Localidades file not found at: {path}. "
            "Expected it at app/static/geo/bogota.json"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_localidad(name: str) -> str:
    return (name or "").strip().upper()


def find_localidad(lat: float, lon: float) -> Optional[str]:
    point = Point(lon, lat)
    data = load_bogota_data()

    for feature in data.get("features", []):
        geom = feature.get("geometry") or {}
        rings = geom.get("rings")
        poly = _rings_to_polygon(rings)
        if not poly:
            continue

        if poly.covers(point):
            attrs = feature.get("attributes", {}) or {}
            name = attrs.get("LocNombre") or attrs.get("name") or attrs.get("localidad")
            return _normalize_localidad(name) or "UNKNOWN"

    return None


def get_localidad_info(lat: float, lon: float) -> Dict[str, str]:
    """
    Returns info for a point:
      {
        "localidad": "CHAPINERO",
        "estrato": "4 y 6",
        "nivel_socioeconomico": "Alto"
      }

    If point is not inside any localidad polygon, fields are empty strings.
    If localidad exists but mapping is missing, estrato/nivel are empty strings.
    """
    loc = find_localidad(lat, lon) or ""
    loc = _normalize_localidad(loc)

    meta = LOCALIDADES_ESTRATOS.get(loc, {}) if loc else {}
    return {
        "localidad": loc,
        "estrato": str(meta.get("estrato") or "").strip(),
        "nivel_socioeconomico": str(meta.get("nivel_socioeconomico") or "").strip(),
    }
