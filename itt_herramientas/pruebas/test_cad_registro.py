"""CAD · diagnóstico de «Sin guardar»: la cabecera X-TV-Registro del borrador se valida, se recorta y va al journal; nunca rompe el
borrador ni deja pasar contenido del dibujo."""
from __future__ import annotations

import json
import logging

from itt_herramientas.cad import registro
from itt_herramientas.models import BorradorPlano

from .conftest import url
from .test_cad import DXF, dibujante, editor_falso, plano  # noqa: F401 (fixtures)

VALIDO = {"v": 1, "m": "t", "r": [[2020, "abierto", 1, 0], [2450, "canvas_only", 1, 0], [9100, "view_set", 1, 0]], "s": [[11000, 1, 0]]}


def test_valido_queda_igual_y_compacto():
    t = registro.limpio(json.dumps(VALIDO))
    assert json.loads(t) == VALIDO and " " not in t


def test_quita_lo_que_no_es_del_formato():
    d = {"v": 1, "m": "t", "x": "otra cosa", "r": [[1, "run", 1, 2], [2, "LINE 395000,3175000", 1, 3], [3, "view_set", 1], "texto"],
         "s": [[1, 2, "3"], [4, 5, 6]]}
    assert json.loads(registro.limpio(json.dumps(d))) == {"v": 1, "m": "t", "r": [[1, "run", 1, 2]], "s": [[4, 5, 6]]}


def test_grande_se_recorta_por_lo_mas_viejo():
    d = {"v": 1, "m": "c", "r": [[i, "view_set", 1, i] for i in range(120)], "s": [[999, 1, 0]]}
    t = registro.limpio(json.dumps(d))
    out = json.loads(t)
    assert len(t) <= registro.LIMITE and out["recortado"] is True and out["s"] == [[999, 1, 0]] and out["r"][-1] == [119, "view_set", 1, 119]


def test_malformado_o_vacio():
    assert registro.limpio("") is None and registro.limpio(None) is None
    assert json.loads(registro.limpio("{no es json")) == {"malformado": "json"}
    assert json.loads(registro.limpio("[1, 2]")) == {"malformado": "tipo"}
    assert json.loads(registro.limpio('{"m": "tñ"}')) == {"malformado": "no_ascii"}
    assert len(registro.limpio("x" * 100000)) < 100


def test_el_borrador_lo_escribe_en_el_registro_y_nunca_falla(cliente_de, dibujante, plano, caplog):
    cl = cliente_de(dibujante)
    caplog.set_level(logging.INFO, logger="itt_herramientas.cad")
    for cabecera, esperado in ((json.dumps(VALIDO), '"canvas_only"'), ("{roto", '"malformado"'), ("a" * 50000, '"malformado"')):
        caplog.clear()
        r = cl.post(url("cad_borrador", pk=plano.pk), DXF, content_type="application/octet-stream", HTTP_X_TV_REVISION="1", HTTP_X_TV_REGISTRO=cabecera)
        assert r.status_code == 200, r.content
        lineas = [x.getMessage() for x in caplog.records if "registro sin guardar" in x.getMessage()]
        assert len(lineas) == 1 and f"plano={plano.pk}" in lineas[0] and f"cuenta={dibujante.pk}" in lineas[0] and esperado in lineas[0], lineas
    # Sin cabecera: nada en el registro.
    caplog.clear()
    assert cl.post(url("cad_borrador", pk=plano.pk), DXF, content_type="application/octet-stream", HTTP_X_TV_REVISION="1").status_code == 200
    assert not [x for x in caplog.records if "registro sin guardar" in x.getMessage()]
    assert BorradorPlano.objects.filter(plano=plano).count() == 1


# -- El arranque vigilado: el informe de un editor que no arrancó (POST /cad/<id>/arranque/) -------------------------------------------

ARRANQUE = {"v": 1, "etapa": "editor", "motivo": "plazo", "desde": "red", "modo": "t", "recuperado": True, "ms": 61234,
            "pasos": [["memoria", 3], ["preparar", 5], ["marco", 900], ["compilar", 4100], ["editor", 16200]],
            "eq": {"w": 800, "h": 1280, "dpr": 1.8, "tactil": 1, "fino": 0, "mem": 8, "cpu": 8, "webgpu": 1, "sw": 1, "app": 0},
            "gl": "ANGLE (ARM, Mali-G68 MC4, OpenGL ES 3.2) · 8192", "aviso": ""}


def test_el_informe_de_arranque_se_valida_campo_por_campo():
    out = json.loads(registro.arranque(ARRANQUE))
    assert out["etapa"] == "editor" and out["desde"] == "red" and out["modo"] == "t" and out["recuperado"] == 1 and out["ms"] == 61234
    assert out["eq"] == ARRANQUE["eq"] and out["pasos"][-1] == ["editor", 16200] and "Mali-G68" in out["gl"] and "aviso" not in out
    raro = {**ARRANQUE, "etapa": "dibujo", "desde": "<x>", "eq": {"w": -1, "mem": "8", "dpr": True, "otro": 5}, "pasos": [["x", 1], ["marco", "1"]],
            "gl": "Malié\n" + "x" * 500, "aviso": 7, "plano": "contenido"}
    out = json.loads(registro.arranque(raro))
    assert out["etapa"] == "?" and out["desde"] == "?" and out["eq"] == {} and out["pasos"] == [] and "plano" not in out and "aviso" not in out
    assert out["gl"].startswith("Mali??") and len(out["gl"]) == 120
    largo = {**ARRANQUE, "pasos": [["marco", i] for i in range(5000)], "aviso": "y" * 5000}
    assert len(registro.arranque(largo)) <= registro.LIMITE
    assert json.loads(registro.arranque(None)) == {"malformado": "tipo"}


def test_el_arranque_detenido_va_al_registro_con_el_equipo(cliente_de, dibujante, plano, caplog):
    cl = cliente_de(dibujante)
    caplog.set_level(logging.INFO, logger="itt_herramientas.cad")
    r = cl.post(url("cad_arranque", pk=plano.pk), json.dumps(ARRANQUE), content_type="application/json",
                HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")
    assert r.status_code == 204
    lineas = [x.getMessage() for x in caplog.records if "cad arranque" in x.getMessage()]
    assert len(lineas) == 1 and f"plano={plano.pk}" in lineas[0] and "Android 10; K" in lineas[0] and '"etapa":"editor"' in lineas[0], lineas
    caplog.clear()
    assert cl.post(url("cad_arranque", pk=plano.pk), "{roto", content_type="application/json").status_code == 204
    assert '"malformado"' in [x.getMessage() for x in caplog.records if "cad arranque" in x.getMessage()][0]
    # Solo con acceso al plano y por POST.
    assert cl.get(url("cad_arranque", pk=plano.pk)).status_code == 405
    from django.test import Client

    from .conftest import HOST
    assert Client(HTTP_HOST=HOST).post(url("cad_arranque", pk=plano.pk), "{}", content_type="application/json").status_code in (302, 401, 403, 404)
