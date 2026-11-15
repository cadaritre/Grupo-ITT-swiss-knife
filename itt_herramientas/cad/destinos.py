"""Destino de subida `herramientas.cad_plano`: un DWG o DXF que entra a la biblioteca de planos como plano nuevo."""
from __future__ import annotations

from django.urls import reverse
from tvplataforma.middleware import urlconf_de
from tvplataforma.subidas import ErrorDeSubida, destino_subida

from ..comun.errores import ErrorUsuario
from . import servicio


def _limite() -> int:
    return servicio.limites()["bytes"]


@destino_subida("herramientas.cad_plano", rol=[("herramientas", "cad"), ("herramientas", "admin")], max_bytes=_limite, meta={"proyecto": None})
def recibir(archivo, metadatos, usuario):
    extension = archivo.nombre.rsplit(".", 1)[-1].lower() if "." in archivo.nombre else ""
    if extension not in servicio.FORMATOS:
        raise ErrorDeSubida("Elige un archivo DWG o DXF.")
    with archivo.abrir() as f:
        formato = servicio.detectar_formato(f.read(8192))
    if formato is None:
        raise ErrorDeSubida("El archivo no es un DWG ni un DXF válido.")
    if formato != extension:
        raise ErrorDeSubida(f"El archivo dice ser {extension.upper()} pero es un {formato.upper()}. Corrige su extensión y vuelve a subirlo.")
    temporal = servicio.carpeta_temporal() / f"subida-{archivo.sha256[:16]}-{usuario.pk}.tmp"
    archivo.mover_a(temporal)
    try:
        p = servicio.crear(usuario, temporal, archivo.nombre, metadatos.get("proyecto") or "", {"tipo": "subida", "nombre": archivo.nombre})
    except ErrorUsuario as exc:
        raise ErrorDeSubida(str(exc)) from exc
    finally:
        temporal.unlink(missing_ok=True)
    return {"plano": str(p.pk), "url": reverse("herramientas:cad_plano", args=[p.pk], urlconf=urlconf_de("herramientas"))}
