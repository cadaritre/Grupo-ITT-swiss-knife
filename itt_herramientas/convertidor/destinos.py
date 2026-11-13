"""Destino de subida `herramientas.convertidor_entrada`: el DXF, KML o KMZ que se va a convertir."""
from __future__ import annotations

from tvplataforma.subidas import ErrorDeSubida, destino_subida

from ..models import Conversion
from ..tareas import analizar_conversion
from . import servicio


def _limite() -> int:
    return servicio.limites()["bytes"]


@destino_subida("herramientas.convertidor_entrada", rol=[("herramientas", "convertidor")], max_bytes=_limite)
def recibir(archivo, metadatos, usuario):
    nombre = archivo.nombre
    formato = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    if formato not in servicio.FORMATOS_ENTRADA:
        raise ErrorDeSubida("Selecciona un archivo DXF, KML o KMZ.")
    with archivo.abrir() as f:
        cabecera = f.read(4096)
    if formato == "kmz" and not cabecera.startswith(b"PK"):
        raise ErrorDeSubida("El KMZ no es un archivo comprimido válido.")
    if formato == "kml" and b"<" not in cabecera.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]:
        raise ErrorDeSubida("El KML debe ser un archivo XML.")
    if formato == "dxf" and b"\x00" in cabecera[:1024] and not cabecera.startswith(b"AutoCAD Binary DXF"):
        raise ErrorDeSubida("El archivo no parece un DXF.")
    c = Conversion(cuenta_id=usuario.pk, nombre=nombre, formato=formato, bytes=archivo.tamano, ruta_origen="")
    # Con el nombre original: el escritorio lo escribe en el archivo convertido («Fuente: <nombre>»), y la salida debe ser idéntica.
    destino = servicio.carpeta(c) / nombre
    archivo.mover_a(destino)
    c.ruta_origen = str(destino.relative_to(servicio.raiz()))
    c.estado = Conversion.Estado.ANALIZANDO
    c.save()
    # Se empieza a leer de inmediato: cuando la persona llega a la mesa de trabajo ya hay avance (o ya terminó).
    c.tarea_id = analizar_conversion.encolar(str(c.pk), usuario=usuario).pk
    c.save(update_fields=["tarea_id"])
    return {"conversion": str(c.pk)}
