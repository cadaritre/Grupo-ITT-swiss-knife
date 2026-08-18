from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .app_storage import cache_dir


OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
MAX_SELECTION_KM2 = 50.0


@dataclass
class VectorPath:
    points: list[tuple[float, float]] = field(default_factory=list)
    closed: bool = False
    role: str = "outer"


@dataclass
class OSMFeature:
    osm_id: str
    category: str
    name: str = ""
    paths: list[VectorPath] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)


CATEGORY_LABELS = {
    "building": "Edificaciones",
    "road": "Vialidades",
    "parking": "Estacionamientos",
    "water": "Agua",
    "vegetation": "Áreas verdes / cobertura vegetal",
    "recreation": "Parques y recreación",
    "railway": "Ferrocarril",
    "barrier": "Bardas y barreras",
    "power": "Infraestructura eléctrica",
    "structure": "Estructuras y equipamiento",
    "amenity": "Equipamiento urbano",
    "contour": "Curvas de nivel",
    "landuse": "Uso de suelo / predios",
    "other": "Otros elementos",
}


def classify_osm_tags(tags: dict[str, str]) -> str:
    if "building" in tags or tags.get("building:part"):
        return "building"
    if "highway" in tags:
        return "road"
    if tags.get("amenity") == "parking" or tags.get("parking"):
        return "parking"
    if "waterway" in tags or tags.get("natural") in {"water", "wetland", "bay"} or "water" in tags:
        return "water"
    if "railway" in tags:
        return "railway"
    if "barrier" in tags:
        return "barrier"
    if "power" in tags:
        return "power"
    if "man_made" in tags or "aeroway" in tags:
        return "structure"
    green_landuse = {"forest", "grass", "meadow", "village_green", "conservation"}
    green_natural = {"wood", "scrub", "grassland", "heath", "tree", "tree_row"}
    green_landcover = {"grass", "trees", "wood", "scrub", "forest"}
    if tags.get("landuse") in green_landuse or tags.get("natural") in green_natural or tags.get("landcover") in green_landcover:
        return "vegetation"
    if "leisure" in tags:
        return "recreation"
    if "landuse" in tags or "landcover" in tags:
        return "landuse"
    if "amenity" in tags or tags.get("tourism"):
        return "amenity"
    return "other"


_category = classify_osm_tags


def selection_area_km2(points) -> float:
    if len(points) < 3:
        return 0.0
    latitude = sum(point.latitude for point in points) / len(points)
    longitude = sum(point.longitude for point in points) / len(points)
    scale_x = 111320 * math.cos(math.radians(latitude))
    scale_y = 110574
    planar = [((point.longitude - longitude) * scale_x, (point.latitude - latitude) * scale_y) for point in points]
    area = abs(sum(planar[i][0] * planar[(i + 1) % len(planar)][1] - planar[(i + 1) % len(planar)][0] * planar[i][1] for i in range(len(planar)))) / 2
    return area / 1_000_000


def _extract_paths(geometry, role: str = "outer") -> list[VectorPath]:
    paths: list[VectorPath] = []
    if geometry.is_empty:
        return paths
    kind = geometry.geom_type
    if kind == "Point":
        paths.append(VectorPath([(geometry.y, geometry.x)], False, "point"))
    elif kind == "LineString":
        points = [(latitude, longitude) for longitude, latitude in geometry.coords]
        if len(points) >= 2:
            paths.append(VectorPath(points, False, role))
    elif kind == "Polygon":
        exterior = [(latitude, longitude) for longitude, latitude in geometry.exterior.coords]
        if len(exterior) >= 4:
            paths.append(VectorPath(exterior, True, "outer"))
        for interior in geometry.interiors:
            ring = [(latitude, longitude) for longitude, latitude in interior.coords]
            if len(ring) >= 4:
                paths.append(VectorPath(ring, True, "inner"))
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            paths.extend(_extract_paths(part, role))
    return paths


def _feature_name(tags: dict[str, str]) -> str:
    return tags.get("name") or tags.get("ref") or tags.get("addr:housenumber") or tags.get("operator") or ""


def _is_area(tags: dict[str, str], coordinates: list[tuple[float, float]]) -> bool:
    if len(coordinates) < 4 or coordinates[0] != coordinates[-1]:
        return False
    if tags.get("area") == "no":
        return False
    return bool(
        tags.get("area") == "yes"
        or "building" in tags
        or "building:part" in tags
        or "landuse" in tags
        or "landcover" in tags
        or "leisure" in tags
        or "amenity" in tags
        or "aeroway" in tags
        or tags.get("natural") in {"water", "wood", "scrub", "wetland", "grassland", "heath"}
        or "water" in tags
    )


def _number(value: str) -> float | None:
    if not value:
        return None
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
    if not match:
        return None
    number = float(match.group(0).replace(",", "."))
    lowered = value.lower()
    if "ft" in lowered or "feet" in lowered or "pie" in lowered:
        number *= 0.3048
    return number


def road_width_m(tags: dict[str, str]) -> float:
    """Returns a practical cartographic road width in metres."""
    explicit = _number(tags.get("width", ""))
    if explicit and explicit > 0:
        return max(1.0, min(explicit, 45.0))
    defaults = {
        "motorway": 22.0, "motorway_link": 9.0, "trunk": 18.0, "trunk_link": 8.0,
        "primary": 14.0, "primary_link": 7.5, "secondary": 12.0, "secondary_link": 7.0,
        "tertiary": 10.0, "tertiary_link": 6.5, "residential": 8.0, "living_street": 6.0,
        "unclassified": 7.0, "service": 5.5, "pedestrian": 5.0, "track": 4.0,
        "cycleway": 2.5, "footway": 1.8, "path": 1.5, "steps": 1.5,
    }
    width = defaults.get(tags.get("highway", ""), 6.0)
    lanes = _number(tags.get("lanes", ""))
    if lanes:
        lane_width = 3.35 if tags.get("highway") not in {"service", "residential"} else 3.0
        width = max(width, lanes * lane_width + (1.0 if lanes > 1 else 0.4))
    if tags.get("parking:lane:both") not in {None, "no", "none"}:
        width += 4.4
    return max(1.0, min(width, 45.0))


def _query(selection_text: str) -> str:
    selectors = (
        'way["highway"]', 'way["building"]', 'way["building:part"]', 'way["waterway"]',
        'way["natural"]', 'way["water"]', 'way["landuse"]', 'way["landcover"]', 'way["leisure"]',
        'way["railway"]', 'way["barrier"]', 'way["amenity"]', 'way["man_made"]',
        'way["power"]', 'way["aeroway"]', 'way["boundary"]',
        'relation["building"]', 'relation["natural"]', 'relation["water"]',
        'relation["landuse"]', 'relation["landcover"]', 'relation["leisure"]', 'relation["amenity"]',
        'relation["man_made"]', 'relation["aeroway"]',
        'node["natural"="tree"]', 'node["power"~"pole|tower|transformer"]',
        'node["man_made"~"mast|tower|water_tower|survey_point"]',
        'node["highway"="street_lamp"]',
    )
    body = "\n".join(f"  {selector}(poly:\"{selection_text}\");" for selector in selectors)
    return f"[out:json][timeout:50];\n(\n{body}\n);\nout tags geom qt;"


def _element_geometry(element: dict, tags: dict[str, str]):
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import polygonize, unary_union

    if element.get("type") == "node" and "lat" in element and "lon" in element:
        return Point(float(element["lon"]), float(element["lat"]))
    if element.get("type") == "relation":
        lines = []
        for member in element.get("members") or []:
            coordinates = [(float(node["lon"]), float(node["lat"])) for node in member.get("geometry") or [] if "lat" in node and "lon" in node]
            if len(coordinates) >= 2:
                lines.append(LineString(coordinates))
        if not lines:
            return None
        polygons = list(polygonize(unary_union(lines)))
        return unary_union(polygons) if polygons else unary_union(lines)
    geometry = element.get("geometry") or []
    coordinates = [(float(node["lon"]), float(node["lat"])) for node in geometry if "lat" in node and "lon" in node]
    if len(coordinates) < 2:
        return None
    return Polygon(coordinates) if _is_area(tags, coordinates) else LineString(coordinates)


def fetch_osm_features(selection_points, progress: Callable[[str], None] | None = None) -> list[OSMFeature]:
    notify = progress or (lambda _message: None)
    if len(selection_points) < 3:
        raise ValueError("Dibuja al menos tres vértices para delimitar el área de vectorización.")
    area_km2 = selection_area_km2(selection_points)
    if area_km2 <= 0:
        raise ValueError("El polígono de selección no tiene una superficie válida.")
    if area_km2 > MAX_SELECTION_KM2:
        raise ValueError(f"El área seleccionada es de {area_km2:.1f} km². Usa un área menor a {MAX_SELECTION_KM2:g} km².")
    try:
        from shapely.geometry import Polygon
    except ImportError as exc:
        raise RuntimeError("Falta Shapely, necesario para recortar la geometría vectorial.") from exc

    polygon_text = " ".join(f"{point.latitude:.7f} {point.longitude:.7f}" for point in selection_points)
    query = _query(polygon_text)
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    overpass_cache = cache_dir("overpass")
    cache_path = overpass_cache / f"{hashlib.sha256(query.encode('utf-8')).hexdigest()}.json"
    raw = None
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 24 * 60 * 60:
        try:
            notify("Cargando geometría desde la caché local…")
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache_path.unlink(missing_ok=True)
    errors = []
    if raw is None:
        for index, endpoint in enumerate(OVERPASS_ENDPOINTS, 1):
            notify(f"Consultando OpenStreetMap · servidor {index} de {len(OVERPASS_ENDPOINTS)}…")
            request = urllib.request.Request(
                endpoint,
                data=payload,
                headers={
                    "User-Agent": "GrupoITT-Herramientas/1.1 (+https://www.grupoitt.com)",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=65) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                cache_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                break
            except Exception as exc:
                errors.append(f"{endpoint}: {exc}")
        if raw is None:
            raise RuntimeError("No se pudo consultar la geometría de OpenStreetMap. " + " | ".join(errors))

    notify("Recortando y clasificando la geometría…")
    selection = Polygon([(point.longitude, point.latitude) for point in selection_points])
    if not selection.is_valid:
        selection = selection.buffer(0)
    if selection.is_empty:
        raise ValueError("El polígono de selección se cruza consigo mismo o no es válido.")

    features: list[OSMFeature] = []
    elements = raw.get("elements", [])
    for index, element in enumerate(elements):
        if index and index % 500 == 0:
            notify(f"Procesando detalle OSM · {index:,} de {len(elements):,} elementos…")
        tags = {str(key): str(value) for key, value in (element.get("tags") or {}).items()}
        source = _element_geometry(element, tags)
        if source is None:
            continue
        if not source.is_valid:
            source = source.buffer(0)
        clipped = source.intersection(selection)
        paths = _extract_paths(clipped)
        if paths:
            osm_type = element.get("type", "way")
            features.append(OSMFeature(f"{osm_type}/{element.get('id')}", _category(tags), _feature_name(tags), paths, tags))
    notify("Preparando capas CAD…")
    return features


def feature_counts(features: list[OSMFeature]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        counts[feature.category] = counts.get(feature.category, 0) + 1
    return counts
