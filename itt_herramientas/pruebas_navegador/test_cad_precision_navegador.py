"""CAD · el dibujo se sigue viendo con una vista de 1 cm de alto en coordenadas UTM, en Chromium y WebKit.

El dueño veía la vista en blanco al acercarse a menos de 0.4 m (16-11-2026): WebKit redondeaba cada vértice a f32 en coordenadas UTM. La
compilación tv-v2026.45-8 dibuja relativo al ojo con precisión exacta. El punto es de prueba, de la misma magnitud que el de su plano
(E entre 2^18 y 2^19, N entre 2^21 y 2^22: el mismo paso de f32, 0.25 m en N). Corre bajo el candado de la máquina compartida.
"""
from __future__ import annotations

import io
import os

import pytest
from PIL import Image

from itt_herramientas.cad import servicio

from .test_cad_navegador import LARGO, _persona, cad, dibujar, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
E, N = 395000.0, 3175000.0


def pedir(p, q):
    return p.evaluate("q => TVCad.pedir(q, {tiempoMs: 60000})", q)


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_vista_de_un_centimetro_en_utm_se_sigue_viendo(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio en UTM", "", "dxf")
    n = navegar(ana, motor, ancho=1280, alto=800)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    # un aspa de 4 mm y un círculo de 2 mm de radio en el punto, más una línea larga que pasa por él (lo de siempre en un plano)
    dibujar(p, f"LINE {E - 0.002},{N - 0.002} {E + 0.002},{N + 0.002}")
    dibujar(p, f"LINE {E - 0.002},{N + 0.002} {E + 0.002},{N - 0.002}")
    dibujar(p, f"CIRCLE {E},{N} 0.002")
    dibujar(p, f"LINE {E - 500},{N - 0.001} {E + 500},{N - 0.001}")
    pedir(p, {"op": "view_set", "center": [E, N], "height": 0.01})
    p.wait_for_timeout(1500)
    st = pedir(p, {"op": "state"})
    c = st["camera"]
    import math

    alto = 2 * c["distance"] * math.tan(c["fov_y"] / 2)
    assert abs(alto - 0.01) < 0.002 and abs(c["target"][0] - E) < 0.001 and abs(c["target"][1] - N) < 0.001, c
    # lo que se ve en el área de dibujo: líneas del color del dibujo sobre el fondo (no un área vacía)
    x, y, w, h = st["viewport_rect"]
    marco = p.locator("#cad-editor").bounding_box()
    zona = {"x": marco["x"] + x + w * 0.2, "y": marco["y"] + y + h * 0.2, "width": w * 0.6, "height": h * 0.6}
    img = Image.open(io.BytesIO(p.screenshot(clip=zona))).convert("RGB")
    if CAPTURAS:
        img.save(f"{CAPTURAS}/cad-precision-{motor}-1cm.png")
    fondo = img.getpixel((2, 2))
    distintos = sum(1 for px in img.getdata() if sum(abs(a - b) for a, b in zip(px, fondo)) > 60)
    assert distintos > 0.002 * img.width * img.height, (motor, distintos, img.size)      # el aspa, el círculo y la línea se ven
    assert not n.errores or motor == "webkit" and all("Refused to apply a stylesheet" in e for e in n.errores), n.errores
