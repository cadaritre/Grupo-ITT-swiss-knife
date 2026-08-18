from __future__ import annotations

import math
import urllib.request
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .metadata import PhotoInfo

TILE = 256
OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def _xy(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    scale = 2**zoom
    x = (lon + 180.0) / 360.0 * scale
    lat_rad = math.radians(max(-85.0511, min(85.0511, lat)))
    y = (1 - math.asinh(math.tan(lat_rad)) / math.pi) / 2 * scale
    return x, y


def _zoom(points: list[tuple[float, float]], width: int, height: int) -> int:
    if len(points) <= 1:
        return 16
    for zoom in range(18, 3, -1):
        pixels = [(_xy(a, b, zoom)[0] * TILE, _xy(a, b, zoom)[1] * TILE) for a, b in points]
        if max(x for x, _ in pixels) - min(x for x, _ in pixels) <= width * .68 and max(y for _, y in pixels) - min(y for _, y in pixels) <= height * .62:
            return zoom
    return 4


def _tile(x: int, y: int, zoom: int) -> Image.Image:
    request = urllib.request.Request(OSM_URL.format(z=zoom, x=x, y=y), headers={"User-Agent": "ReporteFotografico/1.0 (desktop app)"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return Image.open(BytesIO(response.read())).convert("RGB")


def _font(size: int, bold: bool = False):
    names = ["arialbd.ttf" if bold else "arial.ttf", "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def build_map(photos: list[PhotoInfo], size=(1400, 760)) -> tuple[Image.Image, bool]:
    located = [(p.latitude, p.longitude) for p in photos if p.has_gps]
    width, height = size
    if not located:
        canvas = Image.new("RGB", size, "#eef2f5")
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((70, 70, width - 70, height - 70), 28, fill="#ffffff", outline="#cbd5df", width=3)
        draw.ellipse((width // 2 - 45, 165, width // 2 + 45, 255), fill="#e53935")
        draw.ellipse((width // 2 - 16, 194, width // 2 + 16, 226), fill="white")
        draw.text((width // 2, 330), "No se encontraron coordenadas GPS", anchor="mm", font=_font(38, True), fill="#17324d")
        draw.text((width // 2, 395), "El reporte puede generarse normalmente. El croquis aparecerá cuando\nlas fotografías contengan metadatos de ubicación.", anchor="ma", align="center", spacing=12, font=_font(25), fill="#627487")
        return canvas, False

    zoom = _zoom(located, width, height)
    world = [_xy(lat, lon, zoom) for lat, lon in located]
    center_x = sum(x for x, _ in world) / len(world)
    center_y = sum(y for _, y in world) / len(world)
    left = center_x * TILE - width / 2
    top = center_y * TILE - height / 2
    x0, x1 = math.floor(left / TILE), math.floor((left + width) / TILE)
    y0, y1 = math.floor(top / TILE), math.floor((top + height) / TILE)
    canvas = Image.new("RGB", size, "#dfe7ec")
    try:
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                canvas.paste(_tile(tx, ty, zoom), (round(tx * TILE - left), round(ty * TILE - top)))
    except Exception:
        draw = ImageDraw.Draw(canvas)
        for x in range(0, width, 80):
            draw.line((x, 0, x, height), fill="#cad6dd", width=2)
        for y in range(0, height, 80):
            draw.line((0, y, width, y), fill="#cad6dd", width=2)
        draw.text((20, height - 38), "Mapa base no disponible - puntos GPS conservados", font=_font(20), fill="#526575")
    draw = ImageDraw.Draw(canvas, "RGBA")
    for index, photo in enumerate(photos, 1):
        if not photo.has_gps:
            continue
        px, py = _xy(photo.latitude, photo.longitude, zoom)
        x, y = px * TILE - left, py * TILE - top
        draw.ellipse((x - 25, y - 25, x + 25, y + 25), fill="#e53935", outline="white", width=5)
        draw.text((x, y - 1), str(index), anchor="mm", font=_font(20, True), fill="white")
    draw.rectangle((0, height - 32, width, height), fill=(255, 255, 255, 210))
    draw.text((12, height - 25), f"© OpenStreetMap contributors  |  {len(located)} de {len(photos)} fotografías con GPS", font=_font(16), fill="#44596b")
    return canvas, True

