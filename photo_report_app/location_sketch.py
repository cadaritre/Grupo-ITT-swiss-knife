from __future__ import annotations

import json
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .app_storage import cache_dir
from .branding import active_profile
from .osm_vector import CATEGORY_LABELS, OSMFeature, VectorPath, classify_osm_tags, feature_counts, road_width_m


TILE_SIZE = 256
DEFAULT_MAP_LAYER = "Calles - OpenStreetMap"
MAP_LAYERS = {
    DEFAULT_MAP_LAYER: {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors",
        "max_zoom": 19,
    },
    "Topográfico - OpenTopoMap": {
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": "Datos © OpenStreetMap contributors, SRTM | Mapa © OpenTopoMap (CC-BY-SA)",
        "max_zoom": 17,
    },
    "Base neutra": {
        "url": None,
        "attribution": "Base cartográfica neutra - sin teselas en línea",
        "max_zoom": 19,
    },
}
DEFAULT_CENTER = (28.632996, -106.069100)
BLUE = HexColor("#173B5F")
ACCENT = HexColor("#0B7FAB")
TEXT = HexColor("#263746")
MUTED = HexColor("#6B7C8C")
LIGHT = HexColor("#E8EFF4")


def default_layer_visibility() -> dict[str, bool]:
    return {**{category: True for category in CATEGORY_LABELS}, "selection": True, "labels": True}


def _register_pdf_fonts() -> tuple[str, str]:
    candidates = [
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/seguisb.ttf")),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            try:
                pdfmetrics.registerFont(TTFont("GrupoITT-Regular", str(regular)))
                pdfmetrics.registerFont(TTFont("GrupoITT-Bold", str(bold)))
                return "GrupoITT-Regular", "GrupoITT-Bold"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold"


PDF_REGULAR, PDF_BOLD = _register_pdf_fonts()


@dataclass
class SketchPoint:
    name: str
    latitude: float
    longitude: float
    description: str = ""


@dataclass
class SketchData:
    title: str = "Croquis de ubicación"
    client: str = ""
    project: str = ""
    location: str = ""
    sketch_date: str = field(default_factory=lambda: date.today().strftime("%d/%m/%Y"))
    geometry: str = "Polígono"
    map_layer: str = DEFAULT_MAP_LAYER
    contour_interval: str = "Automática"
    layer_visibility: dict[str, bool] = field(default_factory=default_layer_visibility)
    notes: str = ""
    points: list[SketchPoint] = field(default_factory=list)
    features: list[OSMFeature] = field(default_factory=list)
    vectorized_at: str = ""
    version: int = 5

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "SketchData":
        allowed = set(cls.__dataclass_fields__) - {"points", "features"}
        values = {key: value for key, value in raw.items() if key in allowed}
        visibility = default_layer_visibility()
        visibility.update({str(key): bool(value) for key, value in (raw.get("layer_visibility") or {}).items() if key in visibility})
        values["layer_visibility"] = visibility
        point_fields = set(SketchPoint.__dataclass_fields__)
        values["points"] = [
            SketchPoint(**{key: value for key, value in item.items() if key in point_fields})
            for item in raw.get("points", [])
        ]
        values["features"] = []
        for item in raw.get("features", []):
            paths = [VectorPath(
                points=[tuple(point) for point in path.get("points", [])],
                closed=bool(path.get("closed", False)),
                role=str(path.get("role", "outer")),
            ) for path in item.get("paths", [])]
            tags = {str(key): str(value) for key, value in item.get("tags", {}).items()}
            saved_category = str(item.get("category", "other"))
            category = saved_category if saved_category == "contour" else classify_osm_tags(tags)
            values["features"].append(OSMFeature(
                osm_id=str(item.get("osm_id", "")),
                category=category,
                name=str(item.get("name", "")),
                paths=paths,
                tags=tags,
            ))
        return cls(**values)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "SketchData":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def layer_is_visible(data: SketchData, layer: str) -> bool:
    return bool(data.layer_visibility.get(layer, True))


def visible_features(data: SketchData) -> list[OSMFeature]:
    return [feature for feature in data.features if layer_is_visible(data, feature.category)]


@dataclass
class MapSnapshot:
    image: Image.Image
    zoom: int
    left_world_px: float
    top_world_px: float
    online: bool
    layer: str

    def pixel_to_latlon(self, x: float, y: float) -> tuple[float, float]:
        world_x = (self.left_world_px + x) / TILE_SIZE
        world_y = (self.top_world_px + y) / TILE_SIZE
        return _inverse_xy(world_x, world_y, self.zoom)

    def latlon_to_pixel(self, latitude: float, longitude: float) -> tuple[float, float]:
        world_x, world_y = _xy(latitude, longitude, self.zoom)
        return world_x * TILE_SIZE - self.left_world_px, world_y * TILE_SIZE - self.top_world_px

    def center(self) -> tuple[float, float]:
        return self.pixel_to_latlon(self.image.width / 2, self.image.height / 2)


def _font(size: int, bold: bool = False):
    names = ["arialbd.ttf" if bold else "arial.ttf", "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


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


def _choose_zoom(points: list[SketchPoint], width: int, height: int) -> int:
    if not points:
        return 13
    if len(points) == 1:
        return 16
    for zoom in range(18, 3, -1):
        pixels = [(_xy(p.latitude, p.longitude, zoom)[0] * TILE_SIZE, _xy(p.latitude, p.longitude, zoom)[1] * TILE_SIZE) for p in points]
        span_x = max(x for x, _ in pixels) - min(x for x, _ in pixels)
        span_y = max(y for _, y in pixels) - min(y for _, y in pixels)
        if span_x <= width * 0.68 and span_y <= height * 0.64:
            return zoom
    return 4


@lru_cache(maxsize=256)
def _tile(x: int, y: int, zoom: int, layer: str) -> Image.Image:
    config = MAP_LAYERS.get(layer, MAP_LAYERS[DEFAULT_MAP_LAYER])
    template = config["url"]
    if not template:
        raise ValueError("La capa seleccionada no usa teselas en línea.")
    layer_key = "".join(character if character.isalnum() else "_" for character in layer).strip("_")
    cache = cache_dir("map_tiles") / layer_key / str(zoom) / str(x)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{y}.png"
    if target.exists():
        try:
            with Image.open(target) as cached:
                return cached.convert("RGB").copy()
        except Exception:
            target.unlink(missing_ok=True)
    request = urllib.request.Request(
        template.format(z=zoom, x=x, y=y, s=("a", "b", "c")[(x + y) % 3]),
        headers={"User-Agent": "GrupoITT-Herramientas/1.0 (www.grupoitt.com)"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = response.read()
    target.write_bytes(payload)
    with Image.open(BytesIO(payload)) as downloaded:
        return downloaded.convert("RGB").copy()


def _draw_fallback(canvas: Image.Image, message: str = "Mapa base no disponible - coordenadas conservadas"):
    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size
    canvas.paste("#E7EDF1", (0, 0, width, height))
    for x in range(-height, width + height, 90):
        draw.line((x, 0, x + height, height), fill="#D4DFE5", width=2)
    for x in range(0, width, 130):
        draw.line((x, 0, x, height), fill="#DCE5EA", width=1)
    for y in range(0, height, 130):
        draw.line((0, y, width, y), fill="#DCE5EA", width=1)
    draw.rounded_rectangle((20, 20, 390, 67), 10, fill="#FFFFFF", outline="#C7D4DC", width=2)
    draw.text((38, 34), message, font=_font(18, True), fill="#526575")


def _nice_scale_length(meters_per_pixel: float, target_pixels: int = 150) -> tuple[float, int]:
    target = max(1.0, meters_per_pixel * target_pixels)
    power = 10 ** math.floor(math.log10(target))
    value = target / power
    nice = 1 if value < 2 else 2 if value < 5 else 5
    meters = nice * power
    return meters, max(20, round(meters / meters_per_pixel))


def render_location_map(
    points: list[SketchPoint],
    geometry: str = "Polígono",
    layer: str = DEFAULT_MAP_LAYER,
    features: list[OSMFeature] | None = None,
    size: tuple[int, int] = (1400, 860),
    center: tuple[float, float] | None = None,
    zoom_override: int | None = None,
    draw_selection: bool = True,
) -> MapSnapshot:
    width, height = size
    config = MAP_LAYERS.get(layer, MAP_LAYERS[DEFAULT_MAP_LAYER])
    layer = layer if layer in MAP_LAYERS else DEFAULT_MAP_LAYER
    zoom = min(zoom_override if zoom_override is not None else _choose_zoom(points, width, height), config["max_zoom"])
    if points and zoom_override is None:
        projected = [_xy(p.latitude, p.longitude, zoom) for p in points]
        center_x = (min(x for x, _ in projected) + max(x for x, _ in projected)) / 2
        center_y = (min(y for _, y in projected) + max(y for _, y in projected)) / 2
        center_lat = sum(p.latitude for p in points) / len(points)
    else:
        center = center or DEFAULT_CENTER
        center_x, center_y = _xy(center[0], center[1], zoom)
        center_lat = center[0]
    left = center_x * TILE_SIZE - width / 2
    top = center_y * TILE_SIZE - height / 2
    x0, x1 = math.floor(left / TILE_SIZE), math.floor((left + width) / TILE_SIZE)
    y0, y1 = math.floor(top / TILE_SIZE), math.floor((top + height) / TILE_SIZE)
    canvas = Image.new("RGB", size, "#DCE5EA")
    online = bool(config["url"])
    try:
        if not config["url"]:
            raise ValueError("neutral")
        limit = 2**zoom
        requested_tiles = []
        for tile_x in range(x0, x1 + 1):
            for tile_y in range(y0, y1 + 1):
                if not (0 <= tile_y < limit):
                    continue
                requested_tiles.append((tile_x, tile_y))

        def fetch_tile(position):
            tile_x, tile_y = position
            source = _tile(tile_x % limit, tile_y, zoom, layer)
            paste_at = (round(tile_x * TILE_SIZE - left), round(tile_y * TILE_SIZE - top))
            return source, paste_at

        with ThreadPoolExecutor(max_workers=6) as executor:
            for source, paste_at in executor.map(fetch_tile, requested_tiles):
                canvas.paste(source, paste_at)
    except Exception:
        online = False
        message = "Base neutra seleccionada" if not config["url"] else "Mapa base no disponible - coordenadas conservadas"
        _draw_fallback(canvas, message)

    draw = ImageDraw.Draw(canvas, "RGBA")
    feature_styles = {
        "building": ((86, 96, 108, 105), (55, 65, 76, 230), 2),
        "road": ((0, 0, 0, 0), (230, 126, 34, 245), 5),
        "water": ((78, 166, 214, 100), (32, 116, 174, 235), 3),
        "vegetation": ((69, 160, 86, 75), (45, 125, 63, 210), 2),
        "recreation": ((238, 220, 160, 70), (184, 139, 39, 220), 2),
        "railway": ((0, 0, 0, 0), (124, 76, 156, 235), 4),
        "barrier": ((0, 0, 0, 0), (92, 70, 55, 225), 3),
        "parking": ((132, 146, 158, 70), (82, 96, 108, 220), 2),
        "power": ((0, 0, 0, 0), (220, 70, 45, 230), 3),
        "structure": ((120, 110, 100, 75), (82, 72, 64, 220), 2),
        "amenity": ((235, 194, 66, 70), (170, 125, 20, 220), 2),
        "contour": ((0, 0, 0, 0), (133, 94, 66, 205), 1),
        "landuse": ((194, 165, 120, 42), (145, 117, 78, 190), 2),
        "other": ((0, 0, 0, 0), (91, 112, 126, 210), 2),
    }
    for feature in features or []:
        fill, outline, line_width = feature_styles.get(feature.category, feature_styles["other"])
        if feature.category == "contour" and feature.tags.get("major") == "yes":
            line_width = 2
        for path in feature.paths:
            pixels = []
            for latitude, longitude in path.points:
                px, py = _xy(latitude, longitude, zoom)
                pixels.append((px * TILE_SIZE - left, py * TILE_SIZE - top))
            if len(pixels) == 1:
                px, py = pixels[0]
                draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=outline, outline=(255, 255, 255, 235), width=1)
                continue
            if len(pixels) < 2:
                continue
            if path.closed and len(pixels) >= 3:
                draw.polygon(pixels, fill=fill, outline=outline, width=line_width)
            else:
                draw.line(pixels, fill=outline, width=line_width, joint="curve")

    if draw_selection:
        pixel_points: list[tuple[float, float]] = []
        for point in points:
            px, py = _xy(point.latitude, point.longitude, zoom)
            pixel_points.append((px * TILE_SIZE - left, py * TILE_SIZE - top))
        if len(pixel_points) >= 2:
            route = list(pixel_points)
            if len(route) >= 3:
                if not features:
                    draw.polygon(route, fill=(11, 127, 171, 35))
                route.append(route[0])
            draw.line(route, fill=(255, 255, 255, 245), width=8, joint="curve")
            draw.line(route, fill=(11, 127, 171, 255), width=4, joint="curve")
        for x, y in pixel_points:
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=(255, 255, 255, 250), outline=(11, 127, 171, 255), width=4)

    draw.polygon(((width - 66, 30), (width - 80, 63), (width - 66, 56), (width - 52, 63)), fill="#07356F")
    draw.text((width - 66, 18), "N", anchor="mm", font=_font(19, True), fill="#07356F")
    meters_per_pixel = math.cos(math.radians(center_lat)) * 2 * math.pi * 6378137 / (TILE_SIZE * 2**zoom)
    scale_meters, scale_pixels = _nice_scale_length(meters_per_pixel)
    sx, sy = width - 45 - scale_pixels, height - 48
    draw.rectangle((sx - 9, sy - 29, width - 28, sy + 22), fill=(255, 255, 255, 220))
    draw.line((sx, sy, sx + scale_pixels, sy), fill="#173B5F", width=5)
    draw.line((sx, sy - 6, sx, sy + 6), fill="#173B5F", width=3)
    draw.line((sx + scale_pixels, sy - 6, sx + scale_pixels, sy + 6), fill="#173B5F", width=3)
    scale_label = f"{scale_meters / 1000:g} km" if scale_meters >= 1000 else f"{scale_meters:g} m"
    draw.text((sx + scale_pixels / 2, sy - 22), scale_label, anchor="mm", font=_font(16, True), fill="#173B5F")
    attribution = config["attribution"] if online or not config["url"] else f"{config['attribution']} | teselas no disponibles"
    attribution_width = min(width, max(450, round(draw.textlength(attribution, font=_font(15))) + 25))
    draw.rectangle((0, height - 28, attribution_width, height), fill=(255, 255, 255, 215))
    draw.text((12, height - 21), attribution, font=_font(15), fill="#44596B")
    return MapSnapshot(canvas, zoom, left, top, online, layer)


def _utm_zone(points: list[SketchPoint]) -> int:
    longitude = sum(point.longitude for point in points) / len(points)
    return max(1, min(60, int((longitude + 180) / 6) + 1))


def latlon_to_utm(latitude: float, longitude: float, zone: int | None = None) -> tuple[float, float, int, str]:
    zone = zone or max(1, min(60, int((longitude + 180) / 6) + 1))
    a = 6378137.0
    ecc_sq = 0.00669437999014
    k0 = 0.9996
    lat_rad = math.radians(latitude)
    lon_rad = math.radians(longitude)
    lon_origin = math.radians((zone - 1) * 6 - 180 + 3)
    ecc_prime_sq = ecc_sq / (1 - ecc_sq)
    n = a / math.sqrt(1 - ecc_sq * math.sin(lat_rad) ** 2)
    t = math.tan(lat_rad) ** 2
    c = ecc_prime_sq * math.cos(lat_rad) ** 2
    aa = math.cos(lat_rad) * (lon_rad - lon_origin)
    m = a * (
        (1 - ecc_sq / 4 - 3 * ecc_sq**2 / 64 - 5 * ecc_sq**3 / 256) * lat_rad
        - (3 * ecc_sq / 8 + 3 * ecc_sq**2 / 32 + 45 * ecc_sq**3 / 1024) * math.sin(2 * lat_rad)
        + (15 * ecc_sq**2 / 256 + 45 * ecc_sq**3 / 1024) * math.sin(4 * lat_rad)
        - (35 * ecc_sq**3 / 3072) * math.sin(6 * lat_rad)
    )
    easting = k0 * n * (aa + (1 - t + c) * aa**3 / 6 + (5 - 18 * t + t**2 + 72 * c - 58 * ecc_prime_sq) * aa**5 / 120) + 500000
    northing = k0 * (m + n * math.tan(lat_rad) * (aa**2 / 2 + (5 - t + 9 * c + 4 * c**2) * aa**4 / 24 + (61 - 58 * t + t**2 + 600 * c - 330 * ecc_prime_sq) * aa**6 / 720))
    hemisphere = "N" if latitude >= 0 else "S"
    if latitude < 0:
        northing += 10000000
    return easting, northing, zone, hemisphere


def _draw_image_contain(canvas: Canvas, image: Image.Image, box: tuple[float, float, float, float]):
    x, y, width, height = box
    scale = min(width / image.width, height / image.height)
    draw_width, draw_height = image.width * scale, image.height * scale
    stream = BytesIO()
    image.save(stream, format="JPEG", quality=90, optimize=True)
    stream.seek(0)
    canvas.drawImage(ImageReader(stream), x + (width - draw_width) / 2, y + (height - draw_height) / 2, draw_width, draw_height)


def _draw_vector_croquis(canvas: Canvas, data: SketchData, box: tuple[float, float, float, float]):
    x, y, width, height = box
    canvas.saveState()
    canvas.setFillColor(HexColor("#F8FAFB"))
    canvas.roundRect(x, y, width, height, 6, fill=1, stroke=0)
    clip = canvas.beginPath()
    clip.rect(x + 4, y + 4, width - 8, height - 8)
    canvas.clipPath(clip, stroke=0)
    features = visible_features(data)
    if len(data.points) < 3:
        canvas.setFillColor(MUTED)
        canvas.setFont(PDF_BOLD, 11)
        canvas.drawCentredString(x + width / 2, y + height / 2 + 8, "No hay geometría vectorizada")
        canvas.setFont(PDF_REGULAR, 8)
        canvas.drawCentredString(x + width / 2, y + height / 2 - 8, "Dibuja el área y consulta OpenStreetMap antes de exportar.")
        canvas.restoreState()
        return

    zone = _utm_zone(data.points)
    projected_boundary = [latlon_to_utm(point.latitude, point.longitude, zone)[:2] for point in data.points]
    min_e = min(easting for easting, _ in projected_boundary)
    max_e = max(easting for easting, _ in projected_boundary)
    min_n = min(northing for _, northing in projected_boundary)
    max_n = max(northing for _, northing in projected_boundary)
    span_e = max(1.0, max_e - min_e)
    span_n = max(1.0, max_n - min_n)
    margin = 18
    scale = min((width - margin * 2) / span_e, (height - margin * 2) / span_n)
    offset_x = x + (width - span_e * scale) / 2
    offset_y = y + (height - span_n * scale) / 2

    def transform(latitude, longitude):
        easting, northing, _, _ = latlon_to_utm(latitude, longitude, zone)
        return offset_x + (easting - min_e) * scale, offset_y + (northing - min_n) * scale

    styles = {
        "building": (HexColor("#D9DEE2"), HexColor("#5E6871"), 0.55),
        "water": (HexColor("#CDEAF6"), HexColor("#2980B9"), 0.75),
        "vegetation": (HexColor("#DCEEDB"), HexColor("#4E9658"), 0.55),
        "recreation": (HexColor("#F5EAC7"), HexColor("#B48B27"), 0.5),
        "road": (None, HexColor("#D97718"), 1.15),
        "railway": (None, HexColor("#7D4B94"), 1.05),
        "barrier": (None, HexColor("#6E5545"), 0.75),
        "contour": (None, HexColor("#8A6248"), 0.35),
        "landuse": (HexColor("#F1E9DC"), HexColor("#9A7B54"), 0.45),
        "other": (None, HexColor("#718391"), 0.55),
    }
    ordered = sorted(features, key=lambda feature: 0 if any(path.closed for path in feature.paths) else 1)
    labelled: set[str] = set()
    label_positions: list[tuple[float, float]] = []
    labels_drawn = 0
    for feature in ordered:
        fill, stroke, line_width = styles.get(feature.category, styles["other"])
        if feature.category == "contour" and feature.tags.get("major") == "yes":
            line_width = 0.7
        for vector_path in feature.paths:
            coordinates = [transform(latitude, longitude) for latitude, longitude in vector_path.points]
            if len(coordinates) < 2:
                continue
            path = canvas.beginPath()
            path.moveTo(*coordinates[0])
            for coordinate in coordinates[1:]:
                path.lineTo(*coordinate)
            if vector_path.closed:
                path.close()
            canvas.setStrokeColor(stroke)
            canvas.setLineWidth(line_width)
            if fill and vector_path.closed:
                canvas.setFillColor(fill)
                canvas.drawPath(path, stroke=1, fill=1)
            else:
                canvas.drawPath(path, stroke=1, fill=0)
            if layer_is_visible(data, "labels") and feature.name and feature.name not in labelled and labels_drawn < 16 and len(coordinates) >= 2:
                label_x, label_y = coordinates[len(coordinates) // 2]
                if any(math.hypot(label_x - old_x, label_y - old_y) < 42 for old_x, old_y in label_positions):
                    continue
                canvas.setFillColor(TEXT)
                canvas.setFont(PDF_REGULAR, 4.8)
                canvas.drawCentredString(label_x, label_y + 2, _short(feature.name, 28))
                labelled.add(feature.name)
                label_positions.append((label_x, label_y))
                labels_drawn += 1

    if layer_is_visible(data, "selection"):
        boundary = [transform(point.latitude, point.longitude) for point in data.points]
        boundary.append(boundary[0])
        boundary_path = canvas.beginPath()
        boundary_path.moveTo(*boundary[0])
        for coordinate in boundary[1:]:
            boundary_path.lineTo(*coordinate)
        canvas.setStrokeColor(ACCENT)
        canvas.setLineWidth(1.5)
        canvas.setDash(6, 3)
        canvas.drawPath(boundary_path, stroke=1, fill=0)
        canvas.setDash()
        for point_x, point_y in boundary[:-1]:
            canvas.setFillColor(HexColor("#FFFFFF"))
            canvas.setStrokeColor(ACCENT)
            canvas.circle(point_x, point_y, 2.5, stroke=1, fill=1)

    canvas.setFillColor(BLUE)
    canvas.setFont(PDF_BOLD, 8)
    canvas.drawCentredString(x + width - 28, y + height - 18, "N")
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(1.2)
    canvas.line(x + width - 28, y + height - 47, x + width - 28, y + height - 23)
    canvas.line(x + width - 28, y + height - 23, x + width - 32, y + height - 31)
    canvas.line(x + width - 28, y + height - 23, x + width - 24, y + height - 31)
    target_meters = max(span_e, span_n) / 5
    power = 10 ** math.floor(math.log10(max(target_meters, 1)))
    scale_meters = (1 if target_meters / power < 2 else 2 if target_meters / power < 5 else 5) * power
    scale_width = scale_meters * scale
    scale_x, scale_y = x + width - 25 - scale_width, y + 18
    canvas.setLineWidth(1.5)
    canvas.line(scale_x, scale_y, scale_x + scale_width, scale_y)
    canvas.line(scale_x, scale_y - 3, scale_x, scale_y + 3)
    canvas.line(scale_x + scale_width, scale_y - 3, scale_x + scale_width, scale_y + 3)
    canvas.setFont(PDF_BOLD, 6)
    label = f"{scale_meters / 1000:g} km" if scale_meters >= 1000 else f"{scale_meters:g} m"
    canvas.drawCentredString(scale_x + scale_width / 2, scale_y + 5, label)
    canvas.setFillColor(MUTED)
    canvas.setFont(PDF_REGULAR, 5.5)
    attribution = "Datos cartográficos © OpenStreetMap contributors | ODbL"
    if any(feature.category == "contour" for feature in features):
        attribution += " | Elevación: Mapzen Terrain Tiles / AWS Open Data"
    canvas.drawString(x + 8, y + 7, attribution)
    canvas.restoreState()


def _pdf_header(canvas: Canvas, data: SketchData, logo_path: Path | None, page: int):
    company = active_profile()
    width, height = landscape(A4)
    if logo_path and logo_path.exists():
        try:
            canvas.drawImage(str(logo_path), 35, height - 70, 82, 48, preserveAspectRatio=True, mask="auto", anchor="c")
        except Exception:
            pass
    canvas.setFillColor(BLUE)
    canvas.setFont(PDF_BOLD, 15)
    canvas.drawCentredString(width / 2, height - 36, data.title or "Croquis de ubicación")
    canvas.setFillColor(ACCENT)
    canvas.setFont(PDF_BOLD, 8)
    canvas.drawCentredString(width / 2, height - 51, company.document_heading)
    canvas.setFillColor(TEXT)
    canvas.setFont(PDF_REGULAR, 8)
    canvas.drawRightString(width - 35, height - 38, data.sketch_date)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(width - 35, height - 51, f"Página {page}")
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(1.1)
    canvas.line(35, height - 76, width - 35, height - 76)


def _pdf_footer(canvas: Canvas):
    company = active_profile()
    width, _ = landscape(A4)
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(0.5)
    canvas.line(35, 31, width - 35, 31)
    canvas.setFillColor(MUTED)
    canvas.setFont(PDF_REGULAR, 7)
    canvas.drawString(35, 18, f"{company.name} | {company.website_label}")
    canvas.drawRightString(width - 35, 18, "Datos © OpenStreetMap contributors | WGS84 / UTM")


def _short(text: str, limit: int) -> str:
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def generate_sketch_pdf(data: SketchData, output: str | Path, logo_path: Path | None = None) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = landscape(A4)
    canvas = Canvas(str(target), pagesize=(width, height), pageCompression=1)
    canvas.setTitle(data.title)
    canvas.setAuthor(active_profile().name)
    _pdf_header(canvas, data, logo_path, 1)
    map_x, map_y, map_w, map_h = 35, 73, 580, 430
    _draw_vector_croquis(canvas, data, (map_x, map_y, map_w, map_h))
    panel_x, panel_w = 635, width - 670
    canvas.setFillColor(LIGHT)
    canvas.roundRect(panel_x, 73, panel_w, 430, 8, fill=1, stroke=0)
    y = 478
    features = visible_features(data)
    details = [
        ("PROYECTO", data.project),
        ("CLIENTE", data.client),
        ("UBICACIÓN", data.location),
        ("ELEMENTOS VECTORIALES", str(len(features))),
        ("CAPA DE REFERENCIA", data.map_layer),
        ("VÉRTICES DEL ÁREA", str(len(data.points))),
    ]
    contour_features = [feature for feature in features if feature.category == "contour"]
    if contour_features:
        interval = contour_features[0].tags.get("interval", data.contour_interval)
        details.append(("CURVAS DE NIVEL", f"{len(contour_features)} cotas · cada {interval} m"))
    for label, value in details:
        canvas.setFillColor(ACCENT)
        canvas.setFont(PDF_BOLD, 7)
        canvas.drawString(panel_x + 14, y, label)
        canvas.setFillColor(TEXT)
        canvas.setFont(PDF_REGULAR, 8.5)
        canvas.drawString(panel_x + 14, y - 14, _short(value or "-", 34))
        y -= 43
    if data.points:
        zone = _utm_zone(data.points)
        hemisphere = "N" if sum(p.latitude for p in data.points) / len(data.points) >= 0 else "S"
        canvas.setFillColor(ACCENT)
        canvas.setFont(PDF_BOLD, 7)
        canvas.drawString(panel_x + 14, y, "SISTEMA DE SALIDA DXF")
        canvas.setFillColor(TEXT)
        canvas.setFont(PDF_REGULAR, 8.5)
        canvas.drawString(panel_x + 14, y - 14, f"WGS84 / UTM zona {zone}{hemisphere}")
        y -= 43
    canvas.setFillColor(ACCENT)
    canvas.setFont(PDF_BOLD, 7)
    canvas.drawString(panel_x + 14, y, "NOTAS")
    canvas.setFillColor(TEXT)
    canvas.setFont(PDF_REGULAR, 8)
    note = _short(data.notes or "Sin observaciones.", 150)
    words = note.split()
    lines, line = [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) > 34:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    for row in lines[:5]:
        canvas.drawString(panel_x + 14, y - 14, row)
        y -= 12
    _pdf_footer(canvas)
    canvas.showPage()

    if data.points:
        zone = _utm_zone(data.points)
        rows_per_page = 17
        for start in range(0, len(data.points), rows_per_page):
            page = 2 + start // rows_per_page
            _pdf_header(canvas, data, logo_path, page)
            canvas.setFillColor(BLUE)
            canvas.setFont(PDF_BOLD, 11)
            canvas.drawString(35, height - 99, "VÉRTICES DEL ÁREA DE SELECCIÓN")
            headers = ["#", "VÉRTICE", "DESCRIPCIÓN", "LATITUD", "LONGITUD", "ESTE (m)", "NORTE (m)", "ZONA"]
            widths = [30, 80, 198, 85, 85, 92, 92, 55]
            x_positions = [35]
            for cell_width in widths:
                x_positions.append(x_positions[-1] + cell_width)
            y_top = height - 118
            row_height = 23
            canvas.setFillColor(BLUE)
            canvas.rect(35, y_top - row_height, sum(widths), row_height, fill=1, stroke=0)
            canvas.setFillColor(HexColor("#FFFFFF"))
            canvas.setFont(PDF_BOLD, 7.2)
            for index, header in enumerate(headers):
                canvas.drawCentredString(x_positions[index] + widths[index] / 2, y_top - 15, header)
            for row_index, point in enumerate(data.points[start : start + rows_per_page]):
                absolute = start + row_index + 1
                top = y_top - row_height * (row_index + 1)
                canvas.setFillColor(HexColor("#F4F7F9") if row_index % 2 else HexColor("#FFFFFF"))
                canvas.rect(35, top - row_height, sum(widths), row_height, fill=1, stroke=0)
                easting, northing, _, hemisphere = latlon_to_utm(point.latitude, point.longitude, zone)
                values = [
                    str(absolute), _short(point.name, 14), _short(point.description, 35),
                    f"{point.latitude:.7f}", f"{point.longitude:.7f}", f"{easting:.3f}",
                    f"{northing:.3f}", f"{zone}{hemisphere}",
                ]
                canvas.setFillColor(TEXT)
                canvas.setFont(PDF_REGULAR, 7.2)
                for column, value in enumerate(values):
                    if column in {1, 2}:
                        canvas.drawString(x_positions[column] + 4, top - 15, value)
                    else:
                        canvas.drawCentredString(x_positions[column] + widths[column] / 2, top - 15, value)
            canvas.setStrokeColor(HexColor("#B9C9D3"))
            canvas.setLineWidth(0.35)
            table_bottom = y_top - row_height * (min(rows_per_page, len(data.points) - start) + 1)
            for x in x_positions:
                canvas.line(x, table_bottom, x, y_top)
            canvas.line(x_positions[-1], table_bottom, x_positions[-1], y_top)
            canvas.setFillColor(MUTED)
            canvas.setFont(PDF_REGULAR, 7.5)
            canvas.drawString(35, table_bottom - 22, f"Sistema: WGS84 / UTM zona {zone}. Las coordenadas geográficas se conservan en grados decimales.")
            _pdf_footer(canvas)
            canvas.showPage()
    canvas.save()
    return target


def _dxf_text(value: str) -> str:
    return " ".join((value or "").replace("\r", " ").replace("\n", " ").split())


def generate_sketch_dxf(data: SketchData, output: str | Path) -> Path:
    from .dxf_export import generate_detailed_dxf
    return generate_detailed_dxf(data, output)

    if len(data.points) < 3:
        raise ValueError("Dibuja el área de selección antes de exportar el DXF.")
    if not data.features:
        raise ValueError("Vectoriza la geometría de OpenStreetMap antes de exportar el DXF.")
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    zone = _utm_zone(data.points)
    boundary = [latlon_to_utm(point.latitude, point.longitude, zone) for point in data.points]
    eastings = [value[0] for value in boundary]
    northings = [value[1] for value in boundary]
    diagonal = math.hypot(max(eastings) - min(eastings), max(northings) - min(northings))
    text_height = max(0.75, min(5.0, diagonal / 80 if diagonal else 2.5))
    lines: list[str] = []

    def pair(code, value):
        lines.extend((str(code), str(value)))

    pair(0, "SECTION")
    pair(2, "HEADER")
    pair(9, "$ACADVER")
    pair(1, "AC1009")
    pair(9, "$DWGCODEPAGE")
    pair(3, "ANSI_1252")
    pair(9, "$INSUNITS")
    pair(70, 6)
    pair(0, "ENDSEC")
    pair(0, "SECTION")
    pair(2, "TABLES")
    pair(0, "TABLE")
    pair(2, "LAYER")
    layer_colors = (
        ("LIMITE", 4), ("EDIFICIOS", 8), ("VIALIDADES", 30), ("AGUA", 5),
        ("VEGETACION", 3), ("FERROCARRIL", 6), ("BARRERAS", 33),
        ("OTROS", 9), ("TEXTOS", 7), ("DATOS", 2),
    )
    pair(70, len(layer_colors))
    for name, color in layer_colors:
        pair(0, "LAYER")
        pair(2, name)
        pair(70, 0)
        pair(62, color)
        pair(6, "CONTINUOUS")
    pair(0, "ENDTAB")
    pair(0, "ENDSEC")
    pair(0, "SECTION")
    pair(2, "ENTITIES")

    def polyline(layer_name: str, coordinates, closed: bool):
        if len(coordinates) < 2:
            return
        if closed and len(coordinates) > 2 and coordinates[0][:2] == coordinates[-1][:2]:
            coordinates = coordinates[:-1]
        pair(0, "POLYLINE")
        pair(8, layer_name)
        pair(66, 1)
        pair(70, 1 if closed and len(coordinates) >= 3 else 0)
        for easting, northing, *_ in coordinates:
            pair(0, "VERTEX")
            pair(8, layer_name)
            pair(10, f"{easting:.3f}")
            pair(20, f"{northing:.3f}")
            pair(30, "0.0")
        pair(0, "SEQEND")
        pair(8, layer_name)

    polyline("LIMITE", boundary, True)
    category_layers = {
        "building": "EDIFICIOS", "road": "VIALIDADES", "water": "AGUA",
        "vegetation": "VEGETACION", "railway": "FERROCARRIL",
        "barrier": "BARRERAS", "other": "OTROS",
    }
    labelled: set[str] = set()
    for feature in data.features:
        layer_name = category_layers.get(feature.category, "OTROS")
        feature_coordinates = []
        for vector_path in feature.paths:
            coordinates = [latlon_to_utm(latitude, longitude, zone) for latitude, longitude in vector_path.points]
            polyline(layer_name, coordinates, vector_path.closed)
            if not feature_coordinates and coordinates:
                feature_coordinates = coordinates
        if feature.name and feature.name not in labelled and feature_coordinates:
            easting, northing, *_ = feature_coordinates[len(feature_coordinates) // 2]
            pair(0, "TEXT")
            pair(8, "TEXTOS")
            pair(10, f"{easting:.3f}")
            pair(20, f"{northing:.3f}")
            pair(30, "0.0")
            pair(40, f"{text_height * 0.72:.3f}")
            pair(1, _dxf_text(feature.name))
            pair(50, "0.0")
            labelled.add(feature.name)
    min_e, max_n = min(eastings), max(northings)
    title_y = max_n + text_height * 4
    for row, text in enumerate((
        data.title or "Croquis de ubicacion",
        f"{data.project} | {data.location}".strip(" |"),
        f"{len(data.features)} elementos OSM | WGS84 / UTM zona {zone}{boundary[0][3]}",
        "Datos cartograficos: OpenStreetMap contributors | ODbL",
    )):
        if not text:
            continue
        pair(0, "TEXT")
        pair(8, "DATOS")
        pair(10, f"{min_e:.3f}")
        pair(20, f"{title_y - row * text_height * 1.5:.3f}")
        pair(30, "0.0")
        pair(40, f"{text_height * (1.25 if row == 0 else 0.85):.3f}")
        pair(1, _dxf_text(text))
        pair(50, "0.0")
    pair(0, "ENDSEC")
    pair(0, "EOF")
    target.write_text("\n".join(lines) + "\n", encoding="cp1252", errors="replace")
    return target
