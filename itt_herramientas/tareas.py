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
