"""Civil como lo usa la persona en el escritorio: la pestaña «Civil» de la cinta del editor, sus botones y sus comandos
(static/herramientas/cad/civil-cinta.js), o la barra de respaldo cuando el editor no tiene pestañas propias.

Los botones están dentro del editor (un lienzo): se pulsan con el ratón donde el editor dice que los dibujó (`ribbon_items`, en px CSS
de su marco). Un grupo compacto (la ventana es angosta) no dibuja sus botones: entonces la acción se escribe en la línea de comandos,
como lo haría la persona.
"""
from __future__ import annotations

from .test_cad_navegador import LARGO
from .test_pwa_navegador import esperar

#: Civil ya está en la página: en la cinta del editor o en su barra.
CIVIL_LISTO = "!!document.documentElement.dataset.civil"


def pedir(p, peticion, ms=60000):
    return p.evaluate("([q, t]) => TVCad.pedir(q, {tiempoMs: t})", [peticion, ms])


def esperar_civil(p, timeout=LARGO):
    esperar(p, "window.TVCad && TVCad.estaListo()", timeout)
    esperar(p, CIVIL_LISTO, timeout)


def en_cinta(p) -> bool:
    return p.evaluate("document.documentElement.dataset.civil") == "cinta"


def items(p):
    return pedir(p, {"op": "ribbon_items"})


def comando_de(p, clave):
    return p.evaluate("k => (Object.entries(TVCivilCinta.comandos()).find(([, v]) => v === k) || [null])[0]", clave)


def clic_en_marco(p, rect, alto=0.5):
    """Un clic del ratón en `rect` ([x, y, w, h] en px CSS del marco del editor): al centro, o a `alto` de su altura."""
    b = p.locator("#cad-editor").bounding_box()
    p.mouse.click(b["x"] + rect[0] + rect[2] / 2, b["y"] + rect[1] + rect[3] * alto)


def mostrar_pestana(p):
    """La pestaña «Civil» al frente, con un clic en ella como la persona."""
    for _ in range(60):
        r = items(p)
        if r.get("active_tab") == "host:civil" and any(c.get("rect") for c in r["commands"]):
            return r
        tab = next(t for t in r["tabs"] if t["id"] == "civil")
        if tab.get("rect") and r.get("active_tab") != "host:civil":
            clic_en_marco(p, tab["rect"])
        p.wait_for_timeout(250)
    raise AssertionError(f"La pestaña «Civil» no se mostró: {items(p)}")


def foco_en_editor(p):
    """El foco en el lienzo del editor sin tocar el dibujo (para el teclado de la página del editor, p. ej. Esc)."""
    p.evaluate("() => { const m = document.getElementById('cad-editor'); m.contentWindow.focus(); m.contentDocument.querySelector('canvas').focus(); }")


def clic_en_dibujo(p):
    """Un clic de la persona en una zona vacía del dibujo (a la izquierda, lejos de una paleta) y Esc: el teclado queda en el editor."""
    area = pedir(p, {"op": "state"})["viewport_rect"]
    b = p.locator("#cad-editor").bounding_box()
    p.mouse.click(b["x"] + area[0] + area[2] * 0.3, b["y"] + area[1] + area[3] * 0.8)
    p.wait_for_timeout(200)
    p.keyboard.press("Escape")
    p.wait_for_timeout(200)


def escribir_comando(p, comando):
    if not p.evaluate("!!document.querySelector('dialog.civil-hoja--paleta[open]')"):
        clic_en_dibujo(p)
    else:
        foco_en_editor(p)
    p.keyboard.type(comando)
    p.keyboard.press("Enter")


def lugar(r, comando):
    """Dónde está dibujado el botón de `comando`: el suyo, o el de la lista desplegable que lo corre al pulsarla (su primera acción)."""
    c = next((c for c in r["commands"] if c["command"] == comando), None)
    if c and c.get("rect"):
        return c["rect"], 0.5
    for t in r["tabs"]:
        for s in t.get("splits", []):
            if s.get("rect") and s["id"].endswith(":" + comando):
                return s["rect"], 0.2                                       # la cara (su icono), no la flecha de la lista
    return None, 0


def accion(p, clave, como="clic"):
    """Abre la acción `clave` de Civil: un clic en su botón de la cinta (o su comando escrito si no se ve), su comando escrito
    (`como="comando"`) o, con la barra de respaldo, su menú. Devuelve cómo se abrió."""
    if not en_cinta(p):
        if clave == "escala":
            p.locator("[data-civil=escala]").click()
            return "barra"
        menu = p.evaluate("k => { const b = document.querySelector(`.civil-barra [data-civil=\"${k}\"]`); const d = b && b.closest('[data-civil-menu]'); return d && d.dataset.civilMenu; }", clave)
        p.locator(f'[data-civil-menu="{menu}"] > summary').click()
        lista = p.locator(f'[data-civil-menu="{menu}"] [data-civil="{clave}"]')
        if not lista.is_visible():                                           # la barra se desplazó a lo ancho: el menú, abierto en su lugar
            p.locator(f'[data-civil-menu="{menu}"]').evaluate("d => { d.open = true; }")
        lista.click(timeout=15000)
        return "barra"
    comando = comando_de(p, clave)
    assert comando, f"Civil no tiene la acción {clave}"
    if como == "clic":
        rect, alto = lugar(mostrar_pestana(p), comando)
        if rect:
            clic_en_marco(p, rect, alto)
            return "clic"
    escribir_comando(p, comando)
    return "comando"


def libre(p):
    """Civil no está trabajando (en la cinta o en la barra)."""
    return "!document.documentElement.hasAttribute('data-civil-ocupado')"
