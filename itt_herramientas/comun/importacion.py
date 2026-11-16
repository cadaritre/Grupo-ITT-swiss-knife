"""Importar la configuración del escritorio (herramientas-web.md §3).

Se sube un ZIP con `Configuracion/settings.json` (versión 5), `Diccionarios/diccionario_personal.json`,
`Cotizaciones/Machotes/machotes_personalizados.json` y, si hay, el logo personalizado. Primero se lee y se muestra qué
contiene (`leer`, `vista_previa`); al confirmar se aplica solo lo elegido (`aplicar`). Nada se ejecuta ni se extrae a
disco: es JSON y bytes de imagen validados.
"""
from __future__ import annotations

import io
import json
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from django.db import transaction
from django.utils import timezone

from ..dominio.quotation_templates import CustomConceptTemplateStore
from ..models import Machote, Perfil
from . import diccionarios, errores, perfiles, preferencias

VERSION_AJUSTES = 5
MAX_ZIP_BYTES = 25 * 1024 * 1024
MAX_MIEMBROS = 300
MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_DESCOMPRIMIDO = 60 * 1024 * 1024

ELEMENTOS = {
    "identidad": "Identidad de los perfiles (nombre, sitio web, teléfono, firmante)",
    "logo": "Logo personalizado",
    "textos": "Textos de reportes y cotizaciones",
    "paleta": "Paleta de cotizaciones",
    "preferencias": "Preferencias (zona UTM, mapa base, opciones)",
    "diccionario": "Diccionario personal",
    "machotes": "Machotes de cotización",
}
#: Los que cambian configuración compartida (perfiles) solo los puede aplicar un administrador de herramientas.
SOLO_ADMIN = {"identidad", "logo", "textos", "paleta"}
CAMPOS_IDENTIDAD = {"name": "nombre", "website": "sitio_web", "phone": "telefono", "signer": "firmante", "address": "direccion", "email": "correo"}


class ImportacionInvalida(ValueError):
    pass


@dataclass
class Paquete:
    ajustes: dict = field(default_factory=dict)
    diccionario: dict | None = None
    machotes: list = field(default_factory=list)
    logos: dict[str, bytes] = field(default_factory=dict)      # nombre de archivo -> contenido
    avisos: list[str] = field(default_factory=list)


@dataclass
class Elemento:
    clave: str
    titulo: str
    detalle: str
    cantidad: int


def _json(zf: zipfile.ZipFile, nombre: str) -> object:
    info = zf.getinfo(nombre)
    if info.file_size > MAX_JSON_BYTES:
        raise ImportacionInvalida(f"{nombre} es demasiado grande.")
    try:
        return json.loads(zf.read(nombre).decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ImportacionInvalida(f"{nombre} no es un JSON válido.") from exc


def leer(datos: bytes) -> Paquete:
    """Lee y valida el ZIP del escritorio sin extraerlo. Lanza `ImportacionInvalida` si no sirve."""
    if len(datos) > MAX_ZIP_BYTES:
        raise ImportacionInvalida("El archivo pesa más de 25 MB.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile as exc:
        raise ImportacionInvalida("El archivo no es un ZIP válido.") from exc
    with zf:
        miembros = [i for i in zf.infolist() if not i.is_dir()]
        if len(miembros) > MAX_MIEMBROS or sum(i.file_size for i in miembros) > MAX_DESCOMPRIMIDO:
            raise ImportacionInvalida("El ZIP contiene demasiados archivos o es demasiado grande al descomprimirse.")
        indice: dict[str, str] = {}
        for info in miembros:
            ruta = PurePosixPath(info.filename.replace("\\", "/"))
            if ruta.is_absolute() or ".." in ruta.parts:
                raise ImportacionInvalida(f"Ruta no permitida en el ZIP: {info.filename}")
            indice[ruta.as_posix().lower()] = info.filename
        def buscar(*sufijos: str) -> str | None:
            for clave, original in indice.items():
                if any(clave.endswith(s.lower()) for s in sufijos):
                    return original
            return None

        paquete = Paquete()
        ajustes = buscar("configuracion/settings.json")
        if ajustes:
            crudo = _json(zf, ajustes)
            if not isinstance(crudo, dict):
                raise ImportacionInvalida("settings.json no tiene el formato esperado.")
            if int(crudo.get("version", 0) or 0) > VERSION_AJUSTES:
                raise ImportacionInvalida("settings.json es de una versión más reciente del escritorio que la que sabe leer la web.")
            paquete.ajustes = crudo
        diccionario = buscar("diccionarios/diccionario_personal.json")
        if diccionario:
            crudo = _json(zf, diccionario)
            valores = crudo.get("words", crudo) if isinstance(crudo, dict) else crudo
            paquete.diccionario = _palabras(valores)
        machotes = buscar("machotes/machotes_personalizados.json")
        if machotes:
            paquete.machotes = _machotes(zf.read(machotes) if zf.getinfo(machotes).file_size <= MAX_JSON_BYTES else b"")
        for clave, original in indice.items():
            if "/configuracion/" in "/" + clave and original.lower().endswith((".png", ".jpg", ".jpeg")) and zf.getinfo(original).file_size <= perfiles.MAX_IMAGEN_BYTES:
                paquete.logos[PurePosixPath(original.replace("\\", "/")).name] = zf.read(original)
    if not (paquete.ajustes or paquete.diccionario or paquete.machotes):
        raise ImportacionInvalida("El ZIP no contiene ninguna configuración del escritorio (busqué Configuracion/settings.json, Diccionarios y Machotes).")
    return paquete


def _palabras(valores) -> dict[str, list[str]]:
    if isinstance(valores, dict):
        return {i: [str(p) for p in valores.get(i, [])] for i in ("es", "en")}
    if isinstance(valores, list):                   # formato antiguo: una sola lista para los dos idiomas
        return {"es": [str(p) for p in valores], "en": [str(p) for p in valores]}
    raise ImportacionInvalida("El diccionario personal no tiene el formato esperado.")


def _machotes(datos: bytes) -> list:
    if not datos:
        raise ImportacionInvalida("La colección de machotes es demasiado grande.")
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = Path(carpeta) / "m.json"
        ruta.write_bytes(datos)
        try:
            return CustomConceptTemplateStore._read(ruta)
        except (OSError, ValueError, TypeError) as exc:
            raise ImportacionInvalida(errores.texto(exc, "La colección de machotes del archivo no es válida.")) from exc


def _logo_de(paquete: Paquete, clave: str) -> tuple[str, bytes] | None:
    ruta = paquete.ajustes.get("branding", {}).get(clave, {}).get("custom_logo", "") if isinstance(paquete.ajustes.get("branding"), dict) else ""
    nombre = PurePosixPath(str(ruta).replace("\\", "/")).name
    return (nombre, paquete.logos[nombre]) if nombre in paquete.logos else None


def vista_previa(paquete: Paquete) -> list[Elemento]:
    a = paquete.ajustes
    marca = a.get("branding", {}) if isinstance(a.get("branding"), dict) else {}
    docs = a.get("documents", {}) if isinstance(a.get("documents"), dict) else {}
    salida: list[Elemento] = []
    campos = sum(1 for k in ("grupo_itt", "tresvizo") for c in CAMPOS_IDENTIDAD if isinstance(marca.get(k), dict) and marca[k].get(c))
    if campos:
        salida.append(Elemento("identidad", ELEMENTOS["identidad"], f"Perfil activo en el escritorio: {marca.get('profile', 'grupo_itt')}", campos))
    logos = [k for k in ("grupo_itt", "tresvizo") if _logo_de(paquete, k)]
    if logos:
        salida.append(Elemento("logo", ELEMENTOS["logo"], "Perfiles: " + ", ".join(logos), len(logos)))
    textos = len(docs.get("report_texts") or {}) + sum(len((docs.get("quote_texts") or {}).get(i) or {}) for i in ("es", "en"))
    if textos:
        salida.append(Elemento("textos", ELEMENTOS["textos"], "Rótulos personalizados de reportes y cotizaciones", textos))
    if docs.get("quote_palette"):
        salida.append(Elemento("paleta", ELEMENTOS["paleta"], ", ".join(f"{k} {v}" for k, v in docs["quote_palette"].items()), len(docs["quote_palette"])))
    prefs = preferencias.validar({s: a[s] for s in preferencias.SECCIONES if isinstance(a.get(s), dict)}, estricto=False) if a else {}
    if prefs:
        salida.append(Elemento("preferencias", ELEMENTOS["preferencias"], ", ".join(sorted(prefs)), sum(len(v) for v in prefs.values())))
    if paquete.diccionario is not None:
        total = sum(len(v) for v in paquete.diccionario.values())
        if total:
            salida.append(Elemento("diccionario", ELEMENTOS["diccionario"], f"{len(paquete.diccionario['es'])} en español, {len(paquete.diccionario['en'])} en inglés", total))
    if paquete.machotes:
        salida.append(Elemento("machotes", ELEMENTOS["machotes"], ", ".join(t.name for t in paquete.machotes[:5]) + ("…" if len(paquete.machotes) > 5 else ""), len(paquete.machotes)))
    return salida


@dataclass
class Resultado:
    aplicado: dict[str, int] = field(default_factory=dict)
    omitido: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def aplicar(paquete: Paquete, cuenta_id: int, elementos: set[str], *, es_admin: bool, perfil_destino: Perfil | None = None) -> Resultado:
    """Aplica los elementos elegidos. Todo o nada (una transacción). Identidad y logo van a su perfil por clave; textos y
    paleta (globales en el escritorio) van a `perfil_destino`, por omisión el perfil activo del escritorio."""
    resultado = Resultado()
    elementos = set(elementos) & set(ELEMENTOS)
    if not es_admin:
        prohibidos = sorted(elementos & SOLO_ADMIN)
        if prohibidos:
            raise ImportacionInvalida("Solo un administrador de herramientas puede importar: " + ", ".join(ELEMENTOS[e] for e in prohibidos))
    a = paquete.ajustes
    marca = a.get("branding", {}) if isinstance(a.get("branding"), dict) else {}
    docs = a.get("documents", {}) if isinstance(a.get("documents"), dict) else {}
    activo = marca.get("profile", "grupo_itt") if marca.get("profile") in ("grupo_itt", "tresvizo") else "grupo_itt"
    with transaction.atomic():
        def destino_actual() -> Perfil | None:
            """Siempre fresco de la base: identidad y logo pudieron cambiar esa fila antes."""
            return Perfil.objects.filter(pk=perfil_destino.pk).first() if perfil_destino else Perfil.objects.filter(clave=activo).first()
        if "identidad" in elementos:
            total = 0
            for clave in ("grupo_itt", "tresvizo"):
                valores = marca.get(clave)
                perfil = Perfil.objects.filter(clave=clave).first()
                if not isinstance(valores, dict) or perfil is None:
                    continue
                for origen, campo in CAMPOS_IDENTIDAD.items():
                    texto = valores.get(origen)
                    if isinstance(texto, str) and texto.strip() and len(texto) <= 240:
                        setattr(perfil, campo, texto.strip())
                        total += 1
                perfil.actualizado = timezone.now()
                perfil.save()
            resultado.aplicado["identidad"] = total
        if "logo" in elementos:
            total = 0
            for clave in ("grupo_itt", "tresvizo"):
                logo = _logo_de(paquete, clave)
                perfil = Perfil.objects.filter(clave=clave).first()
                if logo and perfil:
                    try:
                        perfiles.guardar_imagen(perfil, "logo", logo[1])
                    except perfiles.PerfilInvalido as exc:
                        resultado.avisos.append(f"Logo de {clave}: {exc}")
                        continue
                    perfil.save()
                    total += 1
            resultado.aplicado["logo"] = total
        destino = destino_actual()
        if "textos" in elementos and destino is not None:
            reporte = {k: v for k, v in (docs.get("report_texts") or {}).items() if isinstance(v, str)}
            citas = docs.get("quote_texts") or {}
            es = {k: v for k, v in (citas.get("es") or {}).items() if isinstance(v, str)}
            en = {k: v for k, v in (citas.get("en") or {}).items() if isinstance(v, str)}
            reporte, es, en = _solo_conocidos(reporte, es, en, resultado)
            perfiles.validar_textos(reporte, es, en)
            destino.textos_reporte, destino.textos_cotizacion_es, destino.textos_cotizacion_en = reporte, es, en
            destino.actualizado = timezone.now()
            destino.save()
            destino = destino_actual()
            resultado.aplicado["textos"] = len(reporte) + len(es) + len(en)
        if "paleta" in elementos and destino is not None and docs.get("quote_palette"):
            paleta = {k: str(v) for k, v in docs["quote_palette"].items()}
            try:
                resultado.avisos.extend(perfiles.verificar_paleta(paleta))
            except perfiles.PerfilInvalido as exc:
                raise ImportacionInvalida(f"Paleta: {exc}") from exc
            destino.paleta = paleta
            destino.actualizado = timezone.now()
            destino.save()
            resultado.aplicado["paleta"] = len(paleta)
        if "preferencias" in elementos and a:
            nuevas = preferencias.validar({s: a[s] for s in preferencias.SECCIONES if isinstance(a.get(s), dict)}, estricto=False)
            preferencias.guardar(cuenta_id, nuevas, estricto=False)
            resultado.aplicado["preferencias"] = sum(len(v) for v in nuevas.values())
        if "diccionario" in elementos and paquete.diccionario is not None:
            resultado.aplicado["diccionario"] = diccionarios.fusionar_palabras(cuenta_id, paquete.diccionario)
        if "machotes" in elementos:
            nuevos = omitidos = 0
            # Los machotes son de la empresa: un nombre que ya existe (de cualquier cuenta) no se toca.
            existentes = {n.casefold() for n in Machote.objects.values_list("nombre", flat=True)}
            ids = set(Machote.objects.values_list("plantilla_id", flat=True))
            from dataclasses import asdict
            for t in paquete.machotes:
                if t.name.casefold() in existentes:
                    omitidos += 1
                    continue
                try:
                    plantilla_id = uuid.UUID(str(t.template_id))
                except ValueError:
                    plantilla_id = uuid.uuid4()
                if plantilla_id in ids:
                    plantilla_id = uuid.uuid4()
                m = Machote(cuenta_id=cuenta_id, plantilla_id=plantilla_id, nombre=t.name, idioma=t.language, concepto=asdict(t.item))
                m.save()
                Machote.objects.filter(pk=m.pk).update(modificado_por_id=cuenta_id, modificado_por_nombre=m.autor_nombre)
                existentes.add(t.name.casefold())
                ids.add(plantilla_id)
                nuevos += 1
            resultado.aplicado["machotes"] = nuevos
            if omitidos:
                resultado.omitido.append(f"{omitidos} machote(s) ya existían con el mismo nombre y no se tocaron.")
    return resultado


def _solo_conocidos(reporte, es, en, resultado: Resultado):
    """Los rótulos que la web ya no conoce se omiten con aviso (el escritorio pudo agregar claves nuevas)."""
    from ..dominio import document_texts
    from ..dominio.quotation_i18n import LABELS

    def filtrar(valores, permitidos, nombre):
        conocidos = {k: v for k, v in valores.items() if k in permitidos}
        if len(conocidos) != len(valores):
            resultado.avisos.append(f"Se omitieron {len(valores) - len(conocidos)} texto(s) desconocidos de {nombre}.")
        return conocidos
    return (filtrar(reporte, document_texts.REPORT_TEXT_DEFAULTS, "reportes"), filtrar(es, LABELS["es"], "cotizaciones (es)"), filtrar(en, LABELS["en"], "cotizaciones (en)"))
