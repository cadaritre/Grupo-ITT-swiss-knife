"""Perfiles corporativos: de la base de datos al dominio.

El dominio (`itt_herramientas.dominio`) sigue leyendo `SETTINGS.get("branding.…")` como el escritorio. Aquí se
convierte un `Perfil` en esos ajustes (`ajustes_de`) y se arman con él los objetos que antes traían datos
personales de fábrica: `nueva_cotizacion` (datos bancarios y firmante) y `opciones_reporte` (firmante, cédula).
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from django.conf import settings
from tvplataforma import ajustes as ajustes_tv
from tvplataforma import permisos

from ..dominio import branding, document_texts, quote_theme
from ..dominio.almacenamiento import Ajustes, ajustes_activos
from ..dominio.pdf_generator import ReportOptions
from ..dominio.quotation_i18n import LABELS
from ..dominio.quotation_models import QuoteData
from django.utils import timezone

from ..models import CLAVES_PERFIL, Perfil, PerfilActivo

#: campo de `Perfil` -> campo del perfil del dominio (`branding.PROFILE_DEFAULTS`)
CAMPOS = {
    "nombre": "name", "encabezado": "document_heading", "encabezado_en": "document_heading_en",
    "descripcion": "description", "descripcion_en": "description_en", "sitio_web": "website",
    "telefono": "phone", "etiqueta_telefono": "phone_label", "etiqueta_telefono_en": "phone_label_en",
    "direccion": "address", "correo": "email", "firmante": "signer", "pie": "document_footer", "pie_en": "document_footer_en",
}

IMAGENES = {"logo": "logo_ruta", "firma": "firma_ruta", "cedula": "cedula_ruta"}
MAX_IMAGEN_BYTES = 5 * 1024 * 1024
MAX_IMAGEN_PIXELES = 24_000_000
FORMATOS = {"PNG": ".png", "JPEG": ".jpg"}
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class PerfilInvalido(ValueError):
    pass


def valores_de_fabrica(clave: str) -> dict:
    """Campos de marca (sin datos personales) con los que nace un perfil."""
    base = branding.profile_defaults(clave)
    return {campo: base[dominio] for campo, dominio in CAMPOS.items() if base.get(dominio)}


def sembrar_perfiles(**_kwargs) -> None:
    """Crea los dos perfiles si faltan (al migrar). Nunca pisa uno existente."""
    for clave in ("grupo_itt", "tresvizo"):
        Perfil.objects.get_or_create(clave=clave, defaults=valores_de_fabrica(clave))


def raiz_archivos() -> Path:
    return Path(ajustes_tv.valor("TV_HERRAMIENTAS_ARCHIVOS"))


def ruta_imagen(perfil: Perfil, tipo: str) -> Path | None:
    relativa = getattr(perfil, IMAGENES[tipo])
    if not relativa:
        return None
    ruta = (raiz_archivos() / relativa).resolve()
    if not ruta.is_relative_to(raiz_archivos().resolve()):
        return None
    return ruta if ruta.is_file() else None


def guardar_imagen(perfil: Perfil, tipo: str, datos: bytes) -> Path:
    """Valida el contenido real (PNG o JPEG, tamaño y píxeles acotados) y lo guarda con nombre propio."""
    from PIL import Image, UnidentifiedImageError

    if tipo not in IMAGENES:
        raise PerfilInvalido(f"Tipo de imagen desconocido: {tipo}")
    if not datos or len(datos) > MAX_IMAGEN_BYTES:
        raise PerfilInvalido("La imagen está vacía o pesa más de 5 MB.")
    try:
        with Image.open(io.BytesIO(datos)) as imagen:
            formato = imagen.format
            ancho, alto = imagen.size
            imagen.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise PerfilInvalido("El archivo no es una imagen válida.") from exc
    if formato not in FORMATOS:
        raise PerfilInvalido("Usa una imagen PNG o JPEG.")
    if ancho * alto > MAX_IMAGEN_PIXELES:
        raise PerfilInvalido("La imagen es demasiado grande (máximo 24 megapíxeles).")
    relativa = Path("perfiles") / perfil.clave / f"{tipo}{FORMATOS[formato]}"
    destino = raiz_archivos() / relativa
    destino.parent.mkdir(parents=True, exist_ok=True)
    for anterior in destino.parent.glob(f"{tipo}.*"):
        anterior.unlink()
    destino.write_bytes(datos)
    setattr(perfil, IMAGENES[tipo], relativa.as_posix())
    return destino


def quitar_imagen(perfil: Perfil, tipo: str) -> None:
    """Borra la imagen propia del perfil (el logo vuelve al de fábrica; sin firma o cédula, los documentos salen sin ellas)."""
    ruta = ruta_imagen(perfil, tipo)
    if ruta is not None:
        ruta.unlink(missing_ok=True)
    setattr(perfil, IMAGENES[tipo], "")


def ajustes_de(perfil: Perfil) -> dict:
    """Ajustes del dominio que equivalen a este perfil (se activan con `ajustes_activos`)."""
    campos = {dominio: getattr(perfil, campo) or "" for campo, dominio in CAMPOS.items()}
    logo = ruta_imagen(perfil, "logo")
    if logo is not None:
        campos["custom_logo"] = str(logo)
    return {
        "branding": {"profile": perfil.clave, perfil.clave: campos},
        "documents": {
            "report_texts": dict(perfil.textos_reporte or {}),
            "quote_texts": {"es": dict(perfil.textos_cotizacion_es or {}), "en": dict(perfil.textos_cotizacion_en or {})},
            "quote_palette": dict(perfil.paleta or {}),
        },
    }


def activar(perfil: Perfil, preferencias: dict | None = None):
    """Administrador de contexto: el dominio ve los ajustes de este perfil (y las preferencias de la cuenta)."""
    valores = ajustes_de(perfil)
    for seccion, contenido in (preferencias or {}).items():
        valores.setdefault(seccion, contenido)
    return ajustes_activos(valores)


def nueva_cotizacion(perfil: Perfil, **campos) -> QuoteData:
    """Cotización nueva con los datos bancarios y el firmante del perfil (antes eran valores fijos del código)."""
    with activar(perfil):
        firmante = branding.signer_for_new_quote()
    base = {"bank": perfil.banco, "clabe": perfil.clabe, "account": perfil.cuenta, "prepared_by": firmante}
    base.update(campos)
    return QuoteData(**base)


def opciones_reporte(perfil: Perfil, titulo, fecha, fotos, salida: Path, *, incluir_credenciales: bool = False, **campos) -> ReportOptions:
    """Opciones del reporte fotográfico con la identidad, la firma y la cédula del perfil."""
    with activar(perfil):
        empresa = branding.active_profile()
    base = dict(
        title=titulo, report_date=fecha, photos=list(fotos), output=Path(salida), logo=empresa.logo_path,
        company=empresa.document_heading, website=empresa.website_label, footer=empresa.document_footer,
        include_credentials=incluir_credenciales, signature=ruta_imagen(perfil, "firma"), license_card=ruta_imagen(perfil, "cedula"),
        signer=perfil.firmante_reporte, professional_license=perfil.cedula_profesional,
    )
    base.update(campos)
    return ReportOptions(**base)


# -- Validación de textos y paleta -----------------------------------------------------------------------------

def validar_textos(reporte: dict, es: dict, en: dict) -> None:
    for nombre, valores, permitidos in (
        ("reporte", reporte, document_texts.REPORT_TEXT_DEFAULTS), ("cotización (es)", es, LABELS["es"]), ("cotización (en)", en, LABELS["en"]),
    ):
        for clave, valor in valores.items():
            if clave not in permitidos:
                raise PerfilInvalido(f"Texto desconocido en {nombre}: {clave}")
            if not isinstance(valor, str) or len(valor) > 600:
                raise PerfilInvalido(f"El texto «{clave}» de {nombre} debe ser texto de hasta 600 caracteres.")
    meses = reporte.get("months")
    if meses is not None and len([m for m in meses.split("|") if m.strip()]) != 12:
        raise PerfilInvalido("Los meses deben ser doce nombres separados por «|».")


def _luminancia(color: str) -> float:
    canales = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lineal = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
    return 0.2126 * lineal[0] + 0.7152 * lineal[1] + 0.0722 * lineal[2]


def contraste(a: str, b: str) -> float:
    """Relación de contraste WCAG entre dos colores `#RRGGBB`."""
    la, lb = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


#: (color del texto, color de fondo, descripción, mínimo WCAG): 4.5 para texto normal; 3 para rótulos en negrita del acento.
UMBRALES = (("bar_text", "primary", "texto sobre las barras", 4.5), ("ink", "pale", "texto sobre el fondo claro", 4.5),
            ("accent", "pale", "acento sobre el fondo claro", 3.0))


def verificar_paleta(paleta: dict) -> list[str]:
    """Valida los colores y devuelve avisos de contraste (no bloquean: la paleta es decisión del propietario)."""
    claves = ("primary", "accent", "pale", "bar_text", "ink")
    for clave in paleta:
        if clave not in claves:
            raise PerfilInvalido(f"Color desconocido: {clave}")
        if not _HEX.match(str(paleta[clave])):
            raise PerfilInvalido(f"El color «{clave}» debe tener la forma #RRGGBB.")
    completa = {**quote_theme.QUOTE_PALETTE_PRESETS["Azul corporativo"], **paleta}
    avisos = []
    for texto, fondo, nombre, minimo in UMBRALES:
        ratio = contraste(completa[texto], completa[fondo])
        if ratio < minimo:
            avisos.append(f"Contraste bajo en {nombre}: {ratio}:1 (se recomienda al menos {minimo}:1).")
    return avisos


# -- Perfil activo de cada cuenta ---------------------------------------------------------------------------------

PERFIL_POR_OMISION = "grupo_itt"


class SinPermiso(PermissionError):
    pass


def es_admin(usuario) -> bool:
    return permisos.tiene_rol(usuario, "herramientas", "admin")


def puede_cambiar(usuario) -> bool:
    """¿Tiene el permiso «Cambiar perfil corporativo»? Solo el propietario lo concede (consola del portal)."""
    return permisos.tiene_rol(usuario, "herramientas", "cambiar_perfil")


def perfil_activo(usuario) -> Perfil:
    """Perfil con el que trabaja la cuenta. Sin el permiso, siempre Grupo ITT (aunque haya un renglón viejo)."""
    if puede_cambiar(usuario):
        fila = PerfilActivo.objects.select_related("perfil").filter(cuenta_id=usuario.pk).first()
        if fila is not None:
            return fila.perfil
    return Perfil.objects.get(clave=PERFIL_POR_OMISION)


def clave_activa(usuario) -> str:
    """Clave del perfil activo: el espacio de documentos en que trabaja la cuenta (listas, búsqueda, folios, sincronización)."""
    if puede_cambiar(usuario):
        clave = PerfilActivo.objects.filter(cuenta_id=usuario.pk).values_list("perfil__clave", flat=True).first()
        if clave:
            return clave
    return PERFIL_POR_OMISION


def claves_de(usuario) -> tuple[str, ...]:
    """Perfiles cuyos documentos puede abrir la cuenta: con el permiso «Cambiar perfil corporativo», todos; sin él, solo Grupo ITT."""
    return tuple(c for c, _ in CLAVES_PERFIL) if puede_cambiar(usuario) else (PERFIL_POR_OMISION,)


def puede_usar(usuario, clave: str) -> bool:
    return clave in claves_de(usuario)


def nombre_de(clave: str) -> str:
    """Nombre con que se muestra un perfil («Grupo ITT», «TresVizo»): el que le puso quien lo administra, o el de fábrica."""
    nombre = Perfil.objects.filter(clave=clave).values_list("nombre", flat=True).first()
    return nombre or dict(CLAVES_PERFIL).get(clave, clave)


def opciones(usuario) -> list[dict]:
    """Los perfiles que la cuenta puede usar, con cuál está activo (el selector de las listas)."""
    activa = clave_activa(usuario)
    nombres = dict(Perfil.objects.values_list("clave", "nombre"))
    return [{"clave": c, "nombre": nombres.get(c) or dict(CLAVES_PERFIL)[c], "activo": c == activa} for c in claves_de(usuario)]


def cambiar_perfil(usuario, clave: str) -> Perfil:
    """Deja `clave` como perfil activo de ESTA cuenta. `SinPermiso` si no tiene el permiso; `PerfilInvalido` si no existe."""
    if not puede_cambiar(usuario):
        raise SinPermiso("No tienes permiso para cambiar el perfil corporativo.")
    perfil = Perfil.objects.filter(clave=clave).first()
    if perfil is None:
        raise PerfilInvalido("Perfil desconocido.")
    PerfilActivo.objects.update_or_create(cuenta_id=usuario.pk, defaults={"perfil": perfil, "actualizado": timezone.now()})
    return perfil
