"""Fuentes SHX de CAD: las sube quien administra (de su AutoCAD), se guardan en Herramientas y la página del plano las registra en el
editor cada vez que lo abre (`font_register`, solo en memoria del editor; docs/cad.md, «Fuentes SHX»).

Dónde viven: `TV_HERRAMIENTAS_ARCHIVOS/fuentes/<sha256>.shx`, fuera de `cad/` para que entren en el respaldo normal de Herramientas
(los planos no se respaldan; las fuentes sí: son pocos kB y no hay que volver a subirlas tras un cambio de servidor).
"""
from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from ..comun import autoria
from ..comun.errores import ErrorUsuario
from ..models import FuenteCad
from . import servicio

MAX_BYTES = 2 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024
_FIRMAS = {b"AutoCAD-86 shapes 1.0": "shapes", b"AutoCAD-86 shapes 1.1": "shapes", b"AutoCAD-86 unifont 1.0": "unifont", b"AutoCAD-86 bigfont 1.0": "bigfont"}
_NOMBRE = re.compile(r"^[a-z0-9][a-z0-9 _.\-]{0,59}\.shx$")


def carpeta() -> Path:
    c = servicio.raiz() / "fuentes"
    c.mkdir(parents=True, exist_ok=True)
    return c


def ruta(f: FuenteCad) -> Path:
    return carpeta() / f"{f.sha256}.shx"


def nombre_limpio(nombre: str) -> str:
    n = Path(str(nombre or "").replace("\\", "/")).name.strip().lower()
    if not n.endswith(".shx"):
        raise ErrorUsuario(f"«{Path(str(nombre)).name}» no es una fuente SHX.")
    if not _NOMBRE.match(n):
        raise ErrorUsuario(f"El nombre «{n}» tiene caracteres que no se pueden usar.")
    return n


def leer(datos: bytes) -> tuple[str, int | None]:
    """(tipo, glifos) de un archivo SHX por su encabezado; ErrorUsuario si no lo es. Los glifos se leen de las fuentes de trazos."""
    cabeza = datos[:40]
    for firma, tipo in _FIRMAS.items():
        if cabeza.startswith(firma):
            fin = datos.find(b"\x1a", len(firma), len(firma) + 4)
            if fin < 0:
                break
            glifos = None
            if tipo == "shapes" and len(datos) >= fin + 7:
                _, _, cuantos = struct.unpack_from("<HHH", datos, fin + 1)
                glifos = max(0, cuantos - 1) if cuantos else None   # la primera forma es el encabezado de la fuente
            return tipo, glifos
    raise ErrorUsuario("El archivo no es una fuente SHX de AutoCAD.")


def guardar(nombre: str, datos: bytes, usuario) -> FuenteCad:
    """Agrega o reemplaza (mismo nombre) una fuente."""
    nombre = nombre_limpio(nombre)
    if not datos:
        raise ErrorUsuario(f"«{nombre}» está vacío.")
    if len(datos) > MAX_BYTES:
        raise ErrorUsuario(f"«{nombre}» pasa de {MAX_BYTES // (1024 * 1024)} MB.")
    tipo, glifos = leer(datos)
    sha = hashlib.sha256(datos).hexdigest()
    with transaction.atomic():
        previa = FuenteCad.objects.select_for_update().filter(nombre=nombre).first()
        total = sum(FuenteCad.objects.exclude(nombre=nombre).values_list("bytes", flat=True))
        if total + len(datos) > MAX_TOTAL:
            raise ErrorUsuario(f"Las fuentes no pueden pasar de {MAX_TOTAL // (1024 * 1024)} MB en total. Elimina las que no uses.")
        destino = carpeta() / f"{sha}.shx"
        temporal = destino.with_suffix(".tmp")
        temporal.write_bytes(datos)
        temporal.replace(destino)
        viejo = ruta(previa) if previa and previa.sha256 != sha else None
        f = previa or FuenteCad(nombre=nombre)
        f.tipo, f.glifos, f.bytes, f.sha256, f.error = tipo, glifos, len(datos), sha, ""
        f.subida_por_nombre = autoria.de_usuario(usuario)[0]
        f.actualizada = timezone.now()
        f.save()
    if viejo and not FuenteCad.objects.filter(sha256=viejo.stem).exists():
        viejo.unlink(missing_ok=True)
    return f


def eliminar(f: FuenteCad) -> None:
    sha = f.sha256
    f.delete()
    if not FuenteCad.objects.filter(sha256=sha).exists():
        (carpeta() / f"{sha}.shx").unlink(missing_ok=True)


#: Por qué el editor no pudo usar una fuente: lista cerrada (la página no manda texto libre al catálogo). Lo que no esté aquí es «rechazada».
MOTIVOS = {
    "rechazada": "El editor no la pudo usar. Revisa que sea una fuente válida o vuelve a subirla.",
    "sin_letras": "La fuente no trae letras que el editor pueda dibujar.",
}


def informar(nombre: str, ok: bool, glifos=None, codigo: str = "") -> None:
    """Lo que dijo el editor al registrarla (letras o rechazo, con un código de `MOTIVOS`); solo cambia si es distinto."""
    f = FuenteCad.objects.filter(nombre=nombre).first()
    if f is None:
        return
    cambios = {}
    if ok and isinstance(glifos, int) and 0 <= glifos <= 1_000_000 and f.glifos != glifos:
        cambios["glifos"] = glifos
    nuevo_error = "" if ok else MOTIVOS.get(codigo, MOTIVOS["rechazada"])
    if f.error != nuevo_error:
        cambios["error"] = nuevo_error
    if cambios:
        FuenteCad.objects.filter(pk=f.pk).update(**cambios)


def para_editor() -> list[dict]:
    """Las fuentes que la página registra en el editor, con la huella en la dirección (caché sin preguntar)."""
    from django.urls import reverse

    return [{"nombre": f.nombre, "url": reverse("herramientas:cad_fuente", args=[f.nombre]) + f"?v={f.sha256[:16]}",
             "informe": reverse("herramientas:cad_fuente_informe", args=[f.nombre]), "glifos": f.glifos, "error": f.error}
            for f in FuenteCad.objects.all() if ruta(f).is_file()]
