"""Vista previa rasterizada de la superficie (como el escritorio): un solo PNG con TODOS los triángulos de la ventana.

Saltarse triángulos deja huecos (cada cara de Delaunay es independiente), así que los que miden menos de un píxel se pintan como un
píxel en su centro (vectorizado con NumPy) y el resto con `ImageDraw.polygon`. Los puntos y las flechas van aparte, en JSON.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw

FONDO = (0x13, 0x23, 0x31)
RAMPA = np.array([(18, 90, 125), (25, 160, 170), (138, 190, 120), (244, 190, 75), (205, 72, 58)], dtype=np.float64)       # la del escritorio
MAX_LADO = 2400


def colores_elevacion(triangulos: np.ndarray) -> np.ndarray:
    z = triangulos[:, :, 2].mean(axis=1)
    minimo, maximo = float(z.min()), float(z.max())
    razon = np.full_like(z, 0.5) if maximo == minimo else (z - minimo) / (maximo - minimo)
    posicion = np.clip(razon, 0, 1) * (len(RAMPA) - 1)
    indice = np.minimum(len(RAMPA) - 2, posicion.astype(int))
    fraccion = (posicion - indice)[:, None]
    return np.rint(RAMPA[indice] * (1 - fraccion) + RAMPA[indice + 1] * fraccion).astype(np.uint8)


def colores_rangos(asignaciones: np.ndarray, colores_hex: list[str]) -> np.ndarray:
    paleta = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in colores_hex], dtype=np.uint8)
    return paleta[np.clip(asignaciones, 0, len(paleta) - 1)]


def pintar(triangulos: np.ndarray, colores: np.ndarray | None, ventana: tuple[float, float, float, float], ancho: int, alto: int,
           color_fijo: tuple[int, int, int] | None = None, contorno: bool | None = None) -> bytes:
    """PNG de `ancho`×`alto` de la ventana (x0, y0, x1, y1) en coordenadas de dibujo. `colores`: (M,3) uint8 o `color_fijo` para todos."""
    ancho, alto = max(2, min(MAX_LADO, int(ancho))), max(2, min(MAX_LADO, int(alto)))
    x0, y0, x1, y1 = ventana
    imagen = Image.new("RGB", (ancho, alto), FONDO)
    if x1 <= x0 or y1 <= y0:
        return _png(imagen)
    xs, ys = triangulos[:, :, 0], triangulos[:, :, 1]
    dentro = (xs.max(axis=1) >= x0) & (xs.min(axis=1) <= x1) & (ys.max(axis=1) >= y0) & (ys.min(axis=1) <= y1)
    indices = np.nonzero(dentro)[0]
    if not len(indices):
        return _png(imagen)
    px = (xs[indices] - x0) / (x1 - x0) * ancho
    py = alto - (ys[indices] - y0) / (y1 - y0) * alto
    area = np.abs((px[:, 1] - px[:, 0]) * (py[:, 2] - py[:, 0]) - (px[:, 2] - px[:, 0]) * (py[:, 1] - py[:, 0])) / 2
    lienzo = np.asarray(imagen).copy()
    paleta = colores[indices] if colores is not None else np.tile(np.array(color_fijo or (60, 90, 110), dtype=np.uint8), (len(indices), 1))
    pequenos = area < 1.0
    if pequenos.any():                                                       # un píxel por triángulo diminuto: la superficie sigue completa
        cx = np.clip(px[pequenos].mean(axis=1).astype(int), 0, ancho - 1)
        cy = np.clip(py[pequenos].mean(axis=1).astype(int), 0, alto - 1)
        lienzo[cy, cx] = paleta[pequenos]
    grandes = np.nonzero(~pequenos)[0]
    imagen = Image.fromarray(lienzo)
    dibujo = ImageDraw.Draw(imagen)
    borde = (0x27, 0x49, 0x5C) if (contorno if contorno is not None else len(grandes) <= 120_000) else None
    for i in grandes.tolist():
        dibujo.polygon([(px[i, 0], py[i, 0]), (px[i, 1], py[i, 1]), (px[i, 2], py[i, 2])], fill=tuple(int(c) for c in paleta[i]), outline=borde)
    return _png(imagen)


def _png(imagen: Image.Image) -> bytes:
    b = io.BytesIO()
    imagen.save(b, "PNG", compress_level=3)
    return b.getvalue()
