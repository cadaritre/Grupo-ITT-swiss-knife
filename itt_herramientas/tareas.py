"""Tareas de Herramientas (el armazón importa este módulo al arrancar). Cola `herramientas`: 1 s a 3 min, ≤ 768 MB."""
from __future__ import annotations

from tvplataforma.tareas import tarea

from .models import Conversion


@tarea(cola="herramientas")
def analizar_conversion(conversion_id):
    from .convertidor import servicio

    servicio.analizar(Conversion.objects.get(pk=conversion_id))
    return {"mensaje": "Archivo leído."}


@tarea(cola="herramientas")
def convertir_conversion(conversion_id):
    from .convertidor import servicio

    c = Conversion.objects.get(pk=conversion_id)
    servicio.convertir(c)
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    # El trabajador usa el urlconf del portal; la ruta de descarga es la del host de Herramientas.
    url = reverse("herramientas:convertidor_descargar", args=[c.pk], urlconf=urlconf_de("herramientas"))
    return {"mensaje": "Conversión terminada.", "url": url, "etiqueta": "Descargar"}


@tarea(cola="herramientas")
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


@tarea(cola="herramientas")
def vectorizar_croquis(croquis_id):
    """Vectoriza el área de un croquis desde OpenStreetMap (10 s a 2 min; hasta 50 km²)."""
    from .models import Croquis
    from .croquis import servicio

    servicio.vectorizar(Croquis.objects.get(pk=croquis_id))
    return {"mensaje": "Cartografía lista."}


@tarea(cola="herramientas")
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


@tarea(cola="herramientas")
def analizar_triangulacion(triangulacion_id):
    """Lee el DXF y cuenta lo que trae (segundos)."""
    from .models import Triangulacion
    from .triangulacion import servicio

    t = _triangulacion(triangulacion_id)
    try:
        t.resumen = servicio.leer(t)
        t.estado, t.error = Triangulacion.Estado.LISTO, ""
    except Exception as exc:
        t.estado, t.error = Triangulacion.Estado.ERROR, str(exc)
    t.save(update_fields=["resumen", "estado", "error"])
    return {"mensaje": "Archivo leído."}


def _calculo(triangulacion_id, funcion, mensaje):
    t = _triangulacion(triangulacion_id)
    t.error = ""
    try:
        funcion(t)
    except Exception as exc:                                         # se muestra en la mesa de trabajo; el DXF sigue en su estado
        t.error = str(exc)
        t.save(update_fields=["error"])
        return {"mensaje": f"No se pudo calcular: {exc}"}
    t.save(update_fields=["error"])
    return {"mensaje": mensaje}


@tarea(cola="herramientas")
def calcular_superficie_triangulacion(triangulacion_id, opciones):
    from .triangulacion import servicio

    return _calculo(triangulacion_id, lambda t: servicio.calcular_superficie(t, opciones), "Superficie lista.")


@tarea(cola="herramientas")
def calcular_pendientes_triangulacion(triangulacion_id, rangos, decimales):
    from .triangulacion import servicio
    from .dominio import triangulation as tri

    def trabajo(t):
        servicio.calcular_pendientes(t, [tri.SlopeRange(**r) for r in rangos], decimales)
    return _calculo(triangulacion_id, trabajo, "Zonificación lista.")


@tarea(cola="herramientas")
def calcular_escurrimiento_triangulacion(triangulacion_id, rangos, opciones):
    from .triangulacion import servicio
    from .dominio import triangulation as tri

    def trabajo(t):
        servicio.calcular_escurrimiento(t, [tri.FlowRange(**r) for r in rangos], opciones)
    return _calculo(triangulacion_id, trabajo, "Escurrimientos listos.")


@tarea(cola="herramientas")
def exportar_triangulacion(triangulacion_id, tipo, opciones):
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    from .triangulacion import servicio

    t = _triangulacion(triangulacion_id)
    try:
        servicio.exportar(t, tipo, opciones)
    except Exception as exc:
        t.error = str(exc)
        t.save(update_fields=["error"])
        return {"mensaje": f"No se pudo exportar: {exc}"}
    url = reverse("herramientas:triangulacion_descargar", args=[t.pk], urlconf=urlconf_de("herramientas"))
    return {"mensaje": "DXF listo.", "url": url, "etiqueta": "Descargar DXF"}


@tarea(cola="herramientas")
def enviar_a_drive(cuenta_id):
    """Envía a Google Drive lo pendiente de una cuenta (los reintentos los dispara `herramientas_drive_reintentar` o la persona)."""
    from .drive import servicio

    r = servicio.procesar(cuenta_id)
    return {"mensaje": f"{r['enviados']} enviados, {r['pendientes']} pendientes."}
