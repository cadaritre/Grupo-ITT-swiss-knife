"""Reportes fotográficos (herramientas-web.md §4.2): validación del borrador, sincronización y PDF.

El PDF es el de `dominio.pdf_generator.generate_report` (el mismo del escritorio) con la identidad del perfil de la cuenta.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import date, datetime
from pathlib import Path

from django.utils import timezone
from tvplataforma.tareas import cancelada, progreso

from ..comun import autoria, capturas, perfiles, sincronizacion
from ..dominio import document_texts, pdf_generator
from ..dominio.metadata import PhotoInfo
from ..models import Captura, Perfil, Reporte

MAX_FOTOS = 150
MAX_NOTA = 1000
FOTOS_POR_PAGINA = pdf_generator.PHOTOS_PER_PAGE                      # 1, 2, 4 o 6; sin la llave (reportes de antes), 1
_SHA = re.compile(r"^[0-9a-f]{64}$")
_HORA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


class ReporteInvalido(ValueError):
    """Los datos no son un reporte válido (el mensaje se muestra a la persona)."""


class FotosPendientes(ReporteInvalido):
    """Faltan fotos por subir al servidor: no se puede generar el PDF todavía."""

    def __init__(self, faltan: int):
        super().__init__(f"Faltan {faltan} foto{'s' if faltan != 1 else ''} por subir al servidor. Con señal se suben solas; vuelve a intentarlo cuando el contador llegue a 0.")
        self.faltan = faltan


def _texto(valor, nombre: str, maximo: int) -> str:
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise ReporteInvalido(f"«{nombre}» debe ser texto.")
    if len(valor) > maximo:
        raise ReporteInvalido(f"«{nombre}» es demasiado largo (máximo {maximo} caracteres).")
    return valor.replace("\x00", "")


def _coordenada(valor, nombre: str, limite: float):
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) or abs(valor) > limite:
        raise ReporteInvalido(f"«{nombre}» no es una coordenada válida.")
    return round(float(valor), 6)


def validar(crudo) -> dict:
    """JSON del navegador -> dict normalizado (mismas llaves y tipos siempre, para poder compararlo con lo guardado)."""
    if not isinstance(crudo, dict):
        raise ReporteInvalido("El reporte debe ser un objeto JSON.")
    titulo = _texto(crudo.get("titulo", ""), "título", 200).strip()
    cliente = " ".join(_texto(crudo.get("cliente", ""), "cliente", 160).split())
    fecha = crudo.get("fecha") or timezone.localdate().isoformat()
    try:
        fecha = date.fromisoformat(str(fecha)).isoformat()
    except ValueError:
        raise ReporteInvalido("La fecha no es válida (aaaa-mm-dd).") from None
    fotos = crudo.get("fotos", [])
    if not isinstance(fotos, list) or len(fotos) > MAX_FOTOS:
        raise ReporteInvalido(f"Un reporte admite hasta {MAX_FOTOS} fotos.")
    salida, vistas = [], set()
    for n, f in enumerate(fotos, 1):
        if not isinstance(f, dict) or not _SHA.match(str(f.get("sha256", ""))):
            raise ReporteInvalido(f"La foto {n} no es válida.")
        sha = f["sha256"]
        if sha in vistas:
            raise ReporteInvalido(f"La foto {n} está repetida.")
        vistas.add(sha)
        tomada = _texto(f.get("tomada", ""), f"hora de la foto {n}", 32)
        if tomada and not _HORA.match(tomada):
            raise ReporteInvalido(f"La hora de la foto {n} no es válida.")
        lat, lon = _coordenada(f.get("lat"), f"latitud de la foto {n}", 90), _coordenada(f.get("lon"), f"longitud de la foto {n}", 180)
        if (lat is None) != (lon is None):
            raise ReporteInvalido(f"La foto {n} tiene solo una coordenada.")
        precision = f.get("precision")
        if precision is not None and (isinstance(precision, bool) or not isinstance(precision, (int, float)) or not math.isfinite(precision) or not 0 <= precision <= 1e6):
            raise ReporteInvalido(f"La precisión de la foto {n} no es válida.")
        salida.append({"sha256": sha, "nota": _texto(f.get("nota", ""), f"nota de la foto {n}", MAX_NOTA), "tomada": tomada, "lat": lat, "lon": lon,
                       "precision": None if precision is None else round(float(precision), 1), "sin_ubicacion": lat is None,
                       "nombre": _texto(f.get("nombre", ""), f"nombre de la foto {n}", 150)})
    por_pagina = crudo.get("fotos_por_pagina", 1)
    if not isinstance(por_pagina, int) or isinstance(por_pagina, bool) or por_pagina not in FOTOS_POR_PAGINA:
        raise ReporteInvalido("«Fotos por página» debe ser 1, 2, 4 o 6.")
    return {"titulo": titulo, "cliente": cliente, "fecha": fecha, "con_croquis": bool(crudo.get("con_croquis", True)),
            "con_credenciales": bool(crudo.get("con_credenciales", False)), "fotos_por_pagina": por_pagina, "fotos": salida}


def _aplicar(r: Reporte, datos: dict, perfil: str) -> None:
    r.datos, r.perfil = datos, perfil
    r.titulo, r.cliente, r.fotos = datos["titulo"][:200], datos["cliente"][:160], len(datos["fotos"])
    r.actualizada = timezone.now()


def sincronizar(usuario, id_cliente: uuid.UUID, servidor_id: uuid.UUID | None, revision_base: int | None, datos: dict, perfil: str) -> dict:
    nuevos = validar(datos)

    def copia(obj):
        obj_datos = dict(nuevos, titulo=(nuevos["titulo"].rstrip() + " (desde el teléfono)").strip())
        _aplicar(obj, obj_datos, perfil)

    return sincronizacion.sincronizar(Reporte, usuario, id_cliente, servidor_id, revision_base, nuevos,
                                      aplicar=lambda obj: _aplicar(obj, nuevos, perfil), aplicar_copia=copia, error=ReporteInvalido, normalizar=_normalizado)


def _normalizado(datos) -> dict | None:
    """Los datos guardados con el esquema de hoy (un reporte de antes no tiene «cliente»): así se comparan con los del teléfono."""
    try:
        return validar(datos)
    except ReporteInvalido:
        return None


def resumen_fila(r: Reporte) -> dict:
    return {"id": str(r.id), "id_cliente": str(r.id_cliente) if r.id_cliente else None, "revision": r.revision, "titulo": r.titulo, "fotos": r.fotos,
            "actualizada": r.actualizada.isoformat(), "copia": r.copia_de_id is not None, "perfil": r.perfil,
            "pdf": bool(r.salida_ruta), "pdf_al_dia": bool(r.salida_ruta) and r.salida_revision == r.revision, "pdf_revision": r.salida_revision,
            "cliente": r.cliente, "autor": autoria.fila(r)}


def titulo_por_omision(perfil: Perfil) -> str:
    with perfiles.activar(perfil):
        return document_texts.report_texts()["header_title"]


def nombre_pdf(titulo: str) -> str:
    """Igual que el escritorio: letras, números, espacio, guion y guion bajo."""
    limpio = "".join(c if c.isalnum() or c in " -_" else "" for c in titulo).strip()
    return (limpio or "Reporte") + ".pdf"


def _fotos_de(cuenta_id: int, datos: dict) -> list[PhotoInfo]:
    filas = {c.sha256: c for c in Captura.objects.filter(cuenta_id=cuenta_id, sha256__in=[f["sha256"] for f in datos["fotos"]])}
    raiz = capturas.raiz()
    faltan = sum(1 for f in datos["fotos"] if f["sha256"] not in filas or not (raiz / filas[f["sha256"]].ruta).is_file())
    if faltan:
        raise FotosPendientes(faltan)
    resultado = []
    for f in datos["fotos"]:
        c = filas[f["sha256"]]
        resultado.append(PhotoInfo(path=raiz / c.ruta, taken_at=datetime.strptime(f["tomada"], "%Y-%m-%dT%H:%M:%S") if f["tomada"] else None,
                                   latitude=f["lat"], longitude=f["lon"], width=c.ancho, height=c.alto, description=f["nota"]))
    return resultado


def verificar_listo(r: Reporte) -> None:
    datos = validar(r.datos)
    if not datos["titulo"]:
        raise ReporteInvalido("Escribe un nombre para el reporte.")
    if not datos["fotos"]:
        raise ReporteInvalido("Agrega al menos una fotografía.")
    _fotos_de(r.cuenta_id, datos)


def _producir(r: Reporte, datos: dict, temporal: Path, informar=progreso) -> None:
    """Escribe el PDF del reporte en `temporal` (mismo documento que el escritorio, con el perfil guardado en el reporte)."""
    perfil = Perfil.objects.get(clave=r.perfil)
    opciones = perfiles.opciones_reporte(
        perfil, datos["titulo"], date.fromisoformat(datos["fecha"]), _fotos_de(r.cuenta_id, datos), temporal,
        incluir_credenciales=datos["con_credenciales"], include_map=datos["con_croquis"], photos_per_page=datos["fotos_por_pagina"])
    informar(0.05, "Preparando el documento…")
    extra = 1 + (1 if datos["con_credenciales"] else 0)                   # portada y, si va, la página de cédula y firma

    def avance(hecho, total):                                                # `hecho` de `total` páginas de fotos
        if cancelada():
            raise InterruptedError("Cancelado")
        informar(0.1 + 0.88 * hecho / max(total, 1), f"Página {hecho + 1} de {total + extra}")

    with perfiles.activar(perfil):
        pdf_generator.generate_report(opciones, avance)


def generar(r: Reporte, informar=progreso) -> Path:
    """Genera el PDF del reporte (se llama desde la tarea). Guarda el archivo y lo deja ligado al reporte."""
    datos = validar(r.datos)
    verificar_listo(r)
    revision = r.revision
    carpeta = capturas.raiz() / "reportes" / str(r.cuenta_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    temporal = carpeta / f"{r.id}.tmp.pdf"
    _producir(r, datos, temporal, informar)
    destino = carpeta / f"{r.id}.pdf"
    temporal.replace(destino)
    Reporte.objects.filter(pk=r.pk).update(salida_ruta=destino.relative_to(capturas.raiz()).as_posix(), salida_nombre=nombre_pdf(datos["titulo"]),
                                           salida_revision=revision, generado=timezone.now())
    informar(1.0, "Listo")
    from ..drive import servicio as drive

    drive.encolar(r.cuenta_id, "reportes", nombre_pdf(datos["titulo"]), origen=destino, clave=drive.clave_de("reporte", r.pk, revision, r.perfil))           # a Drive si la persona lo conectó (no falla el reporte si no)
    return destino


def ruta_copia(r: Reporte) -> Path:
    """PDF de supervisión de esta revisión del reporte: aparte del de la cuenta autora, que nunca se toca."""
    return capturas.raiz() / "supervision" / "reportes" / f"{r.id}-r{r.revision}-{r.perfil}.pdf"


def generar_copia(r: Reporte, informar=progreso) -> Path:
    """PDF de la revisión actual para quien supervisa: no cambia el reporte, su último PDF ni lo que va a su Google Drive."""
    datos = validar(r.datos)
    verificar_listo(r)
    destino = ruta_copia(r)
    if destino.is_file():
        informar(1.0, "Listo")
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(".tmp.pdf")
    _producir(r, datos, temporal, informar)
    temporal.replace(destino)
    for vieja in destino.parent.glob(f"{r.id}-r*.pdf"):                       # solo se guarda la de la revisión más reciente
        if vieja != destino and not vieja.name.endswith(".tmp.pdf"):
            vieja.unlink(missing_ok=True)
    informar(1.0, "Listo")
    return destino


def pdf_vigente(r: Reporte, supervision: bool = False) -> Path | None:
    """El PDF de la versión actual del reporte, si ya existe: el que generó su cuenta o (en supervisión) la copia aparte."""
    if r.salida_ruta and r.salida_revision == r.revision and (capturas.raiz() / r.salida_ruta).is_file():
        return capturas.raiz() / r.salida_ruta
    if supervision:
        copia = ruta_copia(r)
        return copia if copia.is_file() else None
    return None
