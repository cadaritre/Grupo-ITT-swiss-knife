"""Comparadores de salidas: PDF, DXF, KML/KMZ, JSON e imágenes.

Cada comparador devuelve un `Resultado`: `nivel` es "exacto" (mismos bytes tras normalizar lo que depende de la
ejecución), "tolerante" (mismo contenido; difiere la tipografía o el empaquetado) o "distinto" (con el motivo).
"""
from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

EXACTO, TOLERANTE, DISTINTO = "exacto", "tolerante", "distinto"


@dataclass
class Resultado:
    nivel: str
    motivo: str = ""

    @property
    def ok(self) -> bool:
        return self.nivel != DISTINTO


# -- JSON y texto ----------------------------------------------------------------------------------------------

_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_FECHA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?")


def _normalizar_json_texto(texto: str) -> str:
    """Quita lo que cambia en cada ejecución: UUID de machotes y sellos de tiempo, en orden de aparición."""
    ids: dict[str, str] = {}
    texto = _ID.sub(lambda m: ids.setdefault(m.group(0), f"<id{len(ids) + 1}>"), texto)
    return _FECHA_ISO.sub("<fecha>", texto)


def comparar_json(a: Path, b: Path) -> Resultado:
    ja = json.loads(_normalizar_json_texto(a.read_text(encoding="utf-8")))
    jb = json.loads(_normalizar_json_texto(b.read_text(encoding="utf-8")))
    if ja == jb:
        return Resultado(EXACTO)
    return Resultado(DISTINTO, _primera_diferencia_json(ja, jb))


def _primera_diferencia_json(a, b, ruta="$") -> str:
    if type(a) is not type(b):
        return f"{ruta}: tipo {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{ruta}.{k}: solo en un lado"
            d = _primera_diferencia_json(a[k], b[k], f"{ruta}.{k}")
            if d:
                return d
        return ""
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{ruta}: {len(a)} vs {len(b)} elementos"
        for i, (x, y) in enumerate(zip(a, b)):
            d = _primera_diferencia_json(x, y, f"{ruta}[{i}]")
            if d:
                return d
        return ""
    return "" if a == b else f"{ruta}: {a!r} vs {b!r}"


def comparar_texto(a: Path, b: Path) -> Resultado:
    return Resultado(EXACTO) if a.read_bytes() == b.read_bytes() else Resultado(DISTINTO, "texto distinto")


# -- PDF -------------------------------------------------------------------------------------------------------

_XOBJ = re.compile(rb"FormXob\.[0-9a-f]{32}")
_ID_PDF = re.compile(rb"/ID \[<[0-9A-Fa-f]+><[0-9A-Fa-f]+>\]")


def normalizar_pdf(datos: bytes) -> bytes:
    """ReportLab nombra los XObject de imagen con un resumen de la RUTA del archivo; el resto, en modo
    invariante, es idéntico. Se unifican esos nombres (misma longitud, así que las posiciones no cambian)."""
    return _XOBJ.sub(b"FormXob." + b"0" * 32, datos)


def comparar_pdf(a: Path, b: Path, tolerante: bool = False, umbral_pixeles: float = 0.004) -> Resultado:
    da, db = a.read_bytes(), b.read_bytes()
    if normalizar_pdf(da) == normalizar_pdf(db):
        return Resultado(EXACTO)
    if not tolerante:
        i = next((k for k in range(min(len(da), len(db))) if da[k] != db[k]), min(len(da), len(db)))
        return Resultado(DISTINTO, f"bytes distintos (primera diferencia en {i}; {len(da)} vs {len(db)} bytes)")
    return _pdf_tolerante(a, b, umbral_pixeles)


def _pdf_tolerante(a: Path, b: Path, umbral: float) -> Resultado:
    """Mismo número de páginas, mismo texto por página y diferencia de píxeles pequeña (tipografía, no contenido)."""
    try:
        import pymupdf as fitz
        import numpy as np
    except ImportError:
        return Resultado(DISTINTO, "bytes distintos y falta PyMuPDF para la comparación tolerante (pip install .[paridad])")
    with fitz.open(a) as da, fitz.open(b) as db:
        if len(da) != len(db):
            return Resultado(DISTINTO, f"{len(da)} vs {len(db)} páginas")
        for n, (pa, pb) in enumerate(zip(da, db), 1):
            ta = " ".join(pa.get_text().split())
            tb = " ".join(pb.get_text().split())
            if ta != tb:
                return Resultado(DISTINTO, f"texto distinto en la página {n}")
            if pa.rect != pb.rect:
                return Resultado(DISTINTO, f"tamaño de página distinto en {n}")
            ia = np.frombuffer(pa.get_pixmap(dpi=72).samples, dtype=np.uint8).astype(np.int16)
            ib = np.frombuffer(pb.get_pixmap(dpi=72).samples, dtype=np.uint8).astype(np.int16)
            if ia.shape != ib.shape:
                return Resultado(DISTINTO, f"imagen de página distinta en {n}")
            fraccion = float((np.abs(ia - ib) > 48).mean())
            if fraccion > umbral:
                return Resultado(DISTINTO, f"página {n}: {fraccion:.2%} de los píxeles difieren (umbral {umbral:.2%})")
    return Resultado(TOLERANTE, "mismo texto y apariencia; los bytes difieren (fuentes o versión de ReportLab)")


# -- DXF -------------------------------------------------------------------------------------------------------

_VARIABLES_DE_EJECUCION = {"$TDCREATE", "$TDUCREATE", "$TDUPDATE", "$TDUUPDATE", "$TDINDWG", "$TDUSRTIMER",
                           "$FINGERPRINTGUID", "$VERSIONGUID", "$HANDSEED"}


_META_EZDXF = re.compile(r"^(\d+(?:\.\d+)* @ )\d{4}-\d{2}-\d{2}T[\d:.]+(?:[+-]\d{2}:\d{2}|Z)?$")


def normalizar_dxf(texto: str) -> list[tuple[str, str]]:
    """Pares (código, valor) del DXF sin las variables de cabecera que cambian en cada escritura."""
    lineas = texto.replace("\r\n", "\n").split("\n")
    pares = [(lineas[i].strip(), lineas[i + 1]) for i in range(0, len(lineas) - 1, 2)]
    salida = []
    i = 0
    while i < len(pares):
        codigo, valor = pares[i]
        if codigo == "1":                      # EZDXF_META: «versión @ fecha de escritura»
            valor = _META_EZDXF.sub(r"\1<fecha>", valor)
        if codigo == "9" and valor.strip() in _VARIABLES_DE_EJECUCION:
            salida.append((codigo, valor))
            i += 1
            while i < len(pares) and pares[i][0] != "9" and pares[i][0] != "0":
                i += 1
            continue
        salida.append((codigo, valor))
        i += 1
    return salida


def comparar_dxf(a: Path, b: Path) -> Resultado:
    ta = a.read_text(encoding="utf-8", errors="replace")
    tb = b.read_text(encoding="utf-8", errors="replace")
    if ta == tb:
        return Resultado(EXACTO)
    na, nb = normalizar_dxf(ta), normalizar_dxf(tb)
    if na == nb:
        return Resultado(EXACTO, "iguales salvo sellos de tiempo y GUID de la cabecera")
    if len(na) != len(nb):
        return Resultado(DISTINTO, f"{len(na)} vs {len(nb)} pares de códigos")
    for i, (x, y) in enumerate(zip(na, nb)):
        if x != y:
            return Resultado(DISTINTO, f"par {i}: {x!r} vs {y!r}")
    return Resultado(EXACTO)


# -- KML y KMZ --------------------------------------------------------------------------------------------------

def _kml_canonico(datos: bytes) -> str:
    return ET.canonicalize(xml_data=datos.decode("utf-8"), strip_text=True)


def comparar_kml(a: Path, b: Path) -> Resultado:
    return Resultado(EXACTO) if _kml_canonico(a.read_bytes()) == _kml_canonico(b.read_bytes()) else Resultado(DISTINTO, "XML canónico distinto")


def comparar_kmz(a: Path, b: Path) -> Resultado:
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
        if sorted(za.namelist()) != sorted(zb.namelist()):
            return Resultado(DISTINTO, f"contenido distinto: {sorted(za.namelist())} vs {sorted(zb.namelist())}")
        for nombre in za.namelist():
            da, db = za.read(nombre), zb.read(nombre)
            if nombre.lower().endswith(".kml"):
                if _kml_canonico(da) != _kml_canonico(db):
                    return Resultado(DISTINTO, f"{nombre}: XML canónico distinto")
            elif da != db:
                return Resultado(DISTINTO, f"{nombre}: bytes distintos")
    return Resultado(EXACTO)


# -- Imágenes ---------------------------------------------------------------------------------------------------

def comparar_png(a: Path, b: Path, tolerante: bool = False) -> Resultado:
    from PIL import Image, ImageChops

    with Image.open(a) as ia, Image.open(b) as ib:
        if ia.size != ib.size or ia.mode != ib.mode:
            return Resultado(DISTINTO, f"{ia.size}/{ia.mode} vs {ib.size}/{ib.mode}")
        if ia.tobytes() == ib.tobytes():
            return Resultado(EXACTO)
        if not tolerante:
            return Resultado(DISTINTO, "píxeles distintos")
        diferencia = ImageChops.difference(ia.convert("RGB"), ib.convert("RGB")).convert("L").point(lambda v: 255 if v > 48 else 0)
        fraccion = sum(1 for v in diferencia.getdata() if v) / (ia.size[0] * ia.size[1])
        if fraccion > 0.01:
            return Resultado(DISTINTO, f"{fraccion:.2%} de los píxeles difieren")
        return Resultado(TOLERANTE, f"{fraccion:.3%} de los píxeles difieren (tipografía)")


def comparar(a: Path, b: Path, tolerante: bool = False) -> Resultado:
    """Elige el comparador por extensión."""
    ext = a.suffix.lower()
    if ext == ".pdf":
        return comparar_pdf(a, b, tolerante)
    if ext == ".dxf":
        return comparar_dxf(a, b)
    if ext == ".kml":
        return comparar_kml(a, b)
    if ext == ".kmz":
        return comparar_kmz(a, b)
    if ext == ".png":
        return comparar_png(a, b, tolerante)
    if ext == ".json":
        return comparar_json(a, b)
    return comparar_texto(a, b)
