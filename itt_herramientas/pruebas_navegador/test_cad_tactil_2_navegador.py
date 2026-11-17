"""CAD · «Modo táctil», segunda vuelta (pedidos del dueño del 17-11-2026), con el editor real, Chromium y WebKit, toque emulado.

- Toques: un toque elige al momento; toque + tocar otra vez y arrastrar = recuadro (ventana / cruce) que suma a la selección, con el
  plano corriéndose solo junto al borde; tres toques = ver todo, deshaciendo lo que cambiaron los primeros; los casi-gestos (segundo
  toque lejos o tarde) son un toque o un arrastre nuevos.
- Entrada dinámica en la lectura: X/Y con valores UTM completos, distancia y azimut (también negativos), ancho y alto, ángulo de giro y
  distancia del desfase; el valor queda fijo hasta poner el punto y se puede soltar.
- Referencias a objetos de la persona (botón del imán): todas / ninguna / por tipo, recordadas; con un editor sin `modes`, el filtro de
  la página.
- Con un editor que tiene las ops nuevas (tv-v2026.45-9): explotar, recortar y alargar (cada uno un paso de deshacer), referencias en
  bloques girados, reflejados, anidados y a escala, cotas del tamaño de la vista y ver/mover/elegir sin «Sin guardar». Con uno sin ellas
  (el fijado tv-v2026.45-8) esas pruebas se saltan y las herramientas no se muestran.

Plano propio sintético en UTM (E 395 000, N 3 175 000). Corre bajo el candado de la máquina compartida, un navegador a la vez.
"""
from __future__ import annotations

import io
import math
import os
import uuid

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio

from .test_cad_navegador import LARGO, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_cad_tactil_navegador import X0, Y0, _din, _escribir, _herramienta, abrir, entidades, llevar_punta, plano_utm, poner, punto_en_pantalla, tactil  # noqa: F401
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
MOTORES = ["chromium", "webkit"]


def _guardar(usuario, doc, nombre):
    s = io.StringIO()
    doc.write(s)
    t = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    t.write_bytes(s.getvalue().encode())
    return servicio.crear(usuario, t, nombre, "", {"tipo": "prueba"})


def plano_cuadros(usuario):
    """Tres cuadros de 4 m separados 10 m en una fila (A, B, C) y uno lejos (D, 60 m al este): para recuadros y toques."""
    import ezdxf

    d = ezdxf.new("R2018", setup=True)
    m = d.modelspace()
    for i, x in enumerate((0, 10, 20, 60)):
        m.add_lwpolyline([(X0 + x, Y0), (X0 + x + 4, Y0), (X0 + x + 4, Y0 + 4), (X0 + x, Y0 + 4)], close=True, dxfattribs={"layer": f"C{i}"})
    m.add_line((X0 - 2, Y0 + 2), (X0 + 26, Y0 + 2), dxfattribs={"layer": "EJE"})       # cruza A, B y C
    return _guardar(usuario, d, "Cuadros")


def plano_bloques(usuario):
    """Un bloque (un cuadro de 2 × 1 m y una diagonal, con un bloque anidado: un círculo) insertado plano, girado 30°, reflejado (X −1),
    a escala no uniforme (2 × 3) y anidado en otro bloque; más un cruce para recortar y una línea corta para alargar."""
    import ezdxf

    d = ezdxf.new("R2018", setup=True)
    hijo = d.blocks.new("HIJO")
    hijo.add_circle((0, 0), 0.25)
    b = d.blocks.new("CAJA")
    b.add_lwpolyline([(0, 0), (2, 0), (2, 1), (0, 1)], close=True)
    b.add_line((0, 0), (2, 1))
    b.add_blockref("HIJO", (1, 0.5))
    padre = d.blocks.new("PADRE")
    padre.add_blockref("CAJA", (1, 1), dxfattribs={"rotation": 90})
    m = d.modelspace()
    m.add_blockref("CAJA", (X0, Y0))
    m.add_blockref("CAJA", (X0 + 10, Y0), dxfattribs={"rotation": 30})
    m.add_blockref("CAJA", (X0 + 20, Y0), dxfattribs={"xscale": -1})
    m.add_blockref("CAJA", (X0 + 30, Y0), dxfattribs={"xscale": 2, "yscale": 3})
    m.add_blockref("PADRE", (X0 + 40, Y0))
    m.add_line((X0, Y0 + 10), (X0 + 10, Y0 + 10))          # recortar: la parte de la derecha de la cruz
    m.add_line((X0 + 5, Y0 + 5), (X0 + 5, Y0 + 15))
    m.add_line((X0 + 20, Y0 + 10), (X0 + 24, Y0 + 10))     # alargar hasta la vertical de X0 + 30
    m.add_line((X0 + 30, Y0 + 5), (X0 + 30, Y0 + 15))
    return _guardar(usuario, d, "Bloques")


def _sel(p):
    return sorted(p.evaluate("TVCadTactil.seleccion()"))


def _capas_sel(p):
    return sorted(p.evaluate("""TVCad.pedir({op: 'query', handles: TVCadTactil.seleccion(), detail: 'summary', limit: 50})
      .then(r => r.entities.map(e => e.layer))"""))


def _pantalla(p, x, y):
    return punto_en_pantalla(p, x, y)


def _sin_errores(n, motor):
    propios = [e for e in n.errores if not (motor == "webkit" and "Refused to apply a stylesheet" in e)]
    assert not propios, propios


def _requiere(p, *caps):
    c = p.evaluate("TVCadTactil.capacidades()")
    falta = [k for k in caps if not c.get(k)]
    if falta:
        pytest.skip(f"el editor en uso no tiene {', '.join(falta)} (hace falta tv-v2026.45-9)")


# -- Toques: una sola máquina ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_un_toque_elige_al_momento_y_tres_toques_ven_todo_sin_cambiar_la_seleccion(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_cuadros(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    ax, ay = _pantalla(p, X0 + 4, Y0 + 3)                        # lado derecho de A
    # Un toque: elegido sin esperar al posible segundo toque (antes se esperaban 320 ms).
    p.evaluate("([x, y]) => __g.tocar(x, y)", [ax, ay])
    esperar(p, "TVCadTactil.seleccion().length === 1", 300)
    assert _capas_sel(p) == ["C0"]
    # Tres toques sobre B: ver todo; la selección queda como estaba antes de los toques (A).
    v0 = p.evaluate("TVCadTactil.vista()")
    bx, by = _pantalla(p, X0 + 14, Y0 + 3)
    p.evaluate("([x, y]) => __g.toques(x, y, 3)", [bx, by])
    p.wait_for_timeout(1500)
    assert _capas_sel(p) == ["C0"]
    v1 = p.evaluate("TVCadTactil.vista()")
    assert v1["k"] != v0["k"] or v1["cx"] != v0["cx"]
    # Ver todo: también D (60 m al este) queda en la pantalla.
    dx, dy = _pantalla(p, X0 + 62, Y0 + 2)
    assert v1["ox"] < dx < v1["ox"] + v1["w"]
    # Dos toques sobre B: B entra y sale (cada toque actúa); la vista no cambia.
    bx, by = _pantalla(p, X0 + 14, Y0 + 3)
    p.evaluate("([x, y]) => __g.toques(x, y, 2)", [bx, by])
    p.wait_for_timeout(800)
    assert _capas_sel(p) == ["C0"] and p.evaluate("TVCadTactil.vista()")["k"] == v1["k"]
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_toque_y_arrastre_es_ventana_o_cruce_y_suma_a_la_seleccion(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_cuadros(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    # Primero D elegido con un toque.
    dx, dy = _pantalla(p, X0 + 64, Y0 + 3)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [dx, dy])
    esperar(p, "TVCadTactil.seleccion().length === 1", 2000)
    # Ventana (izquierda → derecha) de A y B enteros: no entra el eje (sale del recuadro) ni C. El primer toque cayó en el vacío y
    # habría quitado la selección: se deshace antes del recuadro, así D sigue.
    x1, y1 = _pantalla(p, X0 - 1, Y0 + 5)
    x2, y2 = _pantalla(p, X0 + 15, Y0 - 1)
    vista = p.evaluate("TVCadTactil.vista()")
    p.evaluate("([x, y, dx, dy]) => __g.tocarYArrastrar(x, y, dx, dy)", [x1, y1, x2 - x1, y2 - y1])
    p.wait_for_timeout(900)
    assert _capas_sel(p) == ["C0", "C1", "C3"]
    assert p.evaluate("TVCadTactil.vista()")["cx"] == vista["cx"]               # el arrastre no movió el plano
    # Cruce (derecha → izquierda) que solo toca C y el eje: los suma.
    x1, y1 = _pantalla(p, X0 + 23, Y0 + 1)
    x2, y2 = _pantalla(p, X0 + 21, Y0 + 3)
    p.evaluate("([x, y, dx, dy]) => __g.tocarYArrastrar(x, y, dx, dy)", [x1, y1, x2 - x1, y2 - y1])
    p.wait_for_timeout(900)
    assert _capas_sel(p) == ["C0", "C1", "C2", "C3", "EJE"]
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_el_recuadro_corre_el_plano_junto_al_borde(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_cuadros(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    # Acercar a A, B y C: D queda fuera, a la derecha.
    p.evaluate("TVCad.pedir({op: 'view_set', center: [%r, %r], height: 60})" % (X0 + 12, Y0 + 2))
    p.wait_for_timeout(300)
    p.evaluate("TVCadTactil.releer()")
    v = p.evaluate("TVCadTactil.vista()")
    sx, _ = _pantalla(p, X0 + 64, Y0)
    assert sx > v["ox"] + v["w"]
    # Recuadro desde la izquierda de A hasta el borde derecho, quieto ahí: el plano se corre hasta que D entra.
    x1, y1 = _pantalla(p, X0 - 1, Y0 + 6)
    alto = _pantalla(p, X0, Y0 - 1)[1]
    borde = v["ox"] + v["w"] - 6
    p.evaluate("([x, y, dx, dy]) => __g.tocarYArrastrar(x, y, dx, dy, {quieto: 2200})", [x1, y1, borde - x1, alto - y1])
    try:
        esperar(p, "TVCadTactil.seleccion().length >= 4", 15000)
    except AssertionError:
        pass
    v2 = p.evaluate("TVCadTactil.vista()")
    assert v2["cx"] > v["cx"] + 20
    assert _capas_sel(p) == ["C0", "C1", "C2", "C3"], (v, v2, [x1, y1, borde, alto], p.locator("#tt-aviso").text_content())                                # el eje sale del recuadro por la izquierda
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_casi_gestos_segundo_toque_lejos_o_tarde(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_cuadros(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    ax, ay = _pantalla(p, X0 + 4, Y0 + 3)
    bx, by = _pantalla(p, X0 + 14, Y0 + 3)
    # Segundo toque lejos (en B): dos toques sueltos, A y B elegidos; nada de recuadro ni ver todo.
    v0 = p.evaluate("TVCadTactil.vista()")
    p.evaluate("(async ([a, b, c, d]) => { await __g.tocar(a, b); await new Promise(r => setTimeout(r, 90)); await __g.tocar(c, d); })", [ax, ay, bx, by])
    p.wait_for_timeout(700)
    assert _capas_sel(p) == ["C0", "C1"] and abs(p.evaluate("TVCadTactil.vista()")["k"] / v0["k"] - 1) < 1e-6
    # Segundo toque lejos y arrastre: mueve el plano (no hay recuadro).
    cx, cy = _pantalla(p, X0 + 30, Y0 + 20)
    p.evaluate("([x, y, x2, y2]) => __g.tocarYArrastrar(x, y, 60, 40, {x2, y2})", [ax, ay, cx, cy])
    p.wait_for_timeout(900)
    v1 = p.evaluate("TVCadTactil.vista()")
    assert abs(v1["cx"] - (v0["cx"] - 60 * v0["k"])) < v0["k"] and _capas_sel(p) == ["C1"]     # el toque en A lo quitó
    # Segundo toque tarde y arrastre: también mueve el plano.
    ax, ay = _pantalla(p, X0 + 4, Y0 + 3)
    p.evaluate("([x, y]) => __g.tocarYArrastrar(x, y, -60, 0, {pausa: 600})", [ax, ay])
    p.wait_for_timeout(900)
    v2 = p.evaluate("TVCadTactil.vista()")
    assert abs(v2["cx"] - (v1["cx"] + 60 * v1["k"])) < v1["k"] and _capas_sel(p) == ["C0", "C1"]
    # Tres toques con el tercero tarde: no es ver todo.
    p.evaluate("(async ([x, y]) => { await __g.toques(x, y, 2); await new Promise(r => setTimeout(r, 600)); await __g.tocar(x, y); })", [60, 120])
    p.wait_for_timeout(900)
    assert abs(p.evaluate("TVCadTactil.vista()")["k"] / v2["k"] - 1) < 1e-6
    _sin_errores(n, motor)


# -- Entrada dinámica -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_lectura_dinamica_linea_por_coordenada_distancia_y_azimut_negativo(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    _herramienta(p, "dibujar", "Línea")
    # X e Y completos (7 cifras y decimales): la punta va ahí mientras se escribe.
    _escribir(p, "x", "395012.125")
    expect(p.locator('.tt-valor[data-campo="x"]')).to_have_attribute("data-fijo", "1")
    assert abs(p.evaluate("TVCadTactil.puntero().mundo")[0] - 395012.125) < 1e-9
    p.locator('#tt-din [data-d="siguiente"]').click()
    _din(p, "3175020.5")
    assert p.evaluate("TVCadTactil.puntero().mundo") == [395012.125, 3175020.5]
    # El teclado deja ver la punta encima.
    tip = p.evaluate("(() => { const m = TVCadTactil.puntero().mundo; return TVCadTactil.aPantalla(m[0], m[1]); })()")
    z = p.evaluate("(() => { const r = document.getElementById('tt-zona').getBoundingClientRect(); return [r.width, r.height]; })()")
    assert 0 <= tip[0] <= z[0] and 0 <= tip[1] <= z[1]
    p.locator('#tt-din [data-d="poner"]').click()
    # Tras el primer punto: distancia y azimut; el teclado sigue abierto en «Distancia».
    expect(p.locator('.tt-valor[data-campo="d"]')).to_be_visible()
    expect(p.locator("#tt-din-campos [aria-pressed=true]")).to_have_text("Distancia")
    _din(p, "15")
    p.locator('#tt-din [data-d="siguiente"]').click()                              # Distancia → Azimut
    _din(p, "-45")
    assert p.evaluate("TVCadTactil.fijos()") == {"d": 15, "az": -45}
    p.locator('#tt-din [data-d="poner"]').click()
    # Soltar un valor con su ×: vuelve a seguir la punta.
    _escribir(p, "d", "3")
    p.locator('#tt-din [data-d="cerrar"]').click()
    p.locator('.tt-valor[data-campo="d"] .tt-valor__quitar').click()
    assert p.evaluate("TVCadTactil.fijos()") == {}
    puntos = p.evaluate("TVCadTactil.puntos()")
    assert puntos[0] == [395012.125, 3175020.5]
    a = math.radians(-45)
    assert abs(puntos[1][0] - (395012.125 + 15 * math.sin(a))) < 1e-8 and abs(puntos[1][1] - (3175020.5 + 15 * math.cos(a))) < 1e-8
    p.locator("#tt-terminar").click()
    lin = p.evaluate("TVCad.pedir({op: 'query', type: 'Line', detail: 'geometry'}).then(r => r.entities.filter(e => e.start[0] === 395012.125))")
    assert len(lin) == 1 and abs(math.dist(lin[0]["start"][:2], lin[0]["end"][:2]) - 15) < 1e-8
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_lectura_dinamica_rectangulo_negativo_giro_y_un_deshacer(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    antes = entidades(p)
    _herramienta(p, "dibujar", "Rectángulo")
    llevar_punta(p, X0 + 20, Y0 + 20); poner(p)
    _escribir(p, "ancho", "-12.5")
    p.locator('#tt-din [data-d="siguiente"]').click()
    _din(p, "7")
    p.locator('#tt-din [data-d="poner"]').click()
    p.wait_for_timeout(500)
    assert entidades(p) == antes + 1
    pl = p.evaluate("TVCad.pedir({op: 'query', type: 'Polyline', detail: 'geometry'}).then(r => r.entities)")
    assert any([X0 + 7.5, Y0 + 27] in [v[:2] for v in e["vertices"]] for e in pl), pl
    # Girar el círculo 90° escribiendo el ángulo: un paso de deshacer.
    sx, sy = _pantalla(p, X0 + 45, Y0 + 10)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    expect(p.locator("#tt-seleccion")).to_be_visible()
    p.locator('#tt-seleccion [data-editar="girar"]').click()
    llevar_punta(p, X0 + 40, Y0 + 5); poner(p)                                    # el cuadrante de abajo
    _escribir(p, "ang", "90")
    p.locator('#tt-din [data-d="poner"]').click()
    p.wait_for_timeout(500)
    c = p.evaluate("TVCad.pedir({op: 'query', type: 'Circle', detail: 'geometry'}).then(r => r.entities.filter(e => e.space === '*Model_Space'))")
    assert any(abs(e["center"][0] - (X0 + 35)) < 1e-6 and abs(e["center"][1] - (Y0 + 5)) < 1e-6 for e in c), c
    p.locator("#tt-deshacer").click()
    p.wait_for_timeout(400)
    c = p.evaluate("TVCad.pedir({op: 'query', type: 'Circle', detail: 'geometry'}).then(r => r.entities.filter(e => e.space === '*Model_Space'))")
    assert any(abs(e["center"][0] - (X0 + 40)) < 1e-9 and abs(e["center"][1] - (Y0 + 10)) < 1e-9 for e in c), c
    # Ya no hay herramientas «por coordenada» ni botón «Coordenada».
    p.locator('.tt-pestanas [data-grupo="dibujar"]').click()
    hoja = p.locator("#tt-hoja")
    assert hoja.get_by_role("button", name="Punto por coordenada").count() == 0 and hoja.get_by_role("button", name="Círculo por coordenada").count() == 0
    hoja.get_by_role("button", name="Línea", exact=True).click()
    expect(p.locator("#tt-coord")).to_be_hidden()
    _sin_errores(n, motor)


# -- Referencias a objetos --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_las_referencias_de_la_persona_desde_el_iman_y_recordadas(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_cuadros(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    p.evaluate("TVCad.pedir({op: 'view_set', center: [%r, %r], height: 12})" % (X0 + 2, Y0 + 2))
    p.evaluate("TVCadTactil.releer()")
    _herramienta(p, "dibujar", "Punto")
    # La punta a 0.3 m de la esquina (X0, Y0) del cuadro: «Extremo». (Lejos del asa: tocar el asa la arrastra, no lleva la punta.)
    llevar_punta(p, X0 + 0.2, Y0 + 0.2)
    try:
        esperar(p, "TVCadTactil.puntero().snap === 'Endpoint'", 3000)
    except AssertionError:
        raise AssertionError((p.evaluate("TVCadTactil.puntero()"), p.evaluate("TVCadTactil.referencias()"), p.evaluate("TVCadTactil.capacidades()"),
                              p.evaluate("TVCad.pedir({op: 'snap', point: TVCadTactil.puntero().mundo})"))) from None
    p.locator("#tt-imanes").click()
    hoja = p.locator("#tt-hoja")
    expect(hoja).to_contain_text("Intersección aparente")
    assert hoja.locator(".tt-refs .tt-interruptor").count() == 12 and hoja.locator(".tt-ref-marca").count() == 12
    hoja.get_by_role("button", name="Ninguna").click()
    hoja.get_by_role("switch", name="Punto medio").check()
    p.locator("#tt-hoja [data-tt-cerrar]").click()
    llevar_punta(p, X0 + 0.2, Y0 + 0.2)
    p.wait_for_timeout(500)
    assert p.evaluate("TVCadTactil.puntero().snap") is None                       # sin «Extremo»
    llevar_punta(p, X0 + 2.1, Y0 + 0.1)                                         # medio del lado de abajo de A
    esperar(p, "TVCadTactil.puntero().snap === 'Midpoint'", 3000)
    # El interruptor general apaga todas.
    p.locator("#tt-imanes").click()
    p.locator("#tt-hoja").get_by_role("switch", name="Referencias a objetos").uncheck()
    p.locator("#tt-hoja [data-tt-cerrar]").click()
    llevar_punta(p, X0 + 2.2, Y0 + 0.1)
    p.wait_for_timeout(500)
    assert p.evaluate("TVCadTactil.puntero().snap") is None
    expect(p.locator("#tt-imanes")).to_have_attribute("data-apagado", "1")
    # Recordadas en este dispositivo.
    p.reload()
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    assert p.evaluate("TVCadTactil.referencias()") == {"encendidas": False, "tipos": ["Midpoint"], "candidatos": 0}
    # «Más» lleva a la misma hoja.
    p.locator("#tt-mas").click()
    p.locator("#tt-hoja").get_by_role("button", name="Referencias a objetos").click()
    expect(p.locator("#tt-hoja .tt-refs")).to_be_visible()
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_referencias_en_bloques_girados_reflejados_anidados_y_a_escala(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_bloques(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    _requiere(p, "modos")
    c30, s30 = math.cos(math.radians(30)), math.sin(math.radians(30))
    casos = [
        ((X0 + 2, Y0 + 1), "Endpoint"),                                            # plano: esquina (2, 1)
        ((X0 + 10 + 2 * c30 - s30, Y0 + 2 * s30 + c30), "Endpoint"),               # girado 30°
        ((X0 + 20 - 2, Y0 + 1), "Endpoint"),                                       # reflejado
        ((X0 + 30 + 4, Y0 + 3), "Endpoint"),                                       # escala 2 × 3
        ((X0 + 30 + 2, Y0 + 1.5), ("Center", "Midpoint")),                         # círculo anidado (centro (1, 0.5) × 2 × 3) = medio de la diagonal
        ((X0 + 40 + 1, Y0 + 1 + 2), "Endpoint"),                                   # anidado y girado 90°: (2, 0) → (0, 2) + (1, 1)
    ]
    _herramienta(p, "dibujar", "Punto")
    p.locator("#tt-imanes").click()
    p.locator("#tt-hoja").get_by_role("button", name="Todas").click()
    p.locator("#tt-hoja").get_by_role("switch", name="Cercano").uncheck()
    p.locator("#tt-hoja [data-tt-cerrar]").click()
    for (x, y), tipo in casos:
        p.evaluate("TVCad.pedir({op: 'view_set', center: [%r, %r], height: 6})" % (x, y))
        p.evaluate("TVCadTactil.releer()")
        p.wait_for_timeout(700)
        llevar_punta(p, x + 0.08, y + 0.06)
        tipos = tipo if isinstance(tipo, tuple) else (tipo,)
        try:
            esperar(p, " || ".join(f"TVCadTactil.puntero().snap === '{t}'" for t in tipos), 3000)
        except AssertionError:
            raise AssertionError((x, y, tipo, p.evaluate("TVCadTactil.puntero()"))) from None
        m = p.evaluate("TVCadTactil.puntero().mundo")
        assert abs(m[0] - x) < 1e-6 and abs(m[1] - y) < 1e-6, (x, y, m)
    _sin_errores(n, motor)


# -- Explotar, recortar, alargar (editor con las ops) -------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_explotar_un_bloque_deja_elegido_lo_nuevo_y_es_un_paso(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_bloques(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    _requiere(p, "explotar")
    antes = entidades(p)
    p.evaluate("TVCad.pedir({op: 'view_set', center: [%r, %r], height: 6})" % (X0 + 1, Y0 + 0.5))
    p.evaluate("TVCadTactil.releer()")
    p.wait_for_timeout(700)
    sx, sy = _pantalla(p, X0 + 1, Y0)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    try:
        expect(p.locator("#tt-seleccion")).to_be_visible(timeout=5000)
    except AssertionError:
        raise AssertionError(p.evaluate("([x, y]) => TVCadTactil.cercanos(x, y)", [sx, sy])) from None
    expect(p.locator("#tt-seleccion-cuenta")).to_have_text("1 objeto elegido · Bloque")
    p.locator('#tt-seleccion [data-editar="explotar"]').click()
    expect(p.locator("#tt-aviso")).to_contain_text("Explotado: 3 objetos nuevos")
    assert len(p.evaluate("TVCadTactil.seleccion()")) == 3
    assert entidades(p) == antes + 2                                                # 1 bloque → cuadro, diagonal y bloque anidado
    p.locator("#tt-seleccion-quitar").click()
    p.locator("#tt-deshacer").click()
    p.wait_for_timeout(400)
    assert entidades(p) == antes
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_recortar_y_alargar_con_toque_puntero_y_linea_cada_uno_un_paso(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_bloques(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    _requiere(p, "recortar", "alargar")
    p.evaluate("TVCad.pedir({op: 'view_set', center: [%r, %r], height: 60})" % (X0 + 15, Y0 + 10))
    p.evaluate("TVCadTactil.releer()")
    p.wait_for_timeout(700)

    def lineas():
        return p.evaluate("""TVCad.pedir({op: 'query', type: 'Line', detail: 'geometry'}).then(r => r.entities
          .filter(e => e.space === '*Model_Space').map(e => [e.start[0], e.start[1], e.end[0], e.end[1]].map(v => Math.round(v * 1e6) / 1e6)).sort())""")
    l0 = lineas()
    _herramienta(p, "editar", "Recortar")
    expect(p.locator("#tt-guia")).to_contain_text("Toca la parte que quieres quitar")
    expect(p.locator("#tt-cancelar")).to_contain_text("Listo")
    # Un toque directo en la parte derecha de la horizontal: queda de X0 a X0 + 5.
    sx, sy = _pantalla(p, X0 + 8, Y0 + 10)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    p.wait_for_timeout(700)
    l1 = lineas()
    assert [X0, Y0 + 10, X0 + 5, Y0 + 10] in l1 or [X0 + 5, Y0 + 10, X0, Y0 + 10] in l1, l1
    p.locator("#tt-deshacer").click()
    p.wait_for_timeout(400)
    assert lineas() == l0
    # Con el puntero: la punta sobre la parte de arriba de la vertical y «Recortar aquí».
    tip = _pantalla(p, X0 + 5, Y0 + 13)
    asa = p.evaluate("(() => { const v = TVCadTactil.puntero(); const [x, y] = TVCadTactil.aPantalla(v.mundo[0], v.mundo[1]); return [x, y + 88]; })()")
    p.evaluate("([x, y, dx, dy]) => __g.arrastrar(x, y, dx, dy)", [asa[0], asa[1], tip[0] - asa[0], tip[1] + 88 - asa[1]])
    p.wait_for_timeout(500)
    p.locator("#tt-confirmar").click()
    p.wait_for_timeout(700)
    assert any(abs(max(l[1], l[3]) - (Y0 + 10)) < 1e-6 and l[0] == X0 + 5 == l[2] for l in lineas()), lineas()
    p.locator("#tt-deshacer").click()
    p.wait_for_timeout(400)
    # Línea que cruza las dos partes (derecha de la horizontal y arriba de la vertical): un paso.
    a = _pantalla(p, X0 + 8, Y0 + 9)
    b = _pantalla(p, X0 + 4, Y0 + 13)
    p.evaluate("([x, y, dx, dy]) => __g.arrastrar(x, y, dx, dy)", [a[0], a[1], b[0] - a[0], b[1] - a[1]])
    p.wait_for_timeout(900)
    assert lineas() != l0
    p.locator("#tt-deshacer").click()
    p.wait_for_timeout(400)
    assert lineas() == l0
    p.locator("#tt-cancelar").click()
    # Alargar: toque cerca del extremo derecho de la línea corta, llega a la vertical de X0 + 30.
    _herramienta(p, "editar", "Alargar")
    sx, sy = _pantalla(p, X0 + 23.5, Y0 + 10)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    p.wait_for_timeout(700)
    assert [X0 + 20, Y0 + 10, X0 + 30, Y0 + 10] in lineas(), lineas()
    p.locator("#tt-deshacer").click()
    p.wait_for_timeout(400)
    assert lineas() == l0
    # Donde no hay nada: aviso claro, nada cambia.
    sx, sy = _pantalla(p, X0 + 15, Y0 + 25)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    expect(p.locator("#tt-aviso")).to_contain_text("no hay nada que alargar")
    _sin_errores(n, motor)


def plano_grande(usuario):
    """Un plano «de verdad»: 120 capas, 80 bloques con atributos y 3 000 inserciones giradas en capas distintas, y textos."""
    import ezdxf

    d = ezdxf.new("R2018", setup=True)
    m = d.modelspace()
    for i in range(120):
        d.layers.add(f"CAPA-{i:03d}", color=1 + i % 250)
    for b in range(80):
        blk = d.blocks.new(f"B{b:03d}")
        blk.add_lwpolyline([(0, 0), (2, 0), (2, 1), (0, 1)], close=True)
        blk.add_circle((1, 0.5), 0.3)
        blk.add_text(f"T{b}", dxfattribs={"height": 0.2})
        blk.add_attdef("NUM", (0, -0.4), dxfattribs={"height": 0.2})
    for i in range(3000):
        ins = m.add_blockref(f"B{i % 80:03d}", (X0 + (i % 60) * 4, Y0 + (i // 60) * 3), dxfattribs={"layer": f"CAPA-{i % 120:03d}", "rotation": (i * 7) % 360})
        ins.add_auto_attribs({"NUM": str(i)})
    for i in range(200):
        m.add_text(f"Texto {i}", dxfattribs={"height": 1, "insert": (X0 + i, Y0 - 10), "layer": f"CAPA-{i % 120:03d}"})
    return _guardar(usuario, d, "Grande")


@pytest.mark.parametrize("motor", MOTORES)
def test_un_plano_grande_que_solo_se_mira_nunca_queda_sin_guardar(tactil, navegar, crear_usuario, roles, motor):
    """El dueño (17-11): un plano con muchos bloques y capas que solo abrió, movió, acercó y en el que abrió la hoja «Dibujar» quedó
    «Sin guardar». Abrir, mover, acercar, tres toques, elegir, el puntero con referencias, el recuadro y cada hoja (Dibujar, Editar,
    Capas, Más, Referencias, Modelo y presentaciones) dejan el plano guardado, y la página no le pide al editor nada que cambie el
    dibujo (`TVCad.registro()`)."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_grande(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    _requiere(p, "modos")
    esperar_estado(p, "guardado")
    p.evaluate("__g.arrastrar(150, 300, 80, 40)")
    p.wait_for_timeout(600)
    p.evaluate("__g.pellizcar(180, 300, 80, 160)")
    p.wait_for_timeout(600)
    p.evaluate("__g.toques(200, 300, 3)")
    p.wait_for_timeout(1200)
    p.evaluate("__g.tocar(190, 330)")
    p.wait_for_timeout(500)
    p.evaluate("__g.tocarYArrastrar(60, 200, 200, 150)")
    p.wait_for_timeout(1000)
    p.evaluate("__g.mantener(200, 420)")
    p.evaluate("__g.mover(210, 430)")
    p.evaluate("__g.soltar(210, 430)")
    p.wait_for_timeout(800)
    if p.locator("#tt-seleccion-quitar").is_visible():
        p.locator("#tt-seleccion-quitar").click()
    for grupo in ("dibujar", "editar", "medir", "cota", "texto", "capas"):
        p.locator(f'.tt-pestanas [data-grupo="{grupo}"]').click()
        p.wait_for_timeout(700)
        p.locator("#tt-hoja [data-tt-cerrar]").click()
    for fila in ("Referencias a objetos", "Modelo y presentaciones"):
        p.locator("#tt-mas").click()
        p.locator("#tt-hoja").get_by_role("button", name=fila).click()
        p.wait_for_timeout(700)
        p.locator("#tt-hoja [data-tt-cerrar]").click()
    p.wait_for_timeout(4000)                                       # más que el latido, el borrador (3 s en pruebas) y la consulta de estado
    assert p.locator("#cad-estado").get_attribute("data-estado") == "guardado", p.evaluate("TVCad.registro()")
    cambian = [r for r in p.evaluate("TVCad.registro()") if r.get("op") not in ("canvas_only", "view_set", "select", "dim_style", "sysvar", "action")
               and r.get("ok") is not False]
    assert not cambian, cambian
    _sin_errores(n, motor)


# -- Cotas del tamaño de la vista --------------------------------------------------------------------------------------------------

def _cotas(p):
    """Cotas del modelo: {handle: su DIMSCALE propio (XDATA ACAD «DSTYLE», código 40) o None}."""
    es = p.evaluate("TVCad.pedir({op: 'query', type: 'Dimension', detail: 'full', limit: 50}).then(r => r.entities).catch(() => [])")
    out = {}
    for e in es:
        if e.get("space") != "*Model_Space":
            continue
        props = next(iter(e["properties"].values()))
        base = props.get("base", props)
        escala = None
        for rec in base["common"]["extended_data"]["records"]:
            if rec["application_name"] == "ACAD":
                vals = rec["values"]
                for i, v in enumerate(vals[:-1]):
                    if v.get("Integer16") == 40:
                        escala = vals[i + 1].get("Real")
        out[e["handle"]] = escala
    return out


@pytest.mark.parametrize("motor", MOTORES)
def test_las_cotas_toman_el_tamano_de_la_vista(tactil, navegar, crear_usuario, roles, motor):
    """Pedido del dueño 17-11: la misma cota puesta con 10 veces más de vista sale 10 veces más grande (letras, flechas, separaciones);
    las cotas que ya había no cambian; cada una es un paso de deshacer. Los puntos se escriben en la lectura (sin referencias). El alto
    de su texto (DIMTXT × su DIMSCALE propio) es de la serie 1, 2, 2.5, 5 × 10ⁿ."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    _requiere(p, "cotas")
    tamanos = []

    def punto(x, y):
        _escribir(p, "x", repr(x)); p.locator('#tt-din [data-d="siguiente"]').click(); _din(p, repr(y))
        p.locator('#tt-din [data-d="poner"]').click()
    for altura in (30, 300):
        p.evaluate("TVCad.pedir({op: 'view_set', center: [%r, %r], height: %r})" % (X0 + 10, Y0, altura))
        p.evaluate("TVCadTactil.releer()")
        antes = _cotas(p)
        _herramienta(p, "cota", "Cota alineada")
        punto(X0, Y0 - 1); punto(X0 + 20, Y0 - 1); punto(X0 + 10, Y0 - 9)
        p.wait_for_timeout(600)
        ahora = _cotas(p)
        nuevas = [h for h in ahora if h not in antes]
        assert len(nuevas) == 1 and all(ahora[h] == v for h, v in antes.items()), (antes, ahora)   # las de antes no cambian
        tamanos.append(ahora[nuevas[0]])
        if altura == 30:
            un = entidades(p)
            p.locator("#tt-deshacer").click(); p.wait_for_timeout(400)
            assert entidades(p) == un - 1                                                      # un paso de deshacer
            p.locator("#tt-rehacer").click(); p.wait_for_timeout(400)
    assert tamanos[0] and 7 < tamanos[1] / tamanos[0] < 14, tamanos
    estilo = p.evaluate("TVCad.pedir({op: 'dim_style'})")
    assert {round(s * estilo["dimtxt"], 9) for s in tamanos} <= {0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10}, (tamanos, estilo)   # serie redonda
    _sin_errores(n, motor)


# -- Capturas -----------------------------------------------------------------------------------------------------------------------

TAMANOS = {"telefono": (375, 812), "grande": (430, 932), "tableta": (768, 1024), "horizontal": (812, 375)}


@pytest.mark.skipif(not CAPTURAS, reason="solo con ITT_CAPTURAS")
@pytest.mark.parametrize("tam", sorted(TAMANOS))
@pytest.mark.parametrize("tema", ["claro", "oscuro"])
def test_capturas_de_las_pantallas_nuevas(tactil, navegar, crear_usuario, roles, tam, tema):
    motor = os.environ.get("ITT_MOTOR", "chromium")
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_cuadros(ana)
    n, p = abrir(navegar, ana, motor, tam=tam, tema=tema, p0=p0)
    base = f"{CAPTURAS}/tactil2-{motor}-{tam}-{tema}"
    p.wait_for_timeout(600)
    x1, y1 = _pantalla(p, X0 - 1, Y0 + 5)
    x2, y2 = _pantalla(p, X0 + 15, Y0 - 1)
    p.evaluate("([x, y, dx, dy]) => { __g.tocarYArrastrar(x, y, dx, dy, {quieto: 1500}); }", [x1, y1, (x2 - x1) * 0.8, (y2 - y1) * 0.8])
    p.wait_for_timeout(900)
    p.screenshot(path=f"{base}-1-ventana.png", caret="initial")
    p.wait_for_timeout(1500)
    x1, y1 = _pantalla(p, X0 + 23, Y0 + 1)
    p.evaluate("([x, y]) => { __g.tocarYArrastrar(x, y, -50, 25, {quieto: 1500}); }", [x1, y1])
    p.wait_for_timeout(900)
    p.screenshot(path=f"{base}-2-cruce.png", caret="initial")
    p.wait_for_timeout(1500)
    p.locator("#tt-seleccion-quitar").click()
    _herramienta(p, "dibujar", "Línea")
    llevar_punta(p, X0 + 4.2, Y0 + 0.2)
    p.wait_for_timeout(400)
    p.screenshot(path=f"{base}-3-lectura.png", caret="initial")
    _escribir(p, "x", "395004")
    p.screenshot(path=f"{base}-4-teclado-xy.png", caret="initial")
    p.locator('#tt-din [data-d="poner"]').click()
    _din(p, "12.5")
    p.wait_for_timeout(300)
    p.screenshot(path=f"{base}-5-teclado-distancia.png", caret="initial")
    p.locator('#tt-din [data-d="cerrar"]').click()
    p.wait_for_timeout(300)
    p.screenshot(path=f"{base}-6-valor-fijo.png", caret="initial")
    p.locator("#tt-imanes").click()
    p.wait_for_timeout(300)
    p.screenshot(path=f"{base}-7-referencias.png", caret="initial")
    p.locator("#tt-hoja [data-tt-cerrar]").click()
    p.locator("#tt-cancelar").click()
    p.locator('.tt-pestanas [data-grupo="editar"]').click()
    p.wait_for_timeout(300)
    p.screenshot(path=f"{base}-8-editar.png", caret="initial")
    if p.locator("#tt-hoja").get_by_role("button", name="Recortar").count():
        p.locator("#tt-hoja").get_by_role("button", name="Recortar").click()
        a = _pantalla(p, X0 + 2, Y0 + 6)
        p.evaluate("([x, y]) => { __g.pe('pointerdown', x, y, 1); for (let i = 1; i <= 8; i++) __g.pe('pointermove', x + i * 8, y + i * 9, 1); }", list(a))
        p.wait_for_timeout(300)
        p.screenshot(path=f"{base}-9-recortar.png", caret="initial")
        p.evaluate("([x, y]) => __g.pe('pointercancel', x + 64, y + 72, 1)", list(a))
    propios = [e for e in n.errores if not (motor == "webkit" and "Refused to apply a stylesheet" in e)]
    assert not propios, propios


@pytest.mark.skipif(not CAPTURAS, reason="solo con ITT_CAPTURAS")
@pytest.mark.parametrize("tam", sorted(TAMANOS))
@pytest.mark.parametrize("tema", ["claro", "oscuro"])
def test_capturas_de_explotar_y_cotas(tactil, navegar, crear_usuario, roles, tam, tema):
    motor = os.environ.get("ITT_MOTOR", "chromium")
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_bloques(ana)
    n, p = abrir(navegar, ana, motor, tam=tam, tema=tema, p0=p0)
    base = f"{CAPTURAS}/tactil2-{motor}-{tam}-{tema}"
    p.evaluate("TVCad.pedir({op: 'view_set', center: [%r, %r], height: 30})" % (X0 + 8, Y0 + 8))
    p.evaluate("TVCadTactil.releer()")
    sx, sy = _pantalla(p, X0 + 1, Y0)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    p.wait_for_timeout(900)
    p.screenshot(path=f"{base}-a-seleccion-explotar.png", caret="initial")
    if p.locator('#tt-seleccion [data-editar="explotar"]').is_visible():
        p.locator('#tt-seleccion [data-editar="explotar"]').click()
        p.wait_for_timeout(700)
        p.screenshot(path=f"{base}-b-explotado.png", caret="initial")
    if p.locator("#tt-seleccion-quitar").is_visible():
        p.locator("#tt-seleccion-quitar").click()
    _herramienta(p, "editar", "Alargar")
    p.wait_for_timeout(400)
    p.screenshot(path=f"{base}-c-alargar.png", caret="initial")
    p.locator("#tt-cancelar").click()
    _herramienta(p, "cota", "Cota alineada")
    for x, y in ((X0, Y0 + 10), (X0 + 10, Y0 + 10)):
        _escribir(p, "x", repr(x)); p.locator('#tt-din [data-d="siguiente"]').click(); _din(p, repr(y))
        p.locator('#tt-din [data-d="poner"]').click()
    p.locator('#tt-din [data-d="cerrar"]').click()
    llevar_punta(p, X0 + 5, Y0 + 13.5)
    p.wait_for_timeout(400)
    p.screenshot(path=f"{base}-d-cota.png", caret="initial")
    propios = [e for e in n.errores if not (motor == "webkit" and "Refused to apply a stylesheet" in e)]
    assert not propios, propios
