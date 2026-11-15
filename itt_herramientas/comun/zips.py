"""Descarga de varios documentos en un ZIP (reportes, cotizaciones o, en supervisión, de cualquier cuenta).

El ZIP se arma en una tarea: los reportes sin PDF o con un PDF de una versión anterior se generan primero (con su avance) y
cada PDF entra al ZIP desde el disco, uno a la vez, así la memoria no crece con el tamaño del lote. El archivo queda en
`zips/` con el id de la cuenta que lo pidió (solo ella lo descarga) y se borra al día siguiente.
"""
from __future__ import annotations

import re
import time
import uuid
import zipfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.utils import timezone
from tvplataforma.tareas import progreso

from . import capturas
from .errores import ErrorUsuario

MAXIMO = 50
TIPOS = ("reporte", "cotizacion", "plano")
PREFIJOS = {"reportes": "Reportes", "cotizaciones": "Cotizaciones", "documentos": "Documentos", "planos": "Planos"}
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
VIDA_HORAS = 24


class LoteInvalido(ErrorUsuario):
    pass


def validar_lote(elementos) -> list[tuple[str, str]]:
    """`[[tipo, id], …]` del navegador → lista limpia (sin repetidos, en el mismo orden). Máximo `MAXIMO`."""
    if not isinstance(elementos, list) or not elementos:
        raise LoteInvalido("Elige al menos un documento.")
    limpios: list[tuple[str, str]] = []
    for e in elementos:
        if not (isinstance(e, (list, tuple)) and len(e) == 2 and e[0] in TIPOS):
            raise LoteInvalido("La selección no es válida. Recarga la página e inténtalo de nuevo.")
        try:
            pk = str(uuid.UUID(str(e[1])))
        except ValueError:
            raise LoteInvalido("La selección no es válida. Recarga la página e inténtalo de nuevo.") from None
        if (e[0], pk) not in limpios:
            limpios.append((e[0], pk))
    if len(limpios) > MAXIMO:
        raise LoteInvalido(f"Elegiste {len(limpios)} documentos; el máximo por descarga es {MAXIMO}. Descárgalos en varias partes.")
    return limpios


def _carpeta() -> Path:
    carpeta = capturas.raiz() / "zips"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def ruta(cuenta_id: int, token: str, tipo: str) -> Path:
    return _carpeta() / f"{cuenta_id}-{token}-{tipo}.zip"


def nuevo_token() -> str:
    return uuid.uuid4().hex


def de_la_cuenta(cuenta_id: int, token: str) -> tuple[Path, str] | None:
    """El ZIP `token` de ESTA cuenta y su nombre para descargarlo («Reportes 2026-11-14.zip»), o None."""
    if not _TOKEN.match(token or ""):
        return None
    for archivo in _carpeta().glob(f"{int(cuenta_id)}-{token}-*.zip"):
        tipo = archivo.stem.rsplit("-", 1)[-1]
        fecha = timezone.localtime(timezone.datetime.fromtimestamp(archivo.stat().st_mtime, tz=timezone.get_current_timezone())).date()
        return archivo, f"{PREFIJOS.get(tipo, 'Documentos')} {fecha.isoformat()}.zip"
    return None


def _unico(nombre: str, usados: set[str]) -> str:
    """«Reporte.pdf», «Reporte (2).pdf», … sin distinguir mayúsculas (Windows y macOS no las distinguen)."""
    base, punto, ext = nombre.rpartition(".")
    if not punto:
        base, ext = nombre, "pdf"
    candidato, n = f"{base}.{ext}", 2
    while candidato.casefold() in usados:
        candidato = f"{base} ({n}).{ext}"
        n += 1
    usados.add(candidato.casefold())
    return candidato


def armar(token: str, cuenta_id: int, elementos: list, tipo: str, supervision: bool = False) -> dict:
    """Arma el ZIP (lo llama la tarea). Sin supervisión, solo entran documentos de `cuenta_id`; los ajenos se ignoran."""
    from ..cotizaciones import servicio as cot
    from ..models import Cotizacion, Perfil, Reporte
    from ..reportes import servicio as rep
    from . import perfiles

    lote = validar_lote(elementos)
    usuario = get_user_model().objects.filter(pk=cuenta_id).first()
    destino = ruta(cuenta_id, token, tipo)
    temporal = destino.with_suffix(".tmp")
    usados: set[str] = set()
    omitidos: list[str] = []
    incluidos = 0
    total = len(lote)
    with zipfile.ZipFile(temporal, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:   # los PDF ya vienen comprimidos
        for i, (clase, pk) in enumerate(lote):
            def informar(fraccion, mensaje="", i=i):
                progreso(0.02 + 0.95 * (i + min(1.0, max(0.0, fraccion))) / total, f"Documento {i + 1} de {total}" + (f" · {mensaje}" if mensaje else ""))

            informar(0)
            if (clase == "plano") != (tipo == "planos"):           # un lote de CAD lleva solo planos y los demás lotes nunca llevan planos
                continue
            if clase == "plano":                                   # biblioteca compartida de CAD: cada plano en su versión vigente
                from ..cad import servicio as cad
                from ..models import Plano

                p = Plano.objects.filter(pk=pk, eliminado__isnull=True).first()
                v = cad.version_vigente(p) if p else None
                if v is None or not cad.ruta_version(v).is_file():
                    continue
                zf.write(cad.ruta_version(v), _unico(f"{p.nombre}.{v.formato}", usados))
                incluidos += 1
                continue
            if clase == "cotizacion":
                consulta = Cotizacion.objects.filter(pk=pk) if supervision else Cotizacion.objects.filter(pk=pk, cuenta_id=cuenta_id)
                c = consulta.first()
                if c is None:
                    continue
                try:
                    if supervision or usuario is None:
                        perfil = Perfil.objects.filter(clave=c.perfil).first() or Perfil.objects.get(clave=perfiles.PERFIL_POR_OMISION)
                        contenido, nombre, _ = cot.pdf_de(c.cuenta_id, perfil, c.datos)
                    else:
                        contenido, nombre, _ = cot.generar_pdf(usuario, c.datos)
                except cot.CotizacionInvalida as exc:
                    omitidos.append(f"{c.folio or c.proyecto or 'Cotización'}: {exc}")
                    continue
                zf.writestr(_unico(nombre, usados), contenido)
                incluidos += 1
                continue
            consulta = Reporte.objects.filter(pk=pk) if supervision else Reporte.objects.filter(pk=pk, cuenta_id=cuenta_id)
            r = consulta.first()
            if r is None:
                continue
            archivo = rep.pdf_vigente(r, supervision=supervision)
            if archivo is None:
                try:
                    archivo = rep.generar_copia(r, informar) if supervision else rep.generar(r, informar)
                except rep.ReporteInvalido as exc:
                    omitidos.append(f"{r.titulo or 'Reporte'}: {exc}")
                    continue
            zf.write(archivo, _unico(rep.nombre_pdf(r.titulo or "Reporte"), usados))
            incluidos += 1
    if not incluidos:
        temporal.unlink(missing_ok=True)
        raise LoteInvalido("No se pudo incluir ningún documento. " + (omitidos[0] if omitidos else "Ya no existen."))
    temporal.replace(destino)
    progreso(1.0, "Listo")
    que = "plano" + ("s" if incluidos != 1 else "") if tipo == "planos" else "PDF"
    mensaje = f"ZIP listo: {incluidos} {que}." + (f" Sin incluir: {len(omitidos)}." if omitidos else "")
    return {"mensaje": mensaje, "incluidos": incluidos, "omitidos": omitidos[:MAXIMO]}


def limpiar() -> int:
    """`tv_limpiar` (diario): borra los ZIP de más de un día y los que quedaron a medias."""
    carpeta = capturas.raiz() / "zips"
    if not carpeta.is_dir():
        return 0
    limite = time.time() - VIDA_HORAS * 3600
    n = 0
    for archivo in carpeta.iterdir():
        if archivo.suffix in (".zip", ".tmp") and archivo.stat().st_mtime < limite:
            archivo.unlink(missing_ok=True)
            n += 1
    return n


def responder(request, elementos, tipo: str, supervision: bool = False):
    """Vista común: valida el lote y encola la tarea que arma el ZIP. 202 con la tarea; 422 con un mensaje si no se puede."""
    from django.http import JsonResponse

    from ..tareas import armar_zip

    try:
        lote = validar_lote(elementos)
    except LoteInvalido as exc:
        return JsonResponse({"error": str(exc)}, status=422)
    tarea = armar_zip.encolar(nuevo_token(), request.user.pk, [list(e) for e in lote], tipo, supervision, usuario=request.user)
    respuesta = JsonResponse({"tarea": str(tarea.pk), "terminada": tarea.ha_terminado, "estado": tarea.estado, "resultado": tarea.resultado,
                              "error": tarea.error, "fraccion": tarea.fraccion, "mensaje": tarea.mensaje}, status=202)
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta


def cuerpo_json(request) -> dict:
    import json

    if len(request.body) > 64_000:
        raise LoteInvalido("La selección es demasiado grande.")
    try:
        datos = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        raise LoteInvalido("La petición no es válida.") from None
    if not isinstance(datos, dict):
        raise LoteInvalido("La petición no es válida.")
    return datos
