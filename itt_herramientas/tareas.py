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
