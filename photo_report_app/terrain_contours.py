from __future__ import annotations

import hashlib
import json
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from .app_storage import cache_dir
from .osm_vector import OSMFeature, VectorPath, _extract_paths, selection_area_km2


TILE_SIZE = 256
TERRAIN_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRAIN_ATTRIBUTION = "Elevation data: Mapzen Terrain Tiles / AWS Open Data"


def _xy(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    scale = 2**zoom
    latitude = max(-85.0511, min(85.0511, latitude))
    x = (longitude + 180.0) / 360.0 * scale
    lat_rad = math.radians(latitude)
    y = (1 - math.asinh(math.tan(lat_rad)) / math.pi) / 2 * scale
    return x, y


def _inverse_xy(x: float, y: float, zoom: int) -> tuple[float, float]:
    scale = 2**zoom
    longitude = x / scale * 360.0 - 180.0
    latitude = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / scale))))
    return latitude, longitude


@lru_cache(maxsize=96)
def _terrain_tile(x: int, y: int, zoom: int) -> np.ndarray:
    cache = cache_dir("terrain_tiles") / str(zoom) / str(x)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{y}.png"
    payload = None
    if target.exists():
        try:
            payload = target.read_bytes()
        except OSError:
            pass
    if payload is None:
        request = urllib.request.Request(
            TERRAIN_URL.format(z=zoom, x=x, y=y),
            headers={"User-Agent": "GrupoITT-Herramientas/1.2 (+https://www.grupoitt.com)"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read()
        target.write_bytes(payload)
    with Image.open(BytesIO(payload)) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    return rgb[:, :, 0] * 256.0 + rgb[:, :, 1] + rgb[:, :, 2] / 256.0 - 32768.0


def _chosen_zoom(points) -> int:
    min_lat = min(point.latitude for point in points)
    max_lat = max(point.latitude for point in points)
    min_lon = min(point.longitude for point in points)
    max_lon = max(point.longitude for point in points)
    for zoom in range(14, 9, -1):
        x0, y1 = _xy(min_lat, min_lon, zoom)
        x1, y0 = _xy(max_lat, max_lon, zoom)
        tiles = (math.floor(x1) - math.floor(x0) + 1) * (math.floor(y1) - math.floor(y0) + 1)
        if tiles <= 16:
            return zoom
    return 9


def _auto_interval(points, minimum: float, maximum: float) -> float:
    area = selection_area_km2(points)
    relief = maximum - minimum
    if area <= 0.5 and relief <= 100:
        return 2.0
    if area <= 3 and relief <= 250:
        return 5.0
    if area <= 15 and relief <= 600:
        return 10.0
    if area <= 40 and relief <= 1200:
        return 20.0
    return 50.0


def _parse_interval(value: str, points, minimum: float, maximum: float) -> float:
    if not value or value.lower().startswith("auto"):
        return _auto_interval(points, minimum, maximum)
    try:
        return max(1.0, float(value.lower().replace("metros", "").replace("metro", "").replace("m", "").strip()))
    except ValueError:
        return _auto_interval(points, minimum, maximum)


def _edge_point(edge: int, x: int, y: int, values, level: float) -> tuple[float, float]:
    tl, tr, br, bl = values

    def fraction(a, b):
        return 0.5 if a == b else max(0.0, min(1.0, (level - a) / (b - a)))

    if edge == 0:  # top
        return x + fraction(tl, tr), y
    if edge == 1:  # right
        return x + 1, y + fraction(tr, br)
    if edge == 2:  # bottom
        return x + fraction(bl, br), y + 1
    return x, y + fraction(tl, bl)  # left


def _segments_for_level(grid: np.ndarray, level: float):
    tl = grid[:-1, :-1]
    tr = grid[:-1, 1:]
    br = grid[1:, 1:]
    bl = grid[1:, :-1]
    cases = (tl >= level).astype(np.uint8) | ((tr >= level).astype(np.uint8) << 1) | ((br >= level).astype(np.uint8) << 2) | ((bl >= level).astype(np.uint8) << 3)
    rows, columns = np.nonzero((cases != 0) & (cases != 15))
    for y, x in zip(rows.tolist(), columns.tolist()):
        values = (float(tl[y, x]), float(tr[y, x]), float(br[y, x]), float(bl[y, x]))
        crossings = []
        for edge, (a, b) in enumerate(((values[0], values[1]), (values[1], values[2]), (values[3], values[2]), (values[0], values[3]))):
            if (a < level <= b) or (b < level <= a):
                crossings.append(edge)
        if len(crossings) == 2:
            yield _edge_point(crossings[0], x, y, values, level), _edge_point(crossings[1], x, y, values, level)
        elif len(crossings) == 4:
            center_high = sum(values) / 4 >= level
            pairs = ((0, 3), (1, 2)) if center_high == (values[0] >= level) else ((0, 1), (2, 3))
            for first, second in pairs:
                yield _edge_point(first, x, y, values, level), _edge_point(second, x, y, values, level)


def _cache_path(points, interval_name: str) -> Path:
    key = "v2|" + "|".join(f"{point.latitude:.7f},{point.longitude:.7f}" for point in points) + f"|{interval_name}"
    folder = cache_dir("contours")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


def _smooth_path(path: VectorPath) -> VectorPath:
    points = list(path.points)
    if len(points) < 4:
        return path
    closed = path.closed and points[0] == points[-1]
    source = points[:-1] if closed else points
    smoothed = [] if closed else [source[0]]
    pair_count = len(source) if closed else len(source) - 1
    for index in range(pair_count):
        first = source[index]
        second = source[(index + 1) % len(source)]
        smoothed.append((first[0] * 0.75 + second[0] * 0.25, first[1] * 0.75 + second[1] * 0.25))
        smoothed.append((first[0] * 0.25 + second[0] * 0.75, first[1] * 0.25 + second[1] * 0.75))
    if closed:
        smoothed.append(smoothed[0])
    else:
        smoothed.append(source[-1])
    return VectorPath(smoothed, path.closed, path.role)


def _load_cached(path: Path) -> list[OSMFeature] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [OSMFeature(
            osm_id=item["osm_id"], category="contour", name=item.get("name", ""), tags=item.get("tags", {}),
            paths=[VectorPath([tuple(point) for point in path_data["points"]], path_data.get("closed", False), path_data.get("role", "outer")) for path_data in item.get("paths", [])],
        ) for item in raw]
    except Exception:
        return None


def fetch_elevation_contours(selection_points, interval_name: str = "Automática", progress: Callable[[str], None] | None = None) -> list[OSMFeature]:
    notify = progress or (lambda _message: None)
    cached_path = _cache_path(selection_points, interval_name)
    cached = _load_cached(cached_path)
    if cached is not None:
        notify("Cargando curvas de nivel desde la caché local…")
        return cached

    from shapely.geometry import LineString, Polygon
    from shapely.ops import linemerge, unary_union

    zoom = _chosen_zoom(selection_points)
    min_lat = min(point.latitude for point in selection_points)
    max_lat = max(point.latitude for point in selection_points)
    min_lon = min(point.longitude for point in selection_points)
    max_lon = max(point.longitude for point in selection_points)
    min_x, bottom_y = _xy(min_lat, min_lon, zoom)
    max_x, top_y = _xy(max_lat, max_lon, zoom)
    tile_x0, tile_x1 = math.floor(min_x), math.floor(max_x)
    tile_y0, tile_y1 = math.floor(top_y), math.floor(bottom_y)
    positions = [(x, y) for y in range(tile_y0, tile_y1 + 1) for x in range(tile_x0, tile_x1 + 1)]
    notify(f"Descargando modelo de elevación · {len(positions)} teselas…")
    with ThreadPoolExecutor(max_workers=6) as executor:
        tiles = list(executor.map(lambda position: _terrain_tile(position[0], position[1], zoom), positions))
    rows = []
    for tile_y in range(tile_y0, tile_y1 + 1):
        row = [tiles[positions.index((tile_x, tile_y))] for tile_x in range(tile_x0, tile_x1 + 1)]
        rows.append(np.concatenate(row, axis=1))
    mosaic = np.concatenate(rows, axis=0)

    global_left = tile_x0 * TILE_SIZE
    global_top = tile_y0 * TILE_SIZE
    crop_left = max(0, math.floor(min_x * TILE_SIZE - global_left) - 2)
    crop_right = min(mosaic.shape[1], math.ceil(max_x * TILE_SIZE - global_left) + 3)
    crop_top = max(0, math.floor(top_y * TILE_SIZE - global_top) - 2)
    crop_bottom = min(mosaic.shape[0], math.ceil(bottom_y * TILE_SIZE - global_top) + 3)
    cropped = mosaic[crop_top:crop_bottom, crop_left:crop_right]
    step = max(1, math.ceil(max(cropped.shape) / 420))
    grid = cropped[::step, ::step]
    minimum, maximum = float(np.nanmin(grid)), float(np.nanmax(grid))
    interval = _parse_interval(interval_name, selection_points, minimum, maximum)
    first = math.ceil(minimum / interval) * interval
    levels = np.arange(first, maximum + interval * 0.25, interval)
    while len(levels) > 80:
        interval *= 2
        first = math.ceil(minimum / interval) * interval
        levels = np.arange(first, maximum + interval * 0.25, interval)

    selection = Polygon([(point.longitude, point.latitude) for point in selection_points])
    if not selection.is_valid:
        selection = selection.buffer(0)
    features = []
    notify(f"Generando curvas cada {interval:g} m · elevación {minimum:.0f}–{maximum:.0f} m…")
    origin_x = global_left + crop_left
    origin_y = global_top + crop_top
    tolerance = 360 / (2**zoom * TILE_SIZE) * step * 0.18
    for index, level in enumerate(levels.tolist()):
        if index and index % 10 == 0:
            notify(f"Trazando curvas de nivel · {index} de {len(levels)} cotas…")
        segments = []
        for start, end in _segments_for_level(grid, level):
            coordinates = []
            for local_x, local_y in (start, end):
                world_x = (origin_x + local_x * step) / TILE_SIZE
                world_y = (origin_y + local_y * step) / TILE_SIZE
                latitude, longitude = _inverse_xy(world_x, world_y, zoom)
                coordinates.append((longitude, latitude))
            segments.append(LineString(coordinates))
        if not segments:
            continue
        merged = linemerge(unary_union(segments)).intersection(selection).simplify(tolerance, preserve_topology=True)
        paths = [_smooth_path(path) for path in _extract_paths(merged)]
        if paths:
            level_index = round(level / interval)
            major = level_index % 5 == 0
            features.append(OSMFeature(
                osm_id=f"terrain/{level:g}", category="contour", name=f"{level:g} m" if major else "",
                paths=paths,
                tags={"ele": f"{level:g}", "interval": f"{interval:g}", "major": "yes" if major else "no", "source": TERRAIN_ATTRIBUTION},
            ))
    cached_path.write_text(json.dumps([asdict(feature) for feature in features], ensure_ascii=False), encoding="utf-8")
    return features
