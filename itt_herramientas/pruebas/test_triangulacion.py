"""Triangulación, pendientes y escurrimiento: la web calcula con el dominio del escritorio. Aquí se comprueba que el resultado de la web es el
del dominio (triángulos, áreas por rango, flechas y DXF normalizados), los límites, el aislamiento entre cuentas y la vista previa."""
from __future__ import annotations

import io
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from itt_herramientas.dominio import triangulation as tri
from itt_herramientas.models import Triangulacion
from paridad import comparadores

from .conftest import subir_tus, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def topografa(crear_usuario, roles):
    u = crear_usuario("topo")
    roles.conceder(u, "herramientas", "triangulacion")
    return u


def subir(cliente, ruta: Path) -> Triangulacion:
    r, _ = subir_tus(cliente, ruta.read_bytes(), "herramientas.triangulacion_entrada", ruta.name)
    assert r.status_code == 204, getattr(r, "content", b"")
    return Triangulacion.objects.latest("creada")


def con_superficie(cliente, ruta: Path, **extra) -> Triangulacion:
    t = subir(cliente, ruta)
    assert t.estado == Triangulacion.Estado.LISTO, t.error
    campos = {"modo": "puntos", "puntos": "on", "inserts": "on", "arista_max": "120", "area_min": "0.000001", "decimales": "6"}
    campos.update(extra)
    r = cliente.post(url("triangulacion_superficie", pk=t.pk), campos)
    assert r.status_code == 302
    t.refresh_from_db()
    assert t.superficie and not t.error, t.error
    return t


def rangos_post(rangos=None):
    rangos = rangos or [asdict(r) for r in tri.clone_default_ranges()]
    return {"r_nombre": [r["name"] for r in rangos], "r_desde": [str(r["min_pct"]) for r in rangos], "r_hasta": ["" if r["max_pct"] is None else str(r["max_pct"]) for r in rangos], "r_color": [r["color_hex"] for r in rangos]}


def rangos_flujo_post():
    rangos = [asdict(r) for r in tri.clone_default_flow_ranges()]
    d = rangos_post(rangos)
    d.update({"r_factor": [str(r["length_factor"]) for r in rangos], "r_grosor": [str(r["lineweight_mm"]) for r in rangos]})
    return d


def test_solo_con_el_rol(cliente_de, usuaria, topografa, crear_usuario):
    assert cliente_de(usuaria).get(url("triangulacion")).status_code == 403
    assert cliente_de(crear_usuario("nadie")).get(url("triangulacion")).status_code == 404
    assert cliente_de(topografa).get(url("triangulacion")).status_code == 200


def test_subida_lee_el_dxf_y_cuenta_lo_que_trae(cliente_de, topografa, entradas_geo):
    t = subir(cliente_de(topografa), entradas_geo["puntos.dxf"])
    assert t.estado == Triangulacion.Estado.LISTO and t.resumen["puntos"] > 0 and t.resumen["inserts"] > 0 and t.resumen["limites"]
    assert cliente_de(topografa).get(url("triangulacion_banco", pk=t.pk)).status_code == 200


def test_rechaza_lo_que_no_es_dxf_y_dxf_sin_entidades(cliente_de, topografa, tmp_path):
    c = cliente_de(topografa)
    r, _ = subir_tus(c, b"hola", "herramientas.triangulacion_entrada", "notas.txt")
    assert r.status_code >= 400
    vacio = tmp_path / "vacio.dxf"
    import ezdxf
    ezdxf.new("R2010").saveas(vacio)
    t = subir(c, vacio)
    assert t.estado == Triangulacion.Estado.ERROR and "no hay de dónde sacar una superficie" in t.error
    r, _ = subir_tus(c, b"\x00" * 100, "herramientas.triangulacion_entrada", "binario.dxf")
    assert r.status_code >= 400


def test_mas_de_100_mb_se_rechaza_antes_de_subir(cliente_de, topografa):
    r = cliente_de(topografa).post("/subidas/", HTTP_UPLOAD_LENGTH=str(100 * 1024 * 1024 + 1), HTTP_TUS_RESUMABLE="1.0.0",
                                   HTTP_UPLOAD_METADATA="destino aGVycmFtaWVudGFzLnRyaWFuZ3VsYWNpb25fZW50cmFkYQ==,name ZC5keGY=")
    assert r.status_code in (400, 413)


def test_superficie_pendientes_y_escurrimiento_son_los_del_dominio(cliente_de, topografa, entradas_geo):
    c = cliente_de(topografa)
    t = con_superficie(c, entradas_geo["puntos.dxf"])
    directo = tri.create_tin_model(entradas_geo["puntos.dxf"], max_edge=120.0)
    assert t.superficie["puntos"] == len(directo.points) and t.superficie["triangulos"] == len(directo.triangles)
    assert t.superficie["estadisticas"] == asdict(directo.stats) and t.superficie["limites"] == pytest.approx(list(directo.bounds()))
    # Pendientes
    assert c.post(url("triangulacion_pendientes", pk=t.pk), {**rangos_post(), "decimales": "2"}).status_code == 302
    t.refresh_from_db()
    assert not t.error, t.error
    esperado = tri.analyze_slopes(directo.triangles, tri.clone_default_ranges(), t.nombre)
    assert [(r["count"], r["area_2d"], r["area_3d"]) for r in t.pendientes["rangos"]] == [(r.count, r.area_2d, r.area_3d) for r in esperado.ranges]
    assert t.pendientes["area_total"] == esperado.total_area
    assert sum(r["porcentaje"] for r in t.pendientes["rangos"]) == pytest.approx(100.0)
    # Escurrimiento
    post = {**rangos_flujo_post(), "longitud_base": "", "punta": "28", "densidad": "2", "pendiente_min": "0.5"}
    assert c.post(url("triangulacion_escurrimiento", pk=t.pk), post).status_code == 302
    t.refresh_from_db()
    assert not t.error, t.error
    flujo = tri.analyze_flow(directo.triangles, tri.clone_default_flow_ranges(), t.nombre, density=2, minimum_slope=0.5)
    assert t.escurrimiento["flechas"] == len(flujo.arrows) and t.escurrimiento["longitud_base"] == flujo.base_length
    assert [r["count"] for r in t.escurrimiento["rangos"]] == [r.count for r in flujo.ranges]


def test_los_dxf_exportados_son_identicos_a_los_del_dominio(cliente_de, topografa, entradas_geo, tmp_path):
    c = cliente_de(topografa)
    t = con_superficie(c, entradas_geo["puntos.dxf"])
    c.post(url("triangulacion_pendientes", pk=t.pk), {**rangos_post(), "decimales": "3"})
    c.post(url("triangulacion_escurrimiento", pk=t.pk), {**rangos_flujo_post(), "longitud_base": "", "punta": "28", "densidad": "1", "pendiente_min": "0.1"})
    directo = tri.create_tin_model(entradas_geo["puntos.dxf"], max_edge=120.0)
    flujo = tri.analyze_flow(directo.triangles, tri.clone_default_flow_ranges(), "n.dxf", minimum_slope=0.1)
    pend = tri.analyze_slopes(directo.triangles, tri.clone_default_ranges(), "n.dxf")
    esperados = {"tin": tri.write_tin_dxf(directo, tmp_path / "e_tin.dxf", True),
                 "pendientes": tri.write_slope_dxf(pend, tmp_path / "e_pen.dxf", True, True, 3),
                 "escurrimiento": tri.write_flow_dxf(flujo, tmp_path / "e_flu.dxf", True, True)}
    for tipo, extra in (("tin", {"conservar_puntos": "on"}), ("pendientes", {"caras": "on", "texto_pendiente": "on", "decimales": "3"}), ("escurrimiento", {"referencia_tin": "on", "texto_pendiente": "on"})):
        assert c.post(url("triangulacion_exportar", pk=t.pk), {"tipo": tipo, **extra}).status_code == 302
        r = c.get(url("triangulacion_descargar", pk=t.pk))
        assert r.status_code == 200 and r["Content-Disposition"].endswith(f'_{tri_sufijo(tipo)}.dxf"')
        web = tmp_path / f"w_{tipo}.dxf"
        web.write_bytes(b"".join(r.streaming_content))
        resultado = comparadores.comparar_dxf(web, esperados[tipo])
        assert resultado.ok, f"{tipo}: {resultado.motivo}"


def tri_sufijo(tipo):
    from itt_herramientas.triangulacion import servicio

    return servicio.SUFIJOS[tipo]


def test_triangulos_que_ya_trae_el_dxf(cliente_de, topografa, entradas_geo, tmp_path):
    c = cliente_de(topografa)
    modelo = tri.create_tin_model(entradas_geo["puntos.dxf"], max_edge=120.0)
    tin = tri.write_tin_dxf(modelo, tmp_path / "tin.dxf")
    t = subir(c, tin)
    assert t.resumen["caras_3d"] == len(modelo.triangles) and {x["nombre"] for x in t.resumen["capas"]} >= {"TIN_3DFACE"}
    assert c.post(url("triangulacion_superficie", pk=t.pk), {"modo": "capas", "capa": "TIN_3DFACE"}).status_code == 302
    t.refresh_from_db()
    assert t.superficie["origen"] == "capas" and t.superficie["triangulos"] == len(tri.extract_triangles(tin))
    assert c.post(url("triangulacion_pendientes", pk=t.pk), {**rangos_post(), "decimales": "2"}).status_code == 302
    t.refresh_from_db()
    assert t.pendientes and not t.error
    # El TIN solo se exporta cuando la superficie salió de puntos.
    c.post(url("triangulacion_exportar", pk=t.pk), {"tipo": "tin"})
    t.refresh_from_db()
    assert "a partir de puntos" in t.error


def test_cambiar_la_superficie_borra_los_analisis(cliente_de, topografa, entradas_geo):
    c = cliente_de(topografa)
    t = con_superficie(c, entradas_geo["puntos.dxf"])
    c.post(url("triangulacion_pendientes", pk=t.pk), {**rangos_post(), "decimales": "2"})
    t.refresh_from_db()
    assert t.pendientes
    c.post(url("triangulacion_superficie", pk=t.pk), {"modo": "puntos", "puntos": "on", "inserts": "on", "arista_max": "50", "area_min": "0.000001", "decimales": "6"})
    t.refresh_from_db()
    assert t.pendientes == {} and t.superficie["opciones"]["arista_max"] == 50.0


def test_validaciones_de_opciones_y_rangos(cliente_de, topografa, entradas_geo):
    c = cliente_de(topografa)
    t = subir(c, entradas_geo["puntos.dxf"])
    for malo in ({"modo": "otro"}, {"modo": "puntos"}, {"modo": "puntos", "puntos": "on", "arista_max": "-5"}, {"modo": "puntos", "puntos": "on", "decimales": "99"}):
        r = c.post(url("triangulacion_superficie", pk=t.pk), malo, follow=True)
        t.refresh_from_db()
        assert not t.superficie and not t.error                                  # nada se calculó: el aviso va en pantalla
    t = con_superficie(c, entradas_geo["puntos.dxf"])
    huecos = rangos_post([{"name": "a", "min_pct": 0, "max_pct": 5, "color_hex": "#112233"}, {"name": "b", "min_pct": 7, "max_pct": None, "color_hex": "#445566"}])
    c.post(url("triangulacion_pendientes", pk=t.pk), {**huecos, "decimales": "2"})
    t.refresh_from_db()
    assert t.pendientes == {}
    malo_color = rangos_post([{"name": "a", "min_pct": 0, "max_pct": None, "color_hex": "rojo"}])
    c.post(url("triangulacion_pendientes", pk=t.pk), {**malo_color, "decimales": "2"})
    t.refresh_from_db()
    assert t.pendientes == {}
    sin_cierre = rangos_post([{"name": "a", "min_pct": 0, "max_pct": 5, "color_hex": "#112233"}])
    c.post(url("triangulacion_pendientes", pk=t.pk), {**sin_cierre, "decimales": "2"})
    t.refresh_from_db()
    assert t.pendientes == {}
    punta = {**rangos_flujo_post(), "punta": "90"}
    r = c.post(url("triangulacion_escurrimiento", pk=t.pk), punta, follow=True)
    t.refresh_from_db()
    assert t.escurrimiento == {} and "tamaño de punta" in r.content.decode()


def test_pendientes_sin_superficie_y_aislamiento(cliente_de, topografa, entradas_geo, crear_usuario, roles):
    c = cliente_de(topografa)
    t = subir(c, entradas_geo["puntos.dxf"])
    c.post(url("triangulacion_pendientes", pk=t.pk), {**rangos_post(), "decimales": "2"})
    t.refresh_from_db()
    assert t.pendientes == {}
    t = con_superficie(c, entradas_geo["puntos.dxf"])
    otra = crear_usuario("otra")
    roles.conceder(otra, "herramientas", "triangulacion")
    co = cliente_de(otra)
    for nombre in ("triangulacion_banco", "triangulacion_descargar", "triangulacion_vista", "triangulacion_vector"):
        assert co.get(url(nombre, pk=t.pk) + "?x0=0&y0=0&x1=1&y1=1&w=10&h=10").status_code == 404, nombre
    assert co.post(url("triangulacion_borrar", pk=t.pk)).status_code == 404 and Triangulacion.objects.filter(pk=t.pk).exists()


def ventana(t, w=400, h=300):
    x0, y0, x1, y1 = t.superficie["limites"]
    return f"?x0={x0}&y0={y0}&x1={x1}&y1={y1}&w={w}&h={h}"


def test_vista_rasterizada_con_todos_los_triangulos(cliente_de, topografa, entradas_geo):
    c = cliente_de(topografa)
    t = con_superficie(c, entradas_geo["puntos.dxf"])
    r = c.get(url("triangulacion_vista", pk=t.pk) + ventana(t) + "&modo=tin")
    assert r.status_code == 200 and r["Content-Type"] == "image/png"
    imagen = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
    assert imagen.shape == (300, 400, 3)
    fondo = np.array([0x13, 0x23, 0x31])
    pintados = (imagen != fondo).any(axis=2)
    assert pintados.mean() > 0.4                                                       # la superficie cubre la ventana
    assert len({tuple(p) for p in imagen.reshape(-1, 3)[::37]}) > 5                    # rampa de elevación, no un solo color
    # Una ventana fuera de la superficie queda en el color de fondo.
    fuera = c.get(url("triangulacion_vista", pk=t.pk) + "?x0=0&y0=0&x1=10&y1=10&w=50&h=50&modo=tin")
    assert (np.asarray(Image.open(io.BytesIO(fuera.content)).convert("RGB")) == fondo).all()
    for malo in ("?x0=a&y0=0&x1=1&y1=1&w=10&h=10", "?x0=0&y0=0&x1=1&y1=1&w=1&h=10", "?x0=0&y0=0&x1=1&y1=1&w=99999&h=10", ""):
        assert c.get(url("triangulacion_vista", pk=t.pk) + malo + "&modo=tin").status_code == 404
    assert c.get(url("triangulacion_vista", pk=t.pk) + ventana(t) + "&modo=pendientes").status_code == 404       # aún no se calculan


def test_vista_de_pendientes_usa_los_colores_de_los_rangos(cliente_de, topografa, entradas_geo):
    c = cliente_de(topografa)
    t = con_superficie(c, entradas_geo["puntos.dxf"])
    c.post(url("triangulacion_pendientes", pk=t.pk), {**rangos_post(), "decimales": "2"})
    t.refresh_from_db()
    imagen = np.asarray(Image.open(io.BytesIO(c.get(url("triangulacion_vista", pk=t.pk) + ventana(t) + "&modo=pendientes").content)).convert("RGB"))
    colores = {tuple(int(r["color_hex"][i:i + 2], 16) for i in (1, 3, 5)) for r in t.pendientes["rangos"] if r["count"]}
    vistos = {tuple(p) for p in imagen.reshape(-1, 3)}
    assert colores & vistos                                                              # al menos los rangos con triángulos aparecen


def test_capa_vectorial_decimada(cliente_de, topografa, entradas_geo):
    c = cliente_de(topografa)
    t = con_superficie(c, entradas_geo["puntos.dxf"])
    j = c.get(url("triangulacion_vector", pk=t.pk) + ventana(t) + "&modo=tin").json()
    assert j["total"] == t.superficie["puntos"] and len(j["puntos"]) == t.superficie["puntos"] and j["paso"] == 1
    c.post(url("triangulacion_escurrimiento", pk=t.pk), {**rangos_flujo_post(), "longitud_base": "", "punta": "28", "densidad": "1", "pendiente_min": "0.1"})
    t.refresh_from_db()
    f = c.get(url("triangulacion_vector", pk=t.pk) + ventana(t) + "&modo=flujo").json()
    assert f["total"] == t.escurrimiento["flechas"] and all(len(x) == 9 for x in f["flechas"]) and len(f["rangos"]) == 4


def test_el_limite_de_puntos_detiene_el_trabajo_y_manda_al_modulo_local(cliente_de, topografa, entradas_geo, settings):
    from tvplataforma import ajustes

    c = cliente_de(topografa)
    t = subir(c, entradas_geo["puntos.dxf"])
    settings.TV_HERRAMIENTAS_LIMITES = {"triangulacion": {"bytes": 100 * 1024 * 1024, "puntos": 5, "triangulos": 400_000}}
    ajustes.valor.cache_clear() if hasattr(ajustes.valor, "cache_clear") else None
    c.post(url("triangulacion_superficie", pk=t.pk), {"modo": "puntos", "puntos": "on", "inserts": "on", "area_min": "0.000001", "decimales": "6"})
    t.refresh_from_db()
    assert not t.superficie and "Puntos únicos tras eliminar duplicados" in t.error and "módulo local" in t.error


def dxf_de_puntos(ruta, n):
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for i in range(n):
        msp.add_point((370000.0 + (i % 400) * 3.0, 3170000.0 + (i // 400) * 3.0, 1400.0 + (i % 17) * 0.1))
    doc.saveas(ruta)


def test_75001_puntos_se_rechazan_antes_del_trabajo_pesado(cliente_de, topografa, tmp_path):
    """El límite de la web (memoria: 768 MB de cola): 75 000 puntos únicos. Pasarse corta ANTES de calcular el Delaunay."""
    from unittest import mock

    ruta = tmp_path / "justo_arriba.dxf"
    dxf_de_puntos(ruta, 75_001)
    c = cliente_de(topografa)
    t = subir(c, ruta)
    assert t.estado == Triangulacion.Estado.LISTO and t.resumen["puntos"] == 75_001
    with mock.patch("shapely.delaunay_triangles", side_effect=AssertionError("no debía llegar al Delaunay")):
        c.post(url("triangulacion_superficie", pk=t.pk), {"modo": "puntos", "puntos": "on", "area_min": "0.000001", "decimales": "6"})
    t.refresh_from_db()
    assert not t.superficie
    assert "75,001" in t.error and "máximo 75,000" in t.error and "módulo local" in t.error


def test_75000_puntos_si_entran(cliente_de, topografa, tmp_path):
    ruta = tmp_path / "en_el_limite.dxf"
    dxf_de_puntos(ruta, 75_000)
    c = cliente_de(topografa)
    t = subir(c, ruta)
    c.post(url("triangulacion_superficie", pk=t.pk), {"modo": "puntos", "puntos": "on", "area_min": "0.000001", "decimales": "6"})
    t.refresh_from_db()
    assert t.superficie["puntos"] == 75_000 and not t.error, t.error


def test_el_contrato_mas_bajo_manda_y_uno_mas_alto_no_pasa_del_tope(settings):
    from itt_herramientas.triangulacion import servicio

    assert servicio.limites() == {"bytes": 100 * 1024 * 1024, "puntos": 75_000, "triangulos": 150_000}          # contrato 200 000 / 400 000: gana el tope de la web
    settings.TV_HERRAMIENTAS_LIMITES = {"triangulacion": {"bytes": 1000, "puntos": 10, "triangulos": 20}}
    assert servicio.limites() == {"bytes": 1000, "puntos": 10, "triangulos": 20}


def test_borrar_quita_archivos_y_registro(cliente_de, topografa, entradas_geo):
    c = cliente_de(topografa)
    t = con_superficie(c, entradas_geo["puntos.dxf"])
    from itt_herramientas.triangulacion import servicio

    carpeta = servicio.carpeta(t)
    assert (carpeta / "superficie.npz").is_file()
    assert c.post(url("triangulacion_borrar", pk=t.pk)).status_code == 302
    assert not Triangulacion.objects.exists() and not carpeta.exists()
