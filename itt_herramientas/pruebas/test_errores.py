"""UX-ITT-01: la persona nunca ve rutas del servidor ni texto de excepciones (inglés) en un error."""
from __future__ import annotations

import re

import ezdxf
import pytest

from itt_herramientas.comun import errores
from itt_herramientas.models import Conversion, Triangulacion

from .conftest import url
from .test_convertidor import subir_archivo
from .test_triangulacion import subir, topografa  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db
RUTA_O_INGLES = re.compile(r"/tmp|/var/|/Users/|herramientas/(convertidor|triangulacion)|\.py\b|\bis not\b|\bFile\b|Traceback|Error:", re.I)


def test_las_excepciones_de_bibliotecas_no_se_enseñan():
    assert errores.texto(ValueError("File '/srv/x/3/a.dxf' is not a DXF file.")) == errores.GENERICO
    assert "no es un DXF válido" in errores.texto(OSError("File '/srv/a.dxf' is not a DXF file."))
    assert errores.texto(OSError("[Errno 2] No such file or directory: '/srv/archivos/a.dxf'")) == errores.GENERICO
    assert errores.texto(KeyError("capa")) == errores.GENERICO
    assert "no es un DXF válido" in errores.texto(ezdxf.DXFStructureError("File 'x' is not a DXF file."))
    assert errores.texto(ValueError("invalid literal for int() with base 10: 'x'")) == errores.GENERICO
    assert "demasiado grande" in errores.texto(MemoryError())


def test_los_mensajes_propios_se_conservan():
    assert errores.texto(errores.ErrorUsuario("Activa al menos una capa para exportar.")) == "Activa al menos una capa para exportar."
    assert errores.texto(ValueError("El primer rango debe comenzar en 0%.")) == "El primer rango debe comenzar en 0%."
    assert errores.texto(RuntimeError("No se pudo consultar la geometría de OpenStreetMap. https://x.org/api/interpreter: timed out")) == errores.GENERICO


def test_convertidor_con_un_archivo_que_no_es_dxf_dice_que_no_es_valido(cliente_de, convertidora, tmp_path):
    dxf = tmp_path / "no-es-dxf.dxf"
    dxf.write_text("hola mundo\nesto no es un dxf\n")
    cl = cliente_de(convertidora)
    c = subir_archivo(cl, dxf)
    cl.post(url("convertidor_analizar", pk=c.pk), {"zona": "13", "hemisferio": "N"})
    c.refresh_from_db()
    assert c.estado == Conversion.Estado.ERROR and c.error
    assert not RUTA_O_INGLES.search(c.error) and "DXF válido" in c.error, c.error
    pagina = cl.get(url("convertidor_banco", pk=c.pk)).content.decode()
    assert not RUTA_O_INGLES.search(pagina.split("No se pudo leer el archivo", 1)[-1][:600])


def test_triangulacion_con_un_archivo_que_no_es_dxf_dice_que_no_es_valido(cliente_de, topografa, tmp_path):
    dxf = tmp_path / "no-es-dxf.dxf"
    dxf.write_text("hola mundo\nesto no es un dxf\n")
    t = subir(cliente_de(topografa), dxf)
    assert t.estado == Triangulacion.Estado.ERROR and t.error
    assert not RUTA_O_INGLES.search(t.error) and "DXF" in t.error, t.error


def test_una_tarea_que_falla_deja_solo_un_mensaje_en_español(monkeypatch):
    from itt_herramientas import tareas

    def rota():
        raise RuntimeError("boom: /srv/archivos/9/secreto.dxf (línea 4)")
    segura = tareas.tarea("herramientas")(rota)
    with pytest.raises(errores.ErrorDeTarea) as fallo:
        segura.funcion()
    assert "/srv" not in str(fallo.value) and "secreto" not in str(fallo.value)
    assert str(fallo.value) == errores.GENERICO


def test_cancelar_dentro_de_un_calculo_termina_como_cancelada():
    from tvplataforma.tareas import Cancelada

    from itt_herramientas import tareas

    def cancela():
        raise InterruptedError("Cancelado")
    with pytest.raises(Cancelada):
        tareas.tarea("herramientas")(cancela).funcion()
