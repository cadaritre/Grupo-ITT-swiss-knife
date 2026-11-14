"""Destino de subida `herramientas.triangulacion_entrada`: el DXF con puntos o triángulos."""
from __future__ import annotations

from tvplataforma.subidas import ErrorDeSubida, destino_subida

from ..models import Triangulacion
from ..tareas import analizar_triangulacion
from . import servicio


def _limite() -> int:
    return servicio.limites()["bytes"]


@destino_subida("herramientas.triangulacion_entrada", rol=[("herramientas", "triangulacion")], max_bytes=_limite)
def recibir(archivo, metadatos, usuario):
    nombre = archivo.nombre
    if not nombre.lower().endswith(".dxf"):
        raise ErrorDeSubida("Selecciona un archivo DXF.")
    with archivo.abrir() as f:
        cabecera = f.read(4096)
    if b"\x00" in cabecera[:1024] and not cabecera.startswith(b"AutoCAD Binary DXF"):
        raise ErrorDeSubida("El archivo no parece un DXF.")
    t = Triangulacion(cuenta_id=usuario.pk, nombre=nombre[:150], bytes=archivo.tamano, ruta_origen="")
    destino = servicio.carpeta(t) / "origen.dxf"
    archivo.mover_a(destino)
    t.ruta_origen = str(destino.relative_to(servicio.raiz()))
    t.estado = Triangulacion.Estado.ANALIZANDO
    t.save()
    t.tarea_id = analizar_triangulacion.encolar(str(t.pk), usuario=usuario).pk
    t.save(update_fields=["tarea_id"])
    return {"triangulacion": str(t.pk)}
