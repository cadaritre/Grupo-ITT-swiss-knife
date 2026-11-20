"""CAD · la línea elástica sale del punto de referencia en coordenadas UTM, en Chromium y WebKit.

El dueño (19-11-2026, Chrome en Windows, editor completo) dibujaba en un plano en E 387 576, N 3 168 962: el cuadro amarillo de la referencia
a objetos estaba en la intersección, pero tras el primer clic la línea elástica salía abajo y a la derecha de ella; la línea ya hecha quedaba
bien. La vista previa se armaba en f32 absoluto (un paso de f32 en ese norte es 0.25 m) en vez de relativa al ojo en f64 como lo dibujado; la
compilación tv-v2026.45-15 lo corrige en todas las vistas previas (parche 120 del editor). Aquí: dos líneas que se cruzan en un punto de esa
magnitud (fuera de la rejilla de f32), una vista de 1 mm por píxel, LINE y PLINE con el ratón desde la intersección. La línea elástica
tiene que empezar en el cuadro de la referencia y la línea hecha, también. Corre bajo el candado de la máquina compartida.
"""
from __future__ import annotations

import io
import math
import os

import numpy as np
import pytest
from PIL import Image

from itt_herramientas.cad import servicio

from .test_cad_navegador import LARGO, _errores_propios, _persona, cad, dibujar, esperar_estado, navegar  # noqa: F401 (fixtures)

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
E, N = 387576.902, 3168962.456
TOLERANCIA_PX = 2.5  # el centro del cuadro, el ancho de la línea y el suavizado; el defecto la ponía a 45 px


def pedir(p, q):
    return p.evaluate("q => TVCad.pedir(q, {tiempoMs: 60000})", q)


def _sin_avisos(p):
    """Cierra el aviso de gráficos por software (Chromium con SwiftShader), que tapa el área de dibujo."""
    for _ in range(4):
        st = pedir(p, {"op": "state"})
        if "GpuWarning" not in (st.get("tool_windows") or []) and not st.get("modal"):
            return
        try:
            pedir(p, {"op": "action", "name": "close_modal"})
        except Exception:
            pass
        p.wait_for_timeout(300)


def _a_pantalla(p, x, y):
    """Punto del mundo → píxel CSS de la página (el área de dibujo está dentro del iframe del editor)."""
    st = pedir(p, {"op": "state"})
    c = st["camera"]
    alto = 2 * c["distance"] * math.tan(c["fov_y"] / 2)
    rx, ry, rw, rh = st["viewport_rect"]
    marco = p.locator("#cad-editor").bounding_box()
    k = rh / alto
    return (marco["x"] + rx + rw / 2 + (x - c["target"][0]) * k, marco["y"] + ry + rh / 2 - (y - c["target"][1]) * k, k)


def _captura(p, nombre):
    img = Image.open(io.BytesIO(p.screenshot())).convert("RGB")
    if CAPTURAS:
        img.save(f"{CAPTURAS}/cad-vista-previa-{nombre}.png")
    return np.asarray(img).astype(int)


def _centro(mascara, cerca, radio=40):
    ys, xs = np.nonzero(mascara)
    if len(xs) == 0:
        return None
    sel = (np.abs(xs + 0.5 - cerca[0]) < radio) & (np.abs(ys + 0.5 - cerca[1]) < radio)
    if not sel.any():
        return None
    xs, ys = xs[sel] + 0.5, ys[sel] + 0.5
    return ((xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2)


def _amarillo(img):
    return (img[..., 0] > 170) & (img[..., 1] > 170) & (img[..., 2] < 110)


def _rojo(img):
    return (img[..., 0] > 150) & (img[..., 1] < 100) & (img[..., 2] < 100)


def _inicio(img, desde, hacia):
    """El extremo de la línea roja del lado de `desde` (la línea va de ahí hacia `hacia`)."""
    ys, xs = np.nonzero(_rojo(img))
    assert len(xs), "no se ve la línea roja"
    pts = np.stack([xs + 0.5, ys + 0.5], 1)
    d = np.array(hacia) - np.array(desde)
    largo = np.linalg.norm(d)
    d /= largo
    rel = pts - np.array(desde)
    # solo lo que está sobre la recta y entre el punto y el cursor (no el texto rojo de la línea de órdenes, que puede quedar
    # en la prolongación hacia atrás, ni otras marcas); el defecto la ponía a decenas de píxeles, dentro de este margen
    normal = np.array([-d[1], d[0]])
    a_lo_largo = rel @ d
    pts = pts[(np.abs(rel @ normal) < 3) & (a_lo_largo > -largo * 0.6) & (a_lo_largo < largo + 5)]
    assert len(pts), "no se ve la línea roja entre el punto y el cursor"
    t = (pts - np.array(desde)) @ d
    return pts[t <= t.min() + 1.0].mean(0)


@pytest.mark.parametrize("orden", ["LINE", "PLINE"])
@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_la_linea_elastica_sale_del_punto_de_referencia_en_utm(cad, navegar, crear_usuario, roles, motor, orden):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio en UTM", "", "dxf")
    n = navegar(ana, motor, ancho=1280, alto=860)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    _sin_avisos(p)
    dibujar(p, f"LINE {E - 2},{N} {E + 2},{N} ")
    dibujar(p, f"LINE {E},{N - 2} {E},{N + 2} ")
    dibujar(p, "CECOLOR 1")  # lo nuevo, en rojo: la línea elástica y la línea hecha se distinguen de las dos que se cruzan
    alto_px = pedir(p, {"op": "state"})["viewport_rect"][3]
    pedir(p, {"op": "view_set", "center": [E + 0.083, N + 0.051], "height": alto_px * 0.001})  # 1 mm por píxel
    p.wait_for_timeout(800)
    _sin_avisos(p)  # el aviso de gráficos por software puede aparecer después de abrir
    p.wait_for_timeout(300)
    x, y, k = _a_pantalla(p, E, N)
    assert abs(k - 1000) < 20, k  # un milímetro por píxel
    # La orden queda esperando el primer punto: se arranca sin esperar que termine.
    p.evaluate("c => { TVCad.ejecutar(c).catch(() => null); }", f"_{orden}")
    p.mouse.move(x + 30, y + 30)
    p.wait_for_timeout(300)
    p.mouse.move(x + 3, y - 2, steps=4)
    p.wait_for_timeout(700)
    antes = _captura(p, f"{motor}-{orden}-1-referencia")
    marca = _centro(_amarillo(antes), (x, y))
    assert marca, "no aparece el cuadro de la referencia en la intersección"
    assert math.dist(marca, (x, y)) < 2.5, (marca, (x, y))  # el cuadro está en la intersección
    p.mouse.click(x + 3, y - 2)
    p.wait_for_timeout(400)
    cursor = (x + 150, y - 110)
    p.mouse.move(cursor[0] - 4, cursor[1] + 4)
    p.mouse.move(*cursor, steps=3)
    p.wait_for_timeout(900)
    banda = _captura(p, f"{motor}-{orden}-2-elastica")
    inicio = _inicio(banda, marca, cursor)
    assert math.dist(inicio, marca) <= TOLERANCIA_PX, f"la línea elástica empieza a {math.dist(inicio, marca):.1f} px del punto ({inicio}, {marca})"
    p.mouse.click(*cursor)
    p.wait_for_timeout(300)
    p.keyboard.press("Escape")
    p.wait_for_timeout(300)
    p.mouse.move(cursor[0] + 200, cursor[1] + 150)
    p.wait_for_timeout(900)
    hecha = _captura(p, f"{motor}-{orden}-3-hecha")
    inicio = _inicio(hecha, marca, cursor)
    assert math.dist(inicio, marca) <= TOLERANCIA_PX, f"la línea hecha empieza a {math.dist(inicio, marca):.1f} px del punto"
    tipo = "Line" if orden == "LINE" else "Polyline"
    r = pedir(p, {"op": "query", "type": tipo, "detail": "geometry"})
    nuevas = [e for e in r["entities"] if tipo != "Line" or abs(e["start"][1] - e["end"][1]) > 0.01 and abs(e["start"][0] - e["end"][0]) > 0.01]
    assert nuevas, r
    inicio_mundo = nuevas[-1]["start"] if tipo == "Line" else nuevas[-1]["vertices"][0]
    assert abs(inicio_mundo[0] - E) < 1e-6 and abs(inicio_mundo[1] - N) < 1e-6, inicio_mundo
    assert not _errores_propios(n, motor), n.errores
