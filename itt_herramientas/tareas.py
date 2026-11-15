"""Tareas de Herramientas (el armazón importa este módulo al arrancar). Cola `herramientas`: 1 s a 3 min, ≤ 768 MB."""
from __future__ import annotations

import functools

from tvplataforma.tareas import Cancelada, tarea as _tarea

from .comun import errores
from .models import Conversion


def tarea(cola):
    """`@tarea` del armazón con los errores saneados: la persona ve un mensaje en español, el detalle queda en el registro.

    Un `InterruptedError` es la cancelación pedida desde dentro de un cálculo: termina la tarea como cancelada, no como fallida.
    """
    registrar = _tarea(cola=cola)

    def decorador(funcion):
        @functools.wraps(funcion)
        def segura(*args, **kwargs):
            try:
                return funcion(*args, **kwargs)
            except Cancelada:
                raise
            except InterruptedError:
                raise Cancelada() from None
            except Exception as exc:
                raise errores.ErrorDeTarea(errores.texto(exc)) from exc
        return registrar(segura)
    return decorador


@tarea("herramientas")
def analizar_conversion(conversion_id):
    from .convertidor import servicio

    servicio.analizar(Conversion.objects.get(pk=conversion_id))
    return {"mensaje": "Archivo leído."}


@tarea("herramientas")
def convertir_conversion(conversion_id):
    from .convertidor import servicio

    c = Conversion.objects.get(pk=conversion_id)
    servicio.convertir(c)
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    # El trabajador usa el urlconf del portal; la ruta de descarga es la del host de Herramientas.
    url = reverse("herramientas:convertidor_descargar", args=[c.pk], urlconf=urlconf_de("herramientas"))
    return {"mensaje": "Conversión terminada.", "url": url, "etiqueta": "Descargar"}


@tarea("herramientas")
def generar_reporte(reporte_id):
    """PDF de un reporte fotográfico (10 a 90 s para 150 fotos)."""
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    from .models import Reporte
    from .reportes import servicio

    r = Reporte.objects.get(pk=reporte_id)
    servicio.generar(r)
    url = reverse("herramientas:reportes_descargar", args=[r.pk], urlconf=urlconf_de("herramientas"))
    return {"mensaje": "Reporte listo.", "url": url, "etiqueta": "Descargar PDF"}


@tarea("herramientas")
def vectorizar_croquis(croquis_id):
    """Vectoriza el área de un croquis desde OpenStreetMap (10 s a 2 min; hasta 50 km²)."""
    from .models import Croquis
    from .croquis import servicio

    servicio.vectorizar(Croquis.objects.get(pk=croquis_id))
    return {"mensaje": "Cartografía lista."}


@tarea("herramientas")
def generar_croquis(croquis_id, formato):
    """PDF o DXF de un croquis."""
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    from .models import Croquis
    from .croquis import servicio

    c = Croquis.objects.get(pk=croquis_id)
    servicio.generar(c, formato)
    url = reverse("herramientas:croquis_descargar", args=[c.pk], urlconf=urlconf_de("herramientas"))
    return {"mensaje": "Croquis listo.", "url": url, "etiqueta": f"Descargar {formato.upper()}"}


def _triangulacion(triangulacion_id):
    from .models import Triangulacion

    return Triangulacion.objects.get(pk=triangulacion_id)


@tarea("herramientas")
def analizar_triangulacion(triangulacion_id):
    """Lee el DXF y cuenta lo que trae (segundos)."""
    from .models import Triangulacion
    from .triangulacion import servicio

    t = _triangulacion(triangulacion_id)
    try:
        t.resumen = servicio.leer(t)
        t.estado, t.error = Triangulacion.Estado.LISTO, ""
    except Exception as exc:
        t.estado, t.error = Triangulacion.Estado.ERROR, errores.texto(exc, errores.ARCHIVO_ILEGIBLE)
    t.save(update_fields=["resumen", "estado", "error"])
    return {"mensaje": "Archivo leído."}


def _calculo(triangulacion_id, funcion, mensaje):
    t = _triangulacion(triangulacion_id)
    t.error = ""
    try:
        funcion(t)
    except InterruptedError:
        raise
    except Exception as exc:                                         # se muestra en la mesa de trabajo; el DXF sigue en su estado
        t.error = errores.texto(exc)
        t.save(update_fields=["error"])
        return {"mensaje": f"No se pudo calcular: {t.error}"}
    t.save(update_fields=["error"])
    return {"mensaje": mensaje}


@tarea("herramientas")
def calcular_superficie_triangulacion(triangulacion_id, opciones):
    from .triangulacion import servicio

    return _calculo(triangulacion_id, lambda t: servicio.calcular_superficie(t, opciones), "Superficie lista.")


@tarea("herramientas")
def calcular_pendientes_triangulacion(triangulacion_id, rangos, decimales):
    from .triangulacion import servicio
    from .dominio import triangulation as tri

    def trabajo(t):
        servicio.calcular_pendientes(t, [tri.SlopeRange(**r) for r in rangos], decimales)
    return _calculo(triangulacion_id, trabajo, "Zonificación lista.")


@tarea("herramientas")
def calcular_escurrimiento_triangulacion(triangulacion_id, rangos, opciones):
    from .triangulacion import servicio
    from .dominio import triangulation as tri

    def trabajo(t):
        servicio.calcular_escurrimiento(t, [tri.FlowRange(**r) for r in rangos], opciones)
    return _calculo(triangulacion_id, trabajo, "Escurrimientos listos.")


@tarea("herramientas")
def exportar_triangulacion(triangulacion_id, tipo, opciones):
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    from .triangulacion import servicio

    t = _triangulacion(triangulacion_id)
    try:
        servicio.exportar(t, tipo, opciones)
    except InterruptedError:
        raise
    except Exception as exc:
        t.error = errores.texto(exc)
        t.save(update_fields=["error"])
        return {"mensaje": f"No se pudo exportar: {t.error}"}
    url = reverse("herramientas:triangulacion_descargar", args=[t.pk], urlconf=urlconf_de("herramientas"))
    return {"mensaje": "DXF listo.", "url": url, "etiqueta": "Descargar DXF"}


@tarea("herramientas")
def enviar_a_drive(cuenta_id):
    """Envía a Google Drive lo pendiente de una cuenta (los reintentos los dispara `herramientas_drive_reintentar` o la persona)."""
    from .drive import servicio

    r = servicio.procesar(cuenta_id)
    return {"mensaje": f"{r['enviados']} enviados, {r['pendientes']} pendientes."}


@tarea("herramientas")
def generar_reporte_supervision(reporte_id):
    """PDF de un reporte para quien supervisa (no toca el reporte ni su último PDF)."""
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    from .models import Reporte
    from .reportes import servicio

    r = Reporte.objects.get(pk=reporte_id)
    servicio.generar_copia(r)
    url = reverse("herramientas:supervision_reporte_descargar", args=[r.pk], urlconf=urlconf_de("herramientas")) + f"?revision={r.revision}"
    return {"mensaje": "PDF listo.", "url": url, "etiqueta": "Descargar PDF"}


@tarea("herramientas")
def armar_zip(token, cuenta_id, elementos, tipo, supervision=False):
    """ZIP con los PDF de varios documentos (genera antes los reportes sin PDF al día). Hasta 50 por lote."""
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    from .comun import zips

    resultado = zips.armar(token, cuenta_id, elementos, tipo, supervision)
    url = reverse("herramientas:zip_descargar", args=[token], urlconf=urlconf_de("herramientas"))
    return {**resultado, "url": url, "etiqueta": "Descargar ZIP"}


# -- CAD: impresión a PDF y conversión con el motor nativo, en su propia cola («cad», un trabajador con su propio tope de memoria) ---------

@tarea("cad")
def imprimir_plano(plano_id, numero, cuenta_id, opciones):
    """PDF de una versión de un plano (5 s a 2 min). El archivo es de quien lo pidió y se borra al día siguiente."""
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    from .cad import salidas

    token, nombre = salidas.imprimir(plano_id, numero, cuenta_id, opciones)
    url = reverse("herramientas:cad_salida", args=[token], urlconf=urlconf_de("herramientas"))
    return {"mensaje": "PDF listo.", "url": url, "etiqueta": "Descargar PDF", "nombre": nombre}


@tarea("cad")
def convertir_plano(plano_id, numero, cuenta_id, formato, permitir_perdida):
    """El plano en el otro formato (DWG ↔ DXF). Si se perderían elementos y no se permitió, el resultado lo dice y no hay archivo."""
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    from .cad import nativo, salidas

    try:
        token, nombre, perdidos = salidas.convertir(plano_id, numero, cuenta_id, formato, permitir_perdida)
    except nativo.Perdida as exc:
        return {"mensaje": str(exc), "perdida": True}
    url = reverse("herramientas:cad_salida", args=[token], urlconf=urlconf_de("herramientas"))
    aviso = f" Se dejaron fuera {perdidos} elemento{'s' if perdidos != 1 else ''} que {formato.upper()} no admite." if perdidos else ""
    return {"mensaje": f"{formato.upper()} listo.{aviso}", "url": url, "etiqueta": f"Descargar {formato.upper()}", "nombre": nombre}
