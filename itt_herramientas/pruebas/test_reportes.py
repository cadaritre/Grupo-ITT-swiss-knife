from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from unittest import mock

import pytest
from PIL import Image

from itt_herramientas.comun import capturas, perfiles, teselas
from itt_herramientas.models import Captura, Perfil, Reporte

from .conftest import url

pytestmark = pytest.mark.django_db


def crear_foto(cuenta, n, settings, tam=(640, 480)):
    """Una foto ya subida (fila + archivo), como la dejaría el destino de subida."""
    sha = f"{n:064x}"
    relativa = f"capturas/{cuenta.pk}/{sha[:2]}/{sha}.jpg"
    destino = settings.TV_HERRAMIENTAS_ARCHIVOS / relativa
    destino.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", tam, (20 + n * 7 % 200, 90, 160)).save(destino, "JPEG", quality=85)
    Captura.objects.create(cuenta_id=cuenta.pk, sha256=sha, ruta=relativa, bytes=destino.stat().st_size, ancho=tam[0], alto=tam[1])
    return sha


def foto(sha, nota="", lat=None, lon=None, tomada="2026-11-13T09:30:00"):
    return {"sha256": sha, "nota": nota, "tomada": tomada, "lat": lat, "lon": lon, "precision": 5.0 if lat is not None else None}


def datos(fotos, **campos):
    base = {"titulo": "Avance de obra", "fecha": "2026-11-13", "con_croquis": False, "con_credenciales": False, "fotos": fotos}
    base.update(campos)
    return base


def post(c, nombre, cuerpo, **kw):
    return c.post(url(nombre, **kw), json.dumps(cuerpo), content_type="application/json")


def sinc(c, idc, d, servidor=None, base=None):
    r = post(c, "reportes_sincronizar", {"id_cliente": str(idc), "servidor_id": servidor, "revision_base": base, "datos": d})
    assert r.status_code == 200, r.content
    return r.json()


def test_solo_con_el_rol(cliente_de, usuaria, reportera):
    assert cliente_de(usuaria).get(url("reportes_lista")).status_code == 403          # tiene Herramientas, pero no este rol
    assert cliente_de(reportera).get(url("reportes_lista")).status_code == 200


def test_contexto_usa_el_texto_del_perfil(cliente_de, reportera):
    r = cliente_de(reportera).get(url("reportes_contexto")).json()
    assert r["perfil"] == "grupo_itt" and r["titulo"] and r["max_fotos"] == 150


def test_sincronizacion_idempotente_conflicto_y_aislamiento(cliente_de, reportera, crear_usuario, roles):
    c = cliente_de(reportera)
    idc = uuid.uuid4()
    a = sinc(c, idc, datos([foto("a" * 64)]))
    assert sinc(c, idc, datos([foto("a" * 64)]))["resultado"] == "sin_cambios" and Reporte.objects.count() == 1
    b = sinc(c, idc, datos([foto("a" * 64, "nota")]), a["servidor_id"], a["revision"])
    assert b["resultado"] == "actualizada" and b["revision"] == 2
    r = sinc(c, idc, datos([foto("a" * 64, "del teléfono")]), a["servidor_id"], 1)
    assert r["resultado"] == "copia"
    assert Reporte.objects.get(pk=a["servidor_id"]).datos["fotos"][0]["nota"] == "nota"
    copia = Reporte.objects.get(pk=r["servidor_id"])
    assert copia.titulo.endswith("(desde el teléfono)") and copia.copia_de_id == uuid.UUID(a["servidor_id"])
    assert sinc(c, idc, datos([foto("a" * 64, "del teléfono")]), a["servidor_id"], 1)["servidor_id"] == r["servidor_id"]
    otra = crear_usuario("otra")
    roles.conceder(otra, "herramientas", "reportes")
    co = cliente_de(otra)
    assert co.get(url("reportes_detalle", pk=a["servidor_id"])).status_code == 404
    assert co.get(url("reportes_lista")).json()["reportes"] == []
    assert post(co, "reportes_pdf", {}, pk=a["servidor_id"]).status_code == 404


def test_validacion(cliente_de, reportera):
    c = cliente_de(reportera)
    malos = [datos([foto(f"{i:064x}") for i in range(151)]), datos([foto("zz")]), datos([foto("a" * 64)] * 2), datos([foto("a" * 64, lat=95.0, lon=0.0)]),
             datos([foto("a" * 64, lat=10.0)]), datos([foto("a" * 64, tomada="ayer")]), datos([foto("a" * 64, nota="x" * 1001)]), datos([], fecha="13/11/2026"), []]
    for malo in malos:
        r = post(c, "reportes_sincronizar", {"id_cliente": str(uuid.uuid4()), "datos": malo})
        assert r.status_code == 422, malo
    assert Reporte.objects.count() == 0


def test_no_genera_pdf_si_faltan_fotos_por_subir(cliente_de, reportera, settings):
    c = cliente_de(reportera)
    sha = crear_foto(reportera, 1, settings)
    a = sinc(c, uuid.uuid4(), datos([foto(sha), foto("b" * 64)]))
    r = post(c, "reportes_pdf", {}, pk=a["servidor_id"])
    assert r.status_code == 409 and r.json()["faltan"] == 1
    sin_titulo = sinc(c, uuid.uuid4(), datos([foto(sha)], titulo=""))
    assert post(c, "reportes_pdf", {}, pk=sin_titulo["servidor_id"]).status_code == 422
    vacio = sinc(c, uuid.uuid4(), datos([]))
    assert post(c, "reportes_pdf", {}, pk=vacio["servidor_id"]).status_code == 422


@pytest.mark.parametrize("clave", ["grupo_itt", "tresvizo"])
@pytest.mark.parametrize("caso", ["sin_croquis", "con_croquis_y_cedula"])
def test_pdf_de_la_web_identico_al_del_dominio(cliente_de, reportera, settings, roles, clave, caso, monkeypatch):
    """Mismas fotos y mismos textos por la web y directo por `generate_report` (el del escritorio): el mismo PDF, byte a byte."""
    from reportlab import rl_config

    from itt_herramientas.dominio import pdf_generator
    from itt_herramientas.dominio.metadata import PhotoInfo

    monkeypatch.setattr(rl_config, "invariant", 1)
    roles.conceder(reportera, "herramientas", "cambiar_perfil")
    perfiles.cambiar_perfil(reportera, clave)
    con_mapa = caso == "con_croquis_y_cedula"
    shas = [crear_foto(reportera, n, settings, (800, 600) if n % 2 else (600, 800)) for n in range(1, 6)]
    fotos = [foto(shas[0], "Frente del predio", 28.633, -106.069), foto(shas[1], "", 28.6335, -106.0685, "2026-11-13T09:41:00"), foto(shas[2], "Sin ubicación"),
             foto(shas[3], "Esquina norte " * 8, 28.634, -106.07), foto(shas[4])]
    c = cliente_de(reportera)
    a = sinc(c, uuid.uuid4(), datos(fotos, titulo="Reporte de avance: ñandú/cañería", con_croquis=con_mapa, con_credenciales=con_mapa))
    with mock.patch("itt_herramientas.dominio.maps._tile", side_effect=OSError("sin red")):
        r = post(c, "reportes_pdf", {}, pk=a["servidor_id"])
        assert r.status_code == 202, r.content
        web = c.get(url("reportes_descargar", pk=a["servidor_id"]))
        assert web.status_code == 200 and web["Content-Type"] == "application/pdf"
        assert "Reporte de avance" in web["Content-Disposition"] or "Reporte%20de%20avance" in web["Content-Disposition"]
        perfil = Perfil.objects.get(clave=clave)
        info = [PhotoInfo(path=settings.TV_HERRAMIENTAS_ARCHIVOS / Captura.objects.get(sha256=f["sha256"]).ruta, taken_at=datetime.strptime(f["tomada"], "%Y-%m-%dT%H:%M:%S"),
                          latitude=f["lat"], longitude=f["lon"], width=Captura.objects.get(sha256=f["sha256"]).ancho, height=Captura.objects.get(sha256=f["sha256"]).alto,
                          description=f["nota"]) for f in fotos]
        destino = settings.TV_HERRAMIENTAS_ARCHIVOS / "directo.pdf"
        opciones = perfiles.opciones_reporte(perfil, "Reporte de avance: ñandú/cañería", date(2026, 11, 13), info, destino, incluir_credenciales=con_mapa, include_map=con_mapa)
        with perfiles.activar(perfil):
            pdf_generator.generate_report(opciones)
    assert b"".join(web.streaming_content) == destino.read_bytes()
    assert Reporte.objects.get().salida_revision == 1


def test_descargar_solo_el_propio_y_con_pdf(cliente_de, reportera, settings):
    c = cliente_de(reportera)
    a = sinc(c, uuid.uuid4(), datos([foto(crear_foto(reportera, 1, settings))]))
    assert c.get(url("reportes_descargar", pk=a["servidor_id"])).status_code == 404         # aún sin PDF
    assert Reporte.objects.get().tarea_id is None


def test_miniatura_y_original(cliente_de, reportera, settings):
    sha = crear_foto(reportera, 3, settings, (1600, 1200))
    c = cliente_de(reportera)
    m = c.get(url("reportes_foto", sha256=sha))
    assert m.status_code == 200
    with Image.open(__import__("io").BytesIO(b"".join(m.streaming_content))) as im:
        assert max(im.size) == capturas.LADO_MINIATURA
    assert c.get(url("reportes_foto", sha256=sha) + "?original=1").status_code == 200
    assert c.get(url("reportes_foto", sha256="f" * 64)).status_code == 404
    assert c.get(url("reportes_foto", sha256="no-es-sha")).status_code == 404


def test_teselas_piden_sesion_y_rol(client, cliente_de, convertidora):
    from django.test import Client

    assert Client(HTTP_HOST="tools.tresvizo.com").get(url("teselas", capa="osm", z=0, x=0, y=0)).status_code in (302, 401, 403, 404)
    assert cliente_de(convertidora).get(url("teselas", capa="osm", z=0, x=0, y=0)).status_code == 403


# -- Fotos por página (1, 2, 4 o 6) ---------------------------------------------------------------------------------

def test_fotos_por_pagina_se_valida_y_por_omision_es_1(cliente_de, reportera):
    from itt_herramientas.reportes import servicio as rep

    assert rep.validar(datos([]))["fotos_por_pagina"] == 1                                 # un reporte de antes (o un teléfono con la versión anterior)
    for n in (1, 2, 4, 6):
        assert rep.validar(datos([], fotos_por_pagina=n))["fotos_por_pagina"] == n
    for malo in (0, 3, 5, 8, "2", 2.0, True, None, [4]):
        with pytest.raises(rep.ReporteInvalido):
            rep.validar(datos([], fotos_por_pagina=malo))
    c = cliente_de(reportera)
    r = post(c, "reportes_sincronizar", {"id_cliente": str(uuid.uuid4()), "datos": datos([], fotos_por_pagina=3)})
    assert r.status_code == 422
    idc = uuid.uuid4()
    a = sinc(c, idc, datos([foto("a" * 64)], fotos_por_pagina=4))
    assert Reporte.objects.get().datos["fotos_por_pagina"] == 4
    assert sinc(c, idc, datos([foto("a" * 64)], fotos_por_pagina=4), a["servidor_id"], a["revision"])["resultado"] == "sin_cambios"
    viejo = Reporte.objects.get()
    viejo.datos.pop("fotos_por_pagina")                                                     # guardado antes de existir la llave
    viejo.save()
    assert sinc(c, idc, datos([foto("a" * 64)]), a["servidor_id"], a["revision"])["resultado"] == "sin_cambios"


@pytest.mark.parametrize("por_pagina", [1, 2, 4, 6])
@pytest.mark.parametrize("con_croquis", [False, True])
def test_reparto_de_fotos_por_pagina(por_pagina, con_croquis):
    from itt_herramientas.dominio.pdf_generator import photo_pages

    for n in [*range(1, 14), 150]:
        paginas = photo_pages(n, por_pagina, con_croquis)
        assert [i for p in paginas for i in p] == list(range(n))                                    # todas, en orden, una vez
        assert paginas[0] == ([] if con_croquis else [0])                                           # portada: croquis, o la primera foto sola
        assert all(len(p) == por_pagina for p in paginas[1:-1])                                      # solo la última puede ir corta
        assert len(paginas) == 1 or 1 <= len(paginas[-1]) <= por_pagina
        resto = n - (0 if con_croquis else 1)
        assert len(paginas) == 1 + -(-resto // por_pagina)
    with pytest.raises(ValueError):
        photo_pages(5, 3)


def _fotos_info(carpeta, n, notas=None):
    from itt_herramientas.dominio.metadata import PhotoInfo

    carpeta.mkdir(parents=True, exist_ok=True)
    fotos = []
    for k in range(1, n + 1):
        tam = (400, 300) if k % 3 else (300, 400)                                            # horizontales y verticales mezcladas
        ruta = carpeta / f"{k}.jpg"
        Image.new("RGB", tam, (30 + k % 200, 90, 160)).save(ruta, "JPEG", quality=70)
        notas = notas or ["", "Nota corta", "Muro con grietas diagonales y humedad en la base " * 12]
        nota = notas[k % len(notas)]
        fotos.append(PhotoInfo(path=ruta, taken_at=datetime(2026, 11, 13, 9, k % 60), latitude=28.6 + k / 1e4 if k % 4 else None,
                               longitude=-106.07 if k % 4 else None, width=tam[0], height=tam[1], description=nota))
    return fotos


def _generar(tmp_path, fotos, por_pagina, con_croquis, **kw):
    from itt_herramientas.dominio import pdf_generator as g

    destino = tmp_path / f"r{por_pagina}{int(con_croquis)}{len(fotos)}.pdf"
    with mock.patch("itt_herramientas.dominio.maps._tile", side_effect=OSError("sin red")):
        g.generate_report(g.ReportOptions(title="Avance", report_date=date(2026, 11, 13), photos=fotos, output=destino, include_map=con_croquis,
                                          photos_per_page=por_pagina, **kw))
    return destino


@pytest.mark.parametrize("por_pagina", [2, 4, 6])
@pytest.mark.parametrize("con_croquis", [False, True])
def test_pdf_con_varias_fotos_por_pagina(tmp_path, por_pagina, con_croquis):
    """Páginas, celdas y números iguales al reparto; nada se sale de su celda; la última página corta conserva el tamaño de celda."""
    import pymupdf

    from itt_herramientas.dominio import pdf_generator as g

    n = 13
    fotos = _fotos_info(tmp_path / "fotos", n)
    paginas = g.photo_pages(n, por_pagina, con_croquis)
    celdas = g.grid_cells(por_pagina)
    alto_pagina = g.A4[1]
    with pymupdf.open(_generar(tmp_path, fotos, por_pagina, con_croquis, include_credentials=True)) as doc:
        assert doc.page_count == len(paginas) + 1                                                  # + cédula y firma
        for numero, indices in enumerate(paginas[1:], 2):
            pagina = doc[numero - 1]
            texto = pagina.get_text()
            assert [f"FOTOGRAFÍA {i + 1:02d}" in texto for i in indices] == [True] * len(indices)
            assert texto.count("FOTOGRAFÍA") == len(indices) and f"Página {numero}" in texto
            imagenes = [pymupdf.Rect(i["bbox"]) for i in pagina.get_image_info()]
            assert len(imagenes) == len(indices)
            def celda_de(caja):
                for k, celda in enumerate(celdas):
                    x, y, w, h = g.image_box(por_pagina, celda)
                    if x - .5 <= caja.x0 and caja.x1 <= x + w + .5 and alto_pagina - (y + h) - .5 <= caja.y0 and caja.y1 <= alto_pagina - y + .5:
                        return k
                raise AssertionError(f"imagen fuera de su celda: {caja}")
            assert sorted(celda_de(caja) for caja in imagenes) == list(range(len(indices)))            # las primeras celdas, desde arriba a la izquierda
            for bloque in pagina.get_text("dict")["blocks"]:
                if bloque.get("type") != 0 or bloque["bbox"][1] < 95 or bloque["bbox"][3] > alto_pagina - 50:
                    continue                                                                         # encabezado y pie
                x0, y0, x1, y1 = bloque["bbox"]
                assert any(cx - 1 <= x0 and x1 <= cx + cw + 1 and alto_pagina - (cy + ch) - 3 <= y0 and y1 <= alto_pagina - cy + 1
                           for cx, cy, cw, ch in celdas), (numero, bloque["bbox"])
            for linea in (s for b in pagina.get_text("dict")["blocks"] if b.get("type") == 0 for l in b["lines"] for s in l["spans"]):
                assert linea["size"] >= 7                                                           # nunca ilegible
        assert "…" in "".join(doc[p].get_text() for p in range(1, doc.page_count))                 # la nota larga termina con puntos suspensivos


def test_una_foto_por_pagina_es_el_documento_de_siempre(tmp_path):
    from itt_herramientas.dominio import pdf_generator as g
    from reportlab import rl_config

    fotos = _fotos_info(tmp_path / "fotos", 4)
    with mock.patch.object(rl_config, "invariant", 1), mock.patch("itt_herramientas.dominio.maps._tile", side_effect=OSError("sin red")):
        explicito = _generar(tmp_path / "a", fotos, 1, True)
        omision = tmp_path / "omision.pdf"
        g.generate_report(g.ReportOptions(title="Avance", report_date=date(2026, 11, 13), photos=fotos, output=omision, include_map=True))
    assert explicito.read_bytes() == omision.read_bytes()


def test_ciento_cincuenta_fotos_a_seis_por_pagina_sin_juntarlas_en_memoria(tmp_path):
    import pymupdf

    from itt_herramientas.dominio import pdf_generator as g

    fotos = _fotos_info(tmp_path / "fotos", 150, notas=["", "Nota"])
    abiertas, maximo, original = set(), [0], g._safe_image

    def contar(*a, **kw):
        imagen = original(*a, **kw)
        abiertas.add(id(imagen))
        maximo[0] = max(maximo[0], len(abiertas))
        cerrar = imagen.close
        imagen.close = lambda: (abiertas.discard(id(imagen)), cerrar())
        return imagen

    avance = []
    with mock.patch.object(g, "_safe_image", contar):
        destino = tmp_path / "150.pdf"
        g.generate_report(g.ReportOptions(title="Avance", report_date=date(2026, 11, 13), photos=fotos, output=destino, include_map=False, photos_per_page=6),
                          lambda hecho, total: avance.append((hecho, total)))
    assert maximo[0] <= 2                                                                         # la portada y una celda a la vez, nunca las 150
    with pymupdf.open(destino) as doc:
        assert doc.page_count == 1 + 25 and "FOTOGRAFÍA 150" in doc[-1].get_text()
    assert avance == [(k, 25) for k in range(1, 26)]


def test_web_genera_el_reporte_con_su_diseno_y_cuenta_paginas(cliente_de, reportera, settings):
    """El PDF de la cuenta, la copia de supervisión (y por tanto el ZIP, que usa las dos) salen con las fotos por página del reporte."""
    import pymupdf

    from itt_herramientas.reportes import servicio as rep

    c = cliente_de(reportera)
    shas = [crear_foto(reportera, n, settings, (320, 240)) for n in range(1, 10)]
    a = sinc(c, uuid.uuid4(), datos([foto(s, f"Nota {k}") for k, s in enumerate(shas, 1)], fotos_por_pagina=4, con_croquis=False, con_credenciales=False))
    r = Reporte.objects.get(pk=a["servidor_id"])
    mensajes = []
    ruta = rep.generar(r, lambda fraccion, texto="": mensajes.append(texto))
    with pymupdf.open(ruta) as doc:
        assert doc.page_count == 1 + 2                                                             # portada con la foto 1 y 8 fotos en 2 páginas
    assert [m for m in mensajes if m.startswith("Página")] == ["Página 2 de 3", "Página 3 de 3"]
    with pymupdf.open(rep.generar_copia(r, lambda *a: None)) as doc:
        assert doc.page_count == 3
