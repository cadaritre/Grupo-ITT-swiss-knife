"""Cotizaciones (herramientas-web.md §4.1): validación, sincronización de borradores, PDF, corrector y machotes.

El modelo y el PDF son los del escritorio (`dominio.quotation_models` / `quotation_pdf`); aquí solo se administra a quién
pertenecen, cómo se guardan y cómo se resuelven los conflictos.
"""
from __future__ import annotations

import math
import re
import tempfile
import uuid
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.db import IntegrityError, transaction
from django.utils import timezone
from tvplataforma import ajustes

from ..comun import capturas, diccionarios, perfiles, sincronizacion
from ..dominio import quotation_models as qm
from ..dominio import quotation_pdf
from ..dominio.quotation_i18n import STANDARD_UNITS
from ..models import Captura, Cotizacion, MachoteCuenta

MAX_BYTES_DATOS = 400_000
MAX_CONCEPTOS = 200
MAX_INCISOS = 60
MAX_IMAGENES = 24
MAX_TEXTO = 6000
IDIOMAS = ("es", "en")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_CAPTURA = re.compile(r"^captura:([0-9a-f]{64})$")
UNIDADES = {"es": [*STANDARD_UNITS.keys()], "en": [*STANDARD_UNITS.values()]}


class CotizacionInvalida(ValueError):
    """Los datos no son una cotización válida (el mensaje se muestra a la persona)."""


# -- Validación ----------------------------------------------------------------------------------------------------

def _texto(valor, nombre: str, maximo: int = MAX_TEXTO) -> str:
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise CotizacionInvalida(f"«{nombre}» debe ser texto.")
    if len(valor) > maximo:
        raise CotizacionInvalida(f"«{nombre}» es demasiado largo (máximo {maximo} caracteres).")
    return valor.replace("\x00", "")


def _numero(valor, nombre: str, minimo: float, maximo: float) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise CotizacionInvalida(f"«{nombre}» debe ser un número.")
    if not math.isfinite(valor) or not minimo <= valor <= maximo:
        raise CotizacionInvalida(f"«{nombre}» debe estar entre {minimo:g} y {maximo:g}.")
    return float(valor)


def validar(crudo) -> qm.QuoteData:
    """Convierte el JSON del navegador en `QuoteData`, rechazando lo que el escritorio nunca produciría."""
    import json

    if not isinstance(crudo, dict):
        raise CotizacionInvalida("La cotización debe ser un objeto JSON.")
    if len(json.dumps(crudo)) > MAX_BYTES_DATOS:
        raise CotizacionInvalida("La cotización es demasiado grande.")
    base = qm.QuoteData()
    q = qm.QuoteData()
    for campo in ("quote_number", "quote_date", "client_name", "contact", "email", "phone", "location", "project_title", "notes", "delivery_time",
                  "payment_terms", "bank", "clabe", "account", "prepared_by"):
        if campo in crudo:
            setattr(q, campo, _texto(crudo[campo], campo, 400 if campo != "notes" else MAX_TEXTO))
        else:
            setattr(q, campo, getattr(base, campo))
    moneda = _texto(crudo.get("currency", base.currency), "currency", 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", moneda):
        raise CotizacionInvalida("La moneda debe ser un código de 3 letras (MXN, USD).")
    q.currency = moneda
    q.language = crudo.get("language", "es")
    if q.language not in IDIOMAS:
        raise CotizacionInvalida("El idioma debe ser es o en.")
    q.include_vat = bool(crudo.get("include_vat", True))
    q.vat_rate = _numero(crudo.get("vat_rate", 16.0), "IVA", 0, 100)
    q.advance_percent = _numero(crudo.get("advance_percent", 50.0), "anticipo", 0, 100)
    q.validity_days = int(_numero(crudo.get("validity_days", 15), "vigencia", 0, 3650))
    q.spelling_checked = bool(crudo.get("spelling_checked", False))
    q.version = 1
    conceptos = crudo.get("items", [])
    if not isinstance(conceptos, list) or len(conceptos) > MAX_CONCEPTOS:
        raise CotizacionInvalida(f"Una cotización admite hasta {MAX_CONCEPTOS} conceptos.")
    q.items = []
    for n, c in enumerate(conceptos, 1):
        if not isinstance(c, dict):
            raise CotizacionInvalida(f"El concepto {n} no es válido.")
        incisos = c.get("subitems", [])
        if not isinstance(incisos, list) or len(incisos) > MAX_INCISOS:
            raise CotizacionInvalida(f"El concepto {n} admite hasta {MAX_INCISOS} incisos.")
        q.items.append(qm.QuoteItem(
            title=_texto(c.get("title", ""), f"título del concepto {n}", 400), description=_texto(c.get("description", ""), f"descripción del concepto {n}"),
            subitems=[_texto(i, f"inciso del concepto {n}", 1000) for i in incisos], unit=_texto(c.get("unit", "Lote"), "unidad", 40) or "Lote",
            quantity=_numero(c.get("quantity", 1), f"cantidad del concepto {n}", 0, 1e9), unit_price=_numero(c.get("unit_price", 0), f"precio del concepto {n}", 0, 1e11)))
    imagenes = crudo.get("images", [])
    if not isinstance(imagenes, list) or len(imagenes) > MAX_IMAGENES:
        raise CotizacionInvalida(f"Una cotización admite hasta {MAX_IMAGENES} imágenes de referencia.")
    q.images = []
    for n, i in enumerate(imagenes, 1):
        ruta = _texto(i.get("path", "") if isinstance(i, dict) else "", f"imagen {n}", 200)
        # Las fotos del teléfono llegan por la cola como «captura:<sha256>». Las rutas del escritorio (C:\...) no existen en el servidor: se conservan
        # tal cual en el editable pero no se dibujan.
        q.images.append(qm.QuoteImage(path=ruta, caption=_texto(i.get("caption", ""), f"pie de la imagen {n}", 300)))
    return q


def a_datos(q: qm.QuoteData) -> dict:
    return q.to_dict()


def _resumen(q: qm.QuoteData) -> dict:
    return {"folio": q.quote_number[:60], "cliente": q.client_name[:160], "proyecto": q.project_title[:200], "moneda": q.currency, "total": Decimal(str(q.total))}


def siguiente_folio(cuenta_id: int, hoy: date | None = None) -> str:
    hoy = hoy or timezone.localdate()
    patron = re.compile(rf"^COT-{hoy.year}-(\d{{1,6}})$")
    mayor = 0
    for folio in Cotizacion.objects.filter(cuenta_id=cuenta_id, folio__startswith=f"COT-{hoy.year}-").values_list("folio", flat=True):
        m = patron.match(folio)
        if m:
            mayor = max(mayor, int(m.group(1)))
    return f"COT-{hoy.year}-{mayor + 1:04d}"


# -- Guardado y sincronización -----------------------------------------------------------------------------------------

def resumen_fila(c: Cotizacion) -> dict:
    return {"id": str(c.id), "id_cliente": str(c.id_cliente) if c.id_cliente else None, "revision": c.revision, "folio": c.folio, "cliente": c.cliente,
            "proyecto": c.proyecto, "moneda": c.moneda, "total": float(c.total), "actualizada": c.actualizada.isoformat(), "copia": c.copia_de_id is not None,
            "perfil": c.perfil}


def _aplicar(c: Cotizacion, q: qm.QuoteData, perfil: str) -> None:
    c.datos = a_datos(q)
    r = _resumen(q)
    c.folio, c.cliente, c.proyecto, c.moneda, c.total, c.perfil = r["folio"], r["cliente"], r["proyecto"], r["moneda"], r["total"], perfil
    c.actualizada = timezone.now()


def guardar_nueva(usuario, datos: dict, perfil: str, id_cliente: uuid.UUID | None = None) -> Cotizacion:
    q = validar(datos)
    c = Cotizacion(cuenta_id=usuario.pk, id_cliente=id_cliente)
    _aplicar(c, q, perfil)
    c.save()
    return c


def sincronizar(usuario, id_cliente: uuid.UUID, servidor_id: uuid.UUID | None, revision_base: int | None, datos: dict, perfil: str) -> dict:
    """Aplica un borrador del teléfono (ver `comun.sincronizacion`). Nunca pisa: ante un conflicto guarda una COPIA."""
    q = validar(datos)

    def copia(obj):
        q.project_title = (q.project_title.rstrip() + " (desde el teléfono)").strip()
        _aplicar(obj, q, perfil)

    return sincronizacion.sincronizar(Cotizacion, usuario, id_cliente, servidor_id, revision_base, a_datos(q),
                                      aplicar=lambda obj: _aplicar(obj, q, perfil), aplicar_copia=copia, error=CotizacionInvalida)


def duplicar(usuario, c: Cotizacion) -> Cotizacion:
    q = validar(c.datos)
    q.quote_number = siguiente_folio(usuario.pk)
    q.quote_date = timezone.localdate().strftime("%d/%m/%Y")
    q.spelling_checked = False
    copia = Cotizacion(cuenta_id=usuario.pk)
    _aplicar(copia, q, c.perfil)
    copia.save()
    return copia


# -- PDF -----------------------------------------------------------------------------------------------------------------

def _limpiar(texto: str, respaldo: str) -> str:
    limpio = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", texto or "")
    limpio = " ".join(limpio.split()).strip(" .")
    return limpio[:90] or respaldo


def nombre_pdf(q: qm.QuoteData) -> str:
    """Igual que el escritorio: «COTIZACION <folio> - <proyecto>.pdf»."""
    return f"COTIZACION {_limpiar(q.quote_number, 'SIN FOLIO')} - {_limpiar(q.project_title, 'SERVICIOS')}.pdf"


def _resolver_imagenes(usuario, q: qm.QuoteData) -> list[str]:
    """Cambia «captura:<sha>» por la ruta de la foto de ESTA cuenta; devuelve las que aún no llegan al servidor."""
    pendientes = []
    raiz = Path(ajustes.valor("TV_HERRAMIENTAS_ARCHIVOS"))
    resueltas = []
    for imagen in q.images:
        m = _CAPTURA.match(imagen.path)
        if not m:
            continue
        captura = Captura.objects.filter(cuenta_id=usuario.pk, sha256=m.group(1)).first()
        if captura is None or not (raiz / captura.ruta).is_file():
            pendientes.append(imagen.caption or m.group(1)[:8])
            continue
        resueltas.append(qm.QuoteImage(path=str(raiz / captura.ruta), caption=imagen.caption))
    q.images = resueltas
    return pendientes


def generar_pdf(usuario, datos: dict) -> tuple[bytes, str, list[str]]:
    """PDF de la cotización con la identidad del perfil activo de la cuenta. Devuelve (bytes, nombre, imágenes pendientes de subir)."""
    from ..dominio import branding

    q = validar(datos)
    nombre = nombre_pdf(q)
    pendientes = _resolver_imagenes(usuario, q)
    perfil = perfiles.perfil_activo(usuario)
    with perfiles.activar(perfil):
        logo = branding.active_profile().logo_path
        with tempfile.TemporaryDirectory() as carpeta:
            destino = Path(carpeta) / "cotizacion.pdf"
            quotation_pdf.generate_quote_pdf(q, destino, logo=logo)
            return destino.read_bytes(), nombre, pendientes


def traducir(datos: dict, idioma: str) -> dict:
    """Cambia el idioma de la cotización como el escritorio: solo traduce el texto de fábrica, nunca lo que escribió la persona."""
    from ..dominio.quotation_i18n import translate_standard_quote_values

    if idioma not in IDIOMAS:
        raise CotizacionInvalida("El idioma debe ser es o en.")
    q = validar(datos)
    translate_standard_quote_values(q, idioma)
    q.language = idioma
    q.spelling_checked = False
    return a_datos(q)


def ruta_foto(usuario, sha256: str) -> Path | None:
    """Archivo de una foto de ESTA cuenta (para las miniaturas del editor)."""
    return capturas.ruta(usuario.pk, sha256)


# -- Corrector -------------------------------------------------------------------------------------------------------------

def revisar_ortografia(usuario, idioma: str, campos: list[dict]) -> list[dict]:
    """`campos`: `[{"id": "...", "valor": "..."}]`. Devuelve las palabras dudosas con su posición y sugerencias."""
    from ..dominio.spellcheck import ReviewField

    if idioma not in IDIOMAS:
        raise CotizacionInvalida("Idioma desconocido.")
    if not isinstance(campos, list) or len(campos) > 800:
        raise CotizacionInvalida("Demasiados campos para revisar.")
    servicio = diccionarios.corrector(usuario.pk, idioma)
    revisados = [ReviewField(str(c.get("id", ""))[:80], _texto(c.get("valor", ""), "texto", MAX_TEXTO), lambda _v: None) for c in campos if isinstance(c, dict)]
    documentos, problemas = servicio.scan(revisados)
    salida = []
    for p in problemas:
        doc = documentos[p.document_index]
        m = doc.matches[p.token_index]
        salida.append({"campo": doc.field.label, "inicio": m.start(), "fin": m.end(), "palabra": p.word, "contexto": doc.context(p.token_index),
                       "sugerencias": servicio.suggestions(p.word)})
    return salida


# -- Machotes -----------------------------------------------------------------------------------------------------------------

def plantillas_integradas() -> dict:
    return {"es": {n: asdict(i) for n, i in qm.CONCEPT_TEMPLATES.items()}, "en": {n: asdict(i) for n, i in qm.CONCEPT_TEMPLATES_EN.items()}}


def validar_concepto(crudo) -> dict:
    q = validar({"items": [crudo]})
    return asdict(q.items[0])


def fila_machote(m: MachoteCuenta) -> dict:
    return {"id": str(m.plantilla_id), "nombre": m.nombre, "idioma": m.idioma, "concepto": m.concepto, "actualizado": m.actualizado.isoformat(), "tiene_anterior": bool(m.anterior)}


def guardar_machote(usuario, nombre: str, idioma: str, concepto: dict, plantilla_id: str | None = None) -> MachoteCuenta:
    limpio = " ".join(str(nombre).split()).strip()
    if not limpio:
        raise CotizacionInvalida("Escribe un nombre para el machote.")
    if len(limpio) > 160:
        raise CotizacionInvalida("El nombre del machote es demasiado largo.")
    idioma = idioma if idioma in IDIOMAS else "es"
    concepto = validar_concepto(concepto)
    propios = MachoteCuenta.objects.filter(cuenta_id=usuario.pk)
    existente = propios.filter(plantilla_id=plantilla_id).first() if plantilla_id else None
    choque = propios.filter(nombre__iexact=limpio).first()
    if choque is not None and choque != existente:
        raise CotizacionInvalida(f"Ya existe un machote personalizado llamado «{limpio}».")
    if existente is None:
        return MachoteCuenta.objects.create(cuenta_id=usuario.pk, nombre=limpio, idioma=idioma, concepto=concepto)
    existente.anterior = existente.concepto            # al sobrescribir se guarda la versión anterior
    existente.nombre, existente.idioma, existente.concepto, existente.actualizado = limpio, idioma, concepto, timezone.now()
    existente.save()
    return existente


def exportar_machotes(usuario) -> dict:
    from ..dominio.quotation_templates import SCHEMA_VERSION, TEMPLATE_TOOL_ID

    return {"tool": TEMPLATE_TOOL_ID, "schema_version": SCHEMA_VERSION, "exported_at": timezone.now().isoformat(timespec="seconds"),
            "templates": [{"id": str(m.plantilla_id), "name": m.nombre, "language": m.idioma, "item": m.concepto,
                           "created_at": m.creado.isoformat(timespec="seconds"), "updated_at": m.actualizado.isoformat(timespec="seconds")}
                          for m in MachoteCuenta.objects.filter(cuenta_id=usuario.pk)]}


def importar_machotes(usuario, datos: bytes) -> tuple[int, int]:
    """Importa una colección (esquema del escritorio). Los nombres repetidos se renombran «(importado N)», como el escritorio."""
    from ..dominio.quotation_templates import CustomConceptTemplateStore

    with tempfile.TemporaryDirectory() as carpeta:
        ruta = Path(carpeta) / "m.json"
        ruta.write_bytes(datos)
        try:
            registros = CustomConceptTemplateStore._read(ruta)
        except (OSError, ValueError, TypeError) as exc:
            raise CotizacionInvalida(str(exc)) from exc
    nombres = {n.casefold() for n in MachoteCuenta.objects.filter(cuenta_id=usuario.pk).values_list("nombre", flat=True)}
    ids = set(MachoteCuenta.objects.filter(cuenta_id=usuario.pk).values_list("plantilla_id", flat=True))
    agregados = renombrados = 0
    with transaction.atomic():
        for r in registros:
            nombre, n, cambiado = r.name, 2, False
            while nombre.casefold() in nombres:
                nombre, n, cambiado = f"{r.name} (importado {n})", n + 1, True
            try:
                pid = uuid.UUID(str(r.template_id))
            except ValueError:
                pid = uuid.uuid4()
            if pid in ids:
                pid = uuid.uuid4()
            MachoteCuenta.objects.create(cuenta_id=usuario.pk, plantilla_id=pid, nombre=nombre[:160], idioma=r.language, concepto=validar_concepto(asdict(r.item)))
            nombres.add(nombre.casefold())
            ids.add(pid)
            agregados += 1
            renombrados += cambiado
    return agregados, renombrados


def importar_editable(usuario, contenido: bytes, perfil: str) -> Cotizacion:
    """Importa el JSON editable del escritorio (`EDITABLE_COTIZACION_*.json`)."""
    import json

    try:
        crudo = json.loads(contenido.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CotizacionInvalida("El archivo no es un JSON válido.") from exc
    q = validar(crudo)
    q.spelling_checked = False
    c = Cotizacion(cuenta_id=usuario.pk)
    _aplicar(c, q, perfil)
    c.save()
    return c
