"""Destino de subida `herramientas.captura` (contrato §7): recibe las fotos de la cola sin conexión."""
from __future__ import annotations

from pathlib import Path

from django.db import IntegrityError
from PIL import Image, UnidentifiedImageError
from tvplataforma import ajustes
from tvplataforma.subidas import ErrorDeSubida, destino_subida

from ..models import Captura

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELES = 40_000_000
FORMATOS = {"JPEG": ".jpg", "PNG": ".png"}


def _rango(minimo, maximo):
    def validar(texto):
        n = float(texto)
        if not minimo <= n <= maximo:
            raise ValueError(f"debe estar entre {minimo} y {maximo}")
        return n
    return validar


def _entero(texto):
    return int(float(texto))


def _corto(maximo):
    def validar(texto):
        if len(texto) > maximo:
            raise ValueError(f"máximo {maximo} caracteres")
        return texto
    return validar


#: Metadatos propios que la cola del teléfono manda con cada foto (contrato tvplataforma 1.2: el destino los declara).
META = {
    "tomada": _corto(32), "lat": _rango(-90, 90), "lon": _rango(-180, 180), "precision": _rango(0, 1e6), "sin_ubicacion": _corto(1),
    "nota": _corto(1000), "referencia": _corto(100), "origen": _corto(10), "orden": _rango(-1e6, 1e6),
}


@destino_subida("herramientas.captura", rol=[("herramientas", "reportes"), ("herramientas", "cotizaciones")], max_bytes=MAX_BYTES, meta=META)
def recibir_captura(archivo, metadatos, usuario):
    """Valida la imagen por su contenido real y la guarda con su sha256 como nombre. Idempotente por (cuenta, sha256)."""
    previa = Captura.objects.filter(cuenta_id=usuario.pk, sha256=archivo.sha256).first()
    if previa is not None:
        return {"captura_id": previa.pk, "sha256": previa.sha256, "duplicada": True}
    try:
        with Image.open(archivo.ruta) as imagen:
            formato, (ancho, alto) = imagen.format, imagen.size
            imagen.verify()
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ErrorDeSubida("El archivo no es una imagen válida.") from None
    if formato not in FORMATOS:
        raise ErrorDeSubida("Las fotos deben ser JPEG o PNG.")
    if ancho * alto > MAX_PIXELES:
        raise ErrorDeSubida("La foto supera los 40 megapíxeles.")
    cuota = int(ajustes.valor("TV_HERRAMIENTAS_CUOTA"))
    usado = sum(Captura.objects.filter(cuenta_id=usuario.pk).values_list("bytes", flat=True))
    if usado + archivo.tamano > cuota:
        raise ErrorDeSubida("Tu espacio en el servidor está lleno. Pídele al propietario que lo amplíe.")
    relativa = Path("capturas") / str(usuario.pk) / archivo.sha256[:2] / f"{archivo.sha256}{FORMATOS[formato]}"
    raiz = Path(ajustes.valor("TV_HERRAMIENTAS_ARCHIVOS"))
    archivo.mover_a(raiz / relativa)
    lat, lon = metadatos.get("lat"), metadatos.get("lon")
    if lat is None or lon is None:                  # una sola coordenada no sirve
        lat = lon = None
    try:
        captura = Captura.objects.create(
            cuenta_id=usuario.pk, sha256=archivo.sha256, ruta=relativa.as_posix(), bytes=archivo.tamano, ancho=ancho, alto=alto,
            nombre=archivo.nombre[:150], referencia=(metadatos.get("referencia") or "")[:100], orden=int(metadatos.get("orden") or 0),
            tomada=(metadatos.get("tomada") or "")[:32], lat=lat, lon=lon, precision=metadatos.get("precision"),
            sin_ubicacion=lat is None, nota=(metadatos.get("nota") or "")[:1000], origen=(metadatos.get("origen") or "")[:10])
    except IntegrityError:                         # carrera: llegó la misma foto por otro camino
        captura = Captura.objects.get(cuenta_id=usuario.pk, sha256=archivo.sha256)
        return {"captura_id": captura.pk, "sha256": captura.sha256, "duplicada": True}
    return {"captura_id": captura.pk, "sha256": captura.sha256, "duplicada": False}
