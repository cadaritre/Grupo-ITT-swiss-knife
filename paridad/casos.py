"""Casos de paridad: las mismas entradas pasan por el escritorio y por la web.

Este archivo corre en **los dos lados** (en procesos aparte, ver `trabajador.py`) y solo usa la biblioteca estándar
más las dependencias que ya tiene el escritorio. Cada caso recibe un `Contexto` y devuelve sus artefactos:

- un `Path` a un archivo que el caso escribió (PDF, DXF, KML, KMZ, PNG, JSON...), o
- cualquier objeto serializable a JSON (se guarda como `<nombre>.json` canónico).

No hay fechas ni aleatoriedad sin semilla, y las consultas de red están sustituidas por datos grabados.
"""
from __future__ import annotations

import io
import json
import math
import random
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from unittest import mock

CASOS: dict[str, Callable[["Contexto"], dict[str, Any]]] = {}

SEMILLA = 20261113
PERFILES = ("grupo_itt", "tresvizo")      # cada caso corre con cada identidad corporativa activa, en el escritorio y en la web


def caso(nombre: str):
    def registrar(funcion):
        CASOS[nombre] = funcion
        return funcion
    return registrar


@dataclass
class Contexto:
    """Lo que cada lado le da a un caso."""
    lado: str                                   # "escritorio" o "web"
    mod: Callable[[str], Any]                   # importa un módulo de la lógica por su nombre (p. ej. "quotation_pdf")
    entradas: dict[str, Path]                   # archivos de entrada comunes (incluye `logo_grupo_itt.png` y `logo_tresvizo.png`)
    salida: Path                                # carpeta del caso para escribir archivos
    ajustes: Callable[[dict], Any]              # administrador de contexto: activa ajustes (perfil, textos, paleta)
    logo: Callable[[], Path | None]             # logo del perfil activo
    fotos: list[Path] = field(default_factory=list)
    perfil: str = "grupo_itt"                         # identidad corporativa activa en esta corrida
    personales: dict = field(default_factory=dict)   # datos personales/bancarios del escritorio (no viven en el repositorio)


def a_json(valor: Any) -> Any:
    """Convierte dataclasses, rutas, fechas y conjuntos a algo serializable y estable."""
    if is_dataclass(valor) and not isinstance(valor, type):
        return a_json(asdict(valor))
    if isinstance(valor, dict):
        return {str(k): a_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [a_json(v) for v in valor]
    if isinstance(valor, (set, frozenset)):
        return sorted(a_json(v) for v in valor)
    if isinstance(valor, Path):
        return valor.name
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, float):
        return round(valor, 9)
    return valor


# ---------------------------------------------------------------------------------------------------------------
# Entradas comunes (las genera el ejecutor una vez y las leen los dos lados)
# ---------------------------------------------------------------------------------------------------------------

def _superficie(x: float, y: float) -> float:
    return 1420.0 + 6.0 * math.sin(x / 38.0) + 4.0 * math.cos(y / 27.0) + 0.012 * (x - 370000.0) - 0.008 * (y - 3170000.0)


def _dms(valor: float) -> tuple[float, float, float]:
    grados = int(valor)
    minutos = int((valor - grados) * 60)
    return float(grados), float(minutos), round(((valor - grados) * 60 - minutos) * 60, 4)


def generar_entradas(destino: Path) -> dict[str, Path]:
    """Escribe las entradas sintéticas y devuelve {nombre: ruta}. Determinista: mismas bytes en cada corrida."""
    import ezdxf
    from PIL import Image, ImageDraw, ExifTags

    destino.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEMILLA)
    entradas: dict[str, Path] = {}

    # Puntos topográficos para triangulación (POINT e INSERT) en UTM 13N, zona de Chihuahua.
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.blocks.new("PUNTO")
    for i in range(420):
        x = 370000.0 + rng.uniform(0, 260)
        y = 3170000.0 + rng.uniform(0, 210)
        punto = (round(x, 3), round(y, 3), round(_superficie(x, y), 3))
        if i % 7 == 0:
            msp.add_blockref("PUNTO", punto, dxfattribs={"layer": "PUNTOS_INSERT"})
        else:
            msp.add_point(punto, dxfattribs={"layer": "PUNTOS"})
    msp.add_point((370000.0, 3170000.0, 1420.0), dxfattribs={"layer": "PUNTOS"})
    msp.add_point((370000.0, 3170000.0, 1421.0), dxfattribs={"layer": "PUNTOS"})   # duplicado XY
    ruta = destino / "puntos.dxf"
    doc.saveas(ruta)
    entradas["puntos.dxf"] = ruta

    # Dibujo variado para el convertidor DXF -> KML/KMZ.
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    base_x, base_y = 370100.0, 3170100.0
    msp.add_lwpolyline([(base_x, base_y), (base_x + 80, base_y), (base_x + 80, base_y + 60), (base_x, base_y + 60)], close=True, dxfattribs={"layer": "PREDIO"})
    msp.add_line((base_x, base_y), (base_x + 80, base_y + 60), dxfattribs={"layer": "EJES"})
    msp.add_circle((base_x + 40, base_y + 30), 12.5, dxfattribs={"layer": "TANQUES"})
    msp.add_arc((base_x + 20, base_y + 10), 9, 15, 200, dxfattribs={"layer": "CURVAS"})
    msp.add_point((base_x + 5, base_y + 5, 1421.5), dxfattribs={"layer": "VERTICES"})
    msp.add_text("PUNTO A", dxfattribs={"layer": "VERTICES", "insert": (base_x + 6, base_y + 6), "height": 2})
    poli3d = msp.add_polyline3d([(base_x, base_y, 1420.0), (base_x + 30, base_y + 20, 1423.0), (base_x + 60, base_y + 35, 1424.5)], dxfattribs={"layer": "CURVAS"})
    poli3d.dxf.layer = "CURVAS"
    msp.add_lwpolyline([(base_x + 10, base_y + 40), (base_x + 25, base_y + 52), (base_x + 40, base_y + 40)], dxfattribs={"layer": "BORDE ABIERTO"})
    ruta = destino / "convertidor.dxf"
    doc.saveas(ruta)
    entradas["convertidor.dxf"] = ruta

    kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Folder><name>Predios</name>
<Placemark><name>Predio norte</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
-106.0700,28.6300,0 -106.0690,28.6300,0 -106.0690,28.6310,0 -106.0700,28.6310,0 -106.0700,28.6300,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>Predio sur</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
-106.0710,28.6280,0 -106.0695,28.6280,0 -106.0695,28.6295,0 -106.0710,28.6295,0 -106.0710,28.6280,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Folder>
<Folder><name>Ejes</name>
<Placemark><name>Eje 1</name><LineString><coordinates>-106.0705,28.6285,1420 -106.0698,28.6296,1424 -106.0692,28.6308,1428</coordinates></LineString></Placemark>
<Placemark><name>Punto base</name><Point><coordinates>-106.0699,28.6299,1421</coordinates></Point></Placemark>
</Folder>
</Document></kml>"""
    ruta = destino / "capas.kml"
    ruta.write_text(kml, encoding="utf-8")
    entradas["capas.kml"] = ruta
    ruta = destino / "capas.kmz"
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("doc.kml", (2026, 11, 13, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, kml)
    entradas["capas.kmz"] = ruta

    # Fotografías sintéticas (la 1, 2, 4 y 5 con GPS en Chihuahua; la 3 y 6 sin GPS; una vertical).
    fotos = []
    colores = ["#2F6F9F", "#9F6F2F", "#4F9F2F", "#9F2F6F", "#6F2F9F", "#2F9F8F"]
    for i in range(6):
        ancho, alto = (1600, 1200) if i != 3 else (1200, 1600)
        imagen = Image.new("RGB", (ancho, alto), colores[i])
        dib = ImageDraw.Draw(imagen)
        for k in range(0, ancho, 80):
            dib.line((k, 0, ancho - k, alto), fill="#FFFFFF", width=3)
        dib.rectangle((40, 40, 360, 160), fill="#FFFFFF")
        exif = Image.Exif()
        if i not in (2, 5):
            exif[ExifTags.Base.DateTimeOriginal] = f"2026:11:0{i + 1} 09:{10 + i}:00"
            exif[ExifTags.Base.DateTime] = f"2026:11:0{i + 1} 09:{10 + i}:00"
            lat = 28.6330 + i * 0.0006
            lon = 106.0691 - i * 0.0004          # oeste (referencia «W»)
            exif[ExifTags.IFD.GPSInfo] = {1: "N", 2: _dms(lat), 3: "W", 4: _dms(lon)}
        ruta = destino / f"foto_{i + 1}.jpg"
        imagen.save(ruta, "JPEG", quality=88, exif=exif.tobytes() if len(exif) else b"", optimize=False)
        fotos.append(ruta)
        entradas[f"foto_{i + 1}.jpg"] = ruta

    firma = Image.new("RGBA", (600, 220), (255, 255, 255, 0))
    d = ImageDraw.Draw(firma)
    d.line([(20, 150), (120, 40), (200, 170), (330, 60), (420, 150), (580, 90)], fill=(10, 20, 90, 255), width=6)
    ruta = destino / "firma.png"
    firma.save(ruta)
    entradas["firma.png"] = ruta
    cedula = Image.new("RGB", (900, 560), "#F2EFE6")
    d = ImageDraw.Draw(cedula)
    d.rectangle((30, 30, 870, 530), outline="#555555", width=5)
    d.rectangle((60, 70, 300, 330), fill="#B0B8C0")
    ruta = destino / "cedula.jpg"
    cedula.save(ruta, "JPEG", quality=90)
    entradas["cedula.jpg"] = ruta
    referencia = Image.new("RGB", (1000, 700), "#C9D6DF")
    d = ImageDraw.Draw(referencia)
    d.ellipse((200, 150, 800, 550), fill="#7FA2B8", outline="#173B5F", width=8)
    ruta = destino / "referencia.jpg"
    referencia.save(ruta, "JPEG", quality=90)
    entradas["referencia.jpg"] = ruta
    return entradas


# ---------------------------------------------------------------------------------------------------------------
# Datos de los casos
# ---------------------------------------------------------------------------------------------------------------

PALETAS = {
    "azul": {"primary": "#07356F", "accent": "#0B7FAB", "pale": "#EAF3F8", "bar_text": "#FFFFFF", "ink": "#263746"},
    "naranja": {"primary": "#D85C0D", "accent": "#B84600", "pale": "#FFF0E3", "bar_text": "#FFFFFF", "ink": "#242424"},
    "amarillo": {"primary": "#F2C230", "accent": "#8A6500", "pale": "#FFF8D6", "bar_text": "#111111", "ink": "#202020"},
    "verde": {"primary": "#176B4D", "accent": "#207A58", "pale": "#E5F5EE", "bar_text": "#FFFFFF", "ink": "#21352D"},
    "personalizada": {"primary": "#5B2C83", "accent": "#8E44AD", "pale": "#F3E9F9", "bar_text": "#FFFFFF", "ink": "#2B2B2B"},
}


def _cotizacion(c: Contexto, idioma: str, *, iva: bool = True, con_imagen: bool = False, vacia: bool = False):
    modelos = c.mod("quotation_models")
    q = modelos.QuoteData(
        quote_number="COT-2026-0142", quote_date="13/11/2026", client_name="Constructora del Norte, S.A. de C.V.",
        contact="Arq. María Fernanda López", email="mflopez@example.com", phone="614 555 0123",
        location="Av. Universidad 1500, Chihuahua, Chih.", project_title="Levantamiento y modelado de nave industrial",
        currency="MXN", include_vat=iva, vat_rate=16.0, advance_percent=50.0, validity_days=15,
        language=idioma, spelling_checked=True, **c.personales["cotizacion"],
    )
    if not vacia:
        q.items = [
            modelos.clone_template("Levantamiento topográfico", idioma),
            modelos.clone_template("Escaneo láser 3D", idioma),
            modelos.clone_template("Modelado BIM LOD 300", idioma),
            modelos.QuoteItem(title="Concepto especial de prueba", description="Texto <b>propio</b> con & símbolos, acentos áéíóú ñ y un renglón largo "
                              "que debe ajustarse dentro de la celda sin desbordar la tabla del documento.",
                              subitems=["Primer inciso del alcance.", "Segundo inciso con más detalle técnico."], unit="Día" if idioma == "es" else "Day",
                              quantity=2.5, unit_price=3333.33),
        ]
        q.items[0].quantity, q.items[0].unit_price = 1, 18500.0
        q.items[1].quantity, q.items[1].unit_price = 2, 9750.5
        q.items[2].quantity, q.items[2].unit_price = 1, 42000.0
    if con_imagen:
        q.images = [modelos.QuoteImage(path=str(c.entradas["referencia.jpg"]), caption="Vista general del predio"),
                    modelos.QuoteImage(path=str(c.entradas["referencia.jpg"]), caption="")]
    return q


def _pdf(c: Contexto, nombre: str, funcion, *args, **kwargs) -> Path:
    ruta = c.salida / nombre
    funcion(*args, ruta, **kwargs)
    return ruta


@caso("cotizacion_totales_y_json_editable")
def _(c: Contexto):
    modelos = c.mod("quotation_models")
    resultados = {}
    for clave, (iva, tasa, anticipo) in {"con_iva": (True, 16.0, 50.0), "sin_iva": (False, 16.0, 30.0), "iva_8": (True, 8.0, 0.0)}.items():
        q = _cotizacion(c, "es", iva=iva)
        q.vat_rate, q.advance_percent = tasa, anticipo
        resultados[clave] = {"subtotal": q.subtotal, "vat": q.vat, "total": q.total, "advance": q.advance,
                             "importes": [i.amount for i in q.items]}
    q = _cotizacion(c, "en", con_imagen=True)
    archivo = q.save(c.salida / "EDITABLE_COTIZACION_prueba.json")
    recargada = modelos.QuoteData.load(archivo)
    assert recargada.to_dict() == q.to_dict()
    return {"totales": resultados, "EDITABLE_COTIZACION_prueba.json": archivo}


@caso("cotizacion_pdf_es_azul")
def _(c: Contexto):
    with c.ajustes({"documents": {"quote_palette": PALETAS["azul"]}}):
        pdf = c.mod("quotation_pdf")
        return {"cotizacion.pdf": _pdf(c, "cotizacion.pdf", pdf.generate_quote_pdf, _cotizacion(c, "es", con_imagen=True), logo=c.logo())}


@caso("cotizacion_pdf_en_naranja")
def _(c: Contexto):
    with c.ajustes({"documents": {"quote_palette": PALETAS["naranja"]}}):
        pdf = c.mod("quotation_pdf")
        return {"cotizacion.pdf": _pdf(c, "cotizacion.pdf", pdf.generate_quote_pdf, _cotizacion(c, "en"), logo=c.logo())}


@caso("cotizacion_pdf_tresvizo_verde_sin_iva")
def _(c: Contexto):
    with c.ajustes({"branding": {"profile": "tresvizo"}, "documents": {"quote_palette": PALETAS["verde"]}}):
        pdf = c.mod("quotation_pdf")
        q = _cotizacion(c, "es", iva=False)
        q.prepared_by = c.personales["firmante_antiguo"]            # el perfil TresVizo lo sustituye por su firmante
        return {"cotizacion.pdf": _pdf(c, "cotizacion.pdf", pdf.generate_quote_pdf, q, logo=c.logo())}


@caso("cotizacion_pdf_amarillo_y_personalizada")
def _(c: Contexto):
    pdf = c.mod("quotation_pdf")
    salida = {}
    for clave in ("amarillo", "personalizada"):
        with c.ajustes({"documents": {"quote_palette": PALETAS[clave]}}):
            salida[f"cotizacion_{clave}.pdf"] = _pdf(c, f"cotizacion_{clave}.pdf", pdf.generate_quote_pdf, _cotizacion(c, "es"), logo=c.logo())
    return salida


@caso("cotizacion_pdf_sin_conceptos_y_textos_propios")
def _(c: Contexto):
    textos = {"es": {"quoted_services": "Servicios cotizados (texto propio)", "no_items": "Sin conceptos por ahora"},
              "en": {}}
    with c.ajustes({"documents": {"quote_texts": textos}}):
        pdf = c.mod("quotation_pdf")
        return {"cotizacion.pdf": _pdf(c, "cotizacion.pdf", pdf.generate_quote_pdf, _cotizacion(c, "es", vacia=True), logo=c.logo())}


@caso("cotizacion_machotes_integrados_y_traduccion")
def _(c: Contexto):
    modelos, i18n = c.mod("quotation_models"), c.mod("quotation_i18n")
    es = {k: asdict(v) for k, v in modelos.CONCEPT_TEMPLATES.items()}
    en = {k: asdict(v) for k, v in modelos.CONCEPT_TEMPLATES_EN.items()}
    q = _cotizacion(c, "es")
    q.notes, q.delivery_time = "Se requiere anticipo para programar los trabajos.", "Por confirmar de acuerdo con el alcance."
    i18n.translate_standard_quote_values(q, "en")
    a_en = q.to_dict()
    i18n.translate_standard_quote_values(q, "es")
    return {"machotes_es": es, "machotes_en": en, "traducida_en": a_en, "vuelta_es": q.to_dict(),
            "etiquetas": {"es": i18n.labels("es"), "en": i18n.labels("en")}}


@caso("cotizacion_machotes_personalizados")
def _(c: Contexto):
    plantillas, modelos = c.mod("quotation_templates"), c.mod("quotation_models")
    tienda = plantillas.CustomConceptTemplateStore(c.salida / "uno" / "machotes.json")
    item = modelos.QuoteItem(title="Inspección", description="Inspección en campo", subitems=["a", "b"], unit="Día", quantity=3, unit_price=1200)
    t1 = tienda.save_item("Inspección de obra", item, "es")
    t2 = tienda.save_item("Site inspection", item, "en")
    tienda.save_item("Zeta", modelos.QuoteItem(title="Zeta"), "es")
    tienda.rename(t2.template_id, "Site visit")
    tienda.delete(tienda.find_name("Zeta").template_id)
    exportado = tienda.export_file(c.salida / "exportado.json")
    otra = plantillas.CustomConceptTemplateStore(c.salida / "dos" / "machotes.json")
    resultado = otra.import_file(exportado)
    errores = []
    try:
        tienda.save_item("Site visit", item, "en")
    except ValueError as exc:
        errores.append(str(exc))

    def resumen(t):
        return [{"nombre": x.name, "idioma": x.language, "item": asdict(x.item)} for x in t.templates]
    return {"coleccion": resumen(tienda), "importada": resumen(otra), "importacion": list(resultado), "errores": errores,
            "esquema": {k: v for k, v in json.loads(exportado.read_text(encoding="utf-8")).items() if k in ("tool", "schema_version")}}


@caso("textos_etiquetas_y_perfiles")
def _(c: Contexto):
    textos, i18n, marca, tema = c.mod("document_texts"), c.mod("quotation_i18n"), c.mod("branding"), c.mod("quote_theme")
    salida = {}
    personalizados = {"documents": {"report_texts": {"header_title": "Informe fotográfico", "months": "e|f|m|a|my|j|jl|ag|s|o|n|d"},
                                    "quote_texts": {"es": {"sincerely": "Cordialmente"}, "en": {"sincerely": "Kind regards"}}}}
    for nombre, ajustes in {"fabrica": {}, "tresvizo": {"branding": {"profile": "tresvizo"}}, "personalizados": personalizados,
                            "web_propia": {"branding": {"grupo_itt": {"website": "https://itt.example.org/", "phone": "614 000 0000"}}}}.items():
        with c.ajustes(ajustes):
            perfil = a_json(asdict(marca.active_profile()))
            perfil["logo_path"] = Path(perfil["logo_path"]).name
            salida[nombre] = {
                "perfil": perfil,
                "firmante_nueva": marca.signer_for_new_quote(),
                "firmante_normalizado": [marca.normalize_quote_signer(v) for v in ("", c.personales["firmante_antiguo"], "Otra persona")],
                "textos_reporte": textos.report_texts(),
                "meses": list(textos.report_months()),
                "etiquetas_es": i18n.labels("es"), "etiquetas_en": i18n.labels("en"),
                "paleta": asdict(tema.quote_palette()),
            }
    salida["plantilla"] = textos.fill_template("{day} de {month} de {year} {otro}", day="03", month="enero", year=2026)
    salida["presets"] = {"nombres": list(tema.QUOTE_PALETTE_PRESETS), "coincidencias": [tema.matching_preset(v) for v in PALETAS.values()]}
    return salida


@caso("metadata_de_fotos")
def _(c: Contexto):
    meta = c.mod("metadata")
    return {"fotos": [a_json(asdict(meta.read_photo(c.entradas[f"foto_{i}.jpg"]))) | {"orientacion": meta.read_photo(c.entradas[f"foto_{i}.jpg"]).orientation,
                                                                                   "gps": meta.read_photo(c.entradas[f"foto_{i}.jpg"]).has_gps}
                      for i in range(1, 7)],
            "extensiones": sorted(meta.SUPPORTED_EXTENSIONS)}


def _fotos(c: Contexto, con_gps_solo: bool = False):
    meta = c.mod("metadata")
    fotos = []
    for i in range(1, 7):
        info = meta.read_photo(c.entradas[f"foto_{i}.jpg"])
        info.description = f"Vista {i}: descripción de la fotografía número {i} con acentos áéíóú y texto suficientemente largo para ajustarse." if i % 2 else ""
        if con_gps_solo and not info.has_gps:
            continue
        fotos.append(info)
    return fotos


def _opciones(c: Contexto, nombre: str, **extra):
    gen = c.mod("pdf_generator")
    empresa = c.mod("branding").active_profile()           # como el escritorio: encabezado, sitio y pie del perfil activo
    base = dict(title="Reporte fotográfico de avance de obra", report_date=date(2026, 11, 13), photos=_fotos(c), output=c.salida / nombre,
                logo=c.logo(), company=empresa.document_heading, website=empresa.website_label, footer=empresa.document_footer, include_map=False,
                **c.personales["reporte"])
    base.update(extra)
    return gen.ReportOptions(**base)


@contextmanager
def sin_teselas(c: Contexto):
    """Fuerza el mapa de respaldo (sin red): las teselas de OSM no se descargan en la prueba."""
    with mock.patch.object(c.mod("maps"), "_tile", side_effect=OSError("sin red (prueba)")):
        yield


@caso("reporte_pdf_sin_croquis")
def _(c: Contexto):
    gen = c.mod("pdf_generator")
    return {"reporte.pdf": gen.generate_report(_opciones(c, "reporte.pdf"))}


@caso("reporte_pdf_con_croquis_y_cedula")
def _(c: Contexto):
    gen = c.mod("pdf_generator")
    opciones = _opciones(c, "reporte.pdf", include_map=True, include_credentials=True, signature=c.entradas["firma.png"],
                         license_card=c.entradas["cedula.jpg"],                          title="Reporte con croquis, cédula y firma")
    with sin_teselas(c):
        return {"reporte.pdf": gen.generate_report(opciones)}


@caso("reporte_pdf_croquis_sin_gps_ingles_tresvizo")
def _(c: Contexto):
    gen = c.mod("pdf_generator")
    sin_gps = [p for p in _fotos(c) if not p.has_gps]
    textos = {"header_title": "Photo report", "document_tag": "REPORT", "photo_label": "PHOTO", "page_label": "Page",
              "cover_date_template": "{month} {day}, {year}", "months": "January|February|March|April|May|June|July|August|September|October|November|December",
              "map_no_gps_note": "The selected photos contain no readable GPS location."}
    with c.ajustes({"branding": {"profile": "tresvizo"}, "documents": {"report_texts": textos}}):
        opciones = _opciones(c, "reporte.pdf", photos=sin_gps, include_map=True, title="Photo report with no GPS", logo=c.logo(),
                             company="TresVizo Engineering", website="www.tresvizo.com")
        with sin_teselas(c):
            return {"reporte.pdf": gen.generate_report(opciones)}


def _datos_croquis(c: Contexto):
    ls, osm = c.mod("location_sketch"), c.mod("osm_vector")
    puntos = [ls.SketchPoint("V1", 28.6330, -106.0700, "Esquina SO"), ls.SketchPoint("V2", 28.6330, -106.0670),
              ls.SketchPoint("V3", 28.6352, -106.0668, "Esquina NE"), ls.SketchPoint("V4", 28.6352, -106.0698)]
    ruta = lambda pts, cerrado=False: osm.VectorPath(points=pts, closed=cerrado)
    def lineas(f):
        return [(28.6325 + f * 0.0001, -106.0705), (28.6340 + f * 0.0001, -106.0690), (28.6355, -106.0672 + f * 0.0001)]
    features = [
        osm.OSMFeature("way/1", "road", "Av. Universidad", [ruta(lineas(0))], {"highway": "primary", "name": "Av. Universidad", "lanes": "4"}),
        osm.OSMFeature("way/2", "road", "Calle Sexta", [ruta(lineas(1))], {"highway": "residential", "name": "Calle Sexta", "width": "8"}),
        osm.OSMFeature("way/3", "building", "Nave 1", [ruta([(28.6334, -106.0695), (28.6334, -106.0685), (28.6340, -106.0685), (28.6340, -106.0695)], True)], {"building": "industrial", "name": "Nave 1"}),
        osm.OSMFeature("way/4", "building", "", [ruta([(28.6342, -106.0680), (28.6342, -106.0675), (28.6346, -106.0675), (28.6346, -106.0680)], True)], {"building": "yes"}),
        osm.OSMFeature("way/5", "water", "Canal", [ruta([(28.6331, -106.0699), (28.6345, -106.0669)])], {"waterway": "canal"}),
        osm.OSMFeature("way/6", "vegetation", "", [ruta([(28.6348, -106.0699), (28.6348, -106.0690), (28.6351, -106.0690), (28.6351, -106.0699)], True)], {"landuse": "grass"}),
        osm.OSMFeature("way/7", "railway", "", [ruta([(28.6329, -106.0702), (28.6353, -106.0671)])], {"railway": "rail"}),
        osm.OSMFeature("way/8", "barrier", "", [ruta([(28.6331, -106.0698), (28.6331, -106.0671), (28.6351, -106.0671)])], {"barrier": "fence"}),
        osm.OSMFeature("contour/1420", "contour", "1420", [ruta([(28.6335, -106.0699), (28.6341, -106.0680), (28.6349, -106.0673)])], {"ele": "1420", "interval": "5"}),
        osm.OSMFeature("contour/1425", "contour", "1425", [ruta([(28.6338, -106.0699), (28.6344, -106.0682), (28.6350, -106.0676)])], {"ele": "1425", "interval": "5"}),
    ]
    return ls.SketchData(title="Croquis de ubicación de prueba", client="Constructora del Norte", project="Nave industrial", location="Chihuahua, Chih.",
                         sketch_date="13/11/2026", geometry="Polígono", map_layer="Base neutra", contour_interval="5", notes="Notas del croquis.",
                         points=puntos, features=features, vectorized_at="2026-11-13 10:00", version=5)


@caso("croquis_pdf_y_dxf")
def _(c: Contexto):
    ls, detallado = c.mod("location_sketch"), c.mod("dxf_export")
    datos = _datos_croquis(c)
    pdf = ls.generate_sketch_pdf(datos, c.salida / "croquis.pdf", logo_path=c.logo())
    dxf = ls.generate_sketch_dxf(datos, c.salida / "croquis.dxf")
    det = detallado.generate_detailed_dxf(datos, c.salida / "croquis_detallado.dxf")
    sin_capas = ls.SketchData.from_dict(json.loads(json.dumps(datos.to_dict())))
    sin_capas.layer_visibility["building"] = False
    sin_capas.layer_visibility["labels"] = False
    pdf2 = ls.generate_sketch_pdf(sin_capas, c.salida / "croquis_sin_edificios.pdf", logo_path=c.logo())
    return {"croquis.pdf": pdf, "croquis.dxf": dxf, "croquis_detallado.dxf": det, "croquis_sin_edificios.pdf": pdf2,
            "datos": datos.to_dict(), "visibles": [f.osm_id for f in ls.visible_features(sin_capas)],
            "prioridad": [f.osm_id for f in sorted(datos.features, key=ls.feature_draw_priority)]}


@caso("croquis_mapa_neutro_y_geodesia")
def _(c: Contexto):
    ls = c.mod("location_sketch")
    datos = _datos_croquis(c)
    snap = ls.render_location_map(datos.points, "Polígono", "Base neutra", datos.features, size=(900, 600))
    ruta = c.salida / "mapa.png"
    snap.image.save(ruta)
    lat, lon, zoom = ls.fit_view_to_points(datos.points, 900, 600, 18)
    zona = ls._utm_zone(datos.points)
    return {"mapa.png": ruta, "vista": [lat, lon, zoom], "zoom": snap.zoom, "izquierda": snap.left_world_px, "arriba": snap.top_world_px,
            "online": snap.online, "capa": snap.layer, "utm": [ls.latlon_to_utm(p.latitude, p.longitude) for p in datos.points], "zona": zona,
            "pixel": [snap.latlon_to_pixel(p.latitude, p.longitude) for p in datos.points],
            "kml": [a_json(asdict(p)) for p in ls.polygon_from_kml(c.mod("geospatial_converter").read_kml(c.entradas["capas.kml"]))[0]],
            "kml_poligonos": ls.polygon_from_kml(c.mod("geospatial_converter").read_kml(c.entradas["capas.kml"]))[1],
            "zoom_intermedio": a_json(ls.reproject_snapshot(snap, (lat, lon), snap.zoom + 1).center())}


ELEMENTOS_OSM = [
    {"type": "way", "id": 101, "tags": {"highway": "primary", "name": "Av. Universidad", "lanes": "4", "oneway": "no"},
     "geometry": [{"lat": 28.6320, "lon": -106.0710}, {"lat": 28.6340, "lon": -106.0690}, {"lat": 28.6360, "lon": -106.0665}]},
    {"type": "way", "id": 102, "tags": {"building": "industrial", "name": "Nave 1"},
     "geometry": [{"lat": 28.6334, "lon": -106.0695}, {"lat": 28.6334, "lon": -106.0685}, {"lat": 28.6340, "lon": -106.0685}, {"lat": 28.6340, "lon": -106.0695}, {"lat": 28.6334, "lon": -106.0695}]},
    {"type": "way", "id": 103, "tags": {"landuse": "residential"},
     "geometry": [{"lat": 28.6330, "lon": -106.0700}, {"lat": 28.6330, "lon": -106.0670}, {"lat": 28.6352, "lon": -106.0670}, {"lat": 28.6352, "lon": -106.0700}, {"lat": 28.6330, "lon": -106.0700}]},
    {"type": "way", "id": 104, "tags": {"waterway": "canal", "name": "Canal 3"}, "geometry": [{"lat": 28.6331, "lon": -106.0699}, {"lat": 28.6345, "lon": -106.0669}]},
    {"type": "way", "id": 105, "tags": {"amenity": "parking"},
     "geometry": [{"lat": 28.6344, "lon": -106.0699}, {"lat": 28.6344, "lon": -106.0692}, {"lat": 28.6347, "lon": -106.0692}, {"lat": 28.6347, "lon": -106.0699}, {"lat": 28.6344, "lon": -106.0699}]},
    {"type": "way", "id": 106, "tags": {"power": "line"}, "geometry": [{"lat": 28.6329, "lon": -106.0702}, {"lat": 28.6353, "lon": -106.0671}]},
    {"type": "way", "id": 107, "tags": {"barrier": "wall"}, "geometry": [{"lat": 28.6331, "lon": -106.0698}, {"lat": 28.6331, "lon": -106.0671}]},
    {"type": "way", "id": 108, "tags": {"leisure": "park", "name": "Parque Lineal"},
     "geometry": [{"lat": 28.6348, "lon": -106.0699}, {"lat": 28.6348, "lon": -106.0690}, {"lat": 28.6351, "lon": -106.0690}, {"lat": 28.6351, "lon": -106.0699}, {"lat": 28.6348, "lon": -106.0699}]},
    {"type": "relation", "id": 109, "tags": {"type": "multipolygon", "building": "retail", "name": "Plaza con patio"},
     "members": [
         {"type": "way", "role": "outer", "geometry": [{"lat": 28.6338, "lon": -106.0680}, {"lat": 28.6338, "lon": -106.0672}, {"lat": 28.6346, "lon": -106.0672}, {"lat": 28.6346, "lon": -106.0680}, {"lat": 28.6338, "lon": -106.0680}]},
         {"type": "way", "role": "inner", "geometry": [{"lat": 28.6341, "lon": -106.0677}, {"lat": 28.6341, "lon": -106.0675}, {"lat": 28.6343, "lon": -106.0675}, {"lat": 28.6343, "lon": -106.0677}, {"lat": 28.6341, "lon": -106.0677}]}]},
    {"type": "node", "id": 110, "lat": 28.6342, "lon": -106.0690, "tags": {"man_made": "water_tower", "name": "Torre"}},
]


@caso("osm_vectorizacion_con_respuesta_grabada")
def _(c: Contexto):
    osm, ls = c.mod("osm_vector"), c.mod("location_sketch")
    puntos = _datos_croquis(c).points
    mensajes: list[str] = []
    with mock.patch.object(osm, "_fetch_query_elements", side_effect=lambda _consulta, _avisar: json.loads(json.dumps(ELEMENTOS_OSM))):
        features = osm.fetch_osm_features(puntos, progress=mensajes.append)
    etiquetas = [{"highway": "motorway"}, {"building": "yes"}, {"natural": "water"}, {"leisure": "park"}, {"railway": "rail"}, {"barrier": "fence"},
                 {"power": "tower"}, {"amenity": "school"}, {"landuse": "farmland"}, {}, {"highway": "footway"}]
    return {"features": [a_json(asdict(f)) for f in features], "avisos": mensajes, "conteos": osm.feature_counts(features),
            "area_km2": osm.selection_area_km2(puntos),
            "clasificacion": [osm.classify_osm_tags(t) for t in etiquetas],
            "anchos": [osm.road_width_m(t) for t in ({"highway": "primary"}, {"highway": "residential", "width": "9.5"}, {"highway": "service", "lanes": "2"}, {"highway": "path"})]}


def _tesela_sintetica(x: int, y: int, zoom: int):
    import numpy as np
    j, i = np.meshgrid(np.arange(256), np.arange(256))
    base = 1420.0 + 18.0 * np.sin((x * 256 + j) / 61.0) + 11.0 * np.cos((y * 256 + i) / 47.0) + ((x * 256 + j) % 5000) * 0.002
    return base.astype("float32")


@caso("curvas_de_nivel_con_relieve_sintetico")
def _(c: Contexto):
    terreno = c.mod("terrain_contours")
    puntos = _datos_croquis(c).points
    salida = {}
    with mock.patch.object(terreno, "_terrain_tile", side_effect=_tesela_sintetica):
        for intervalo in ("Automática", "1 m"):
            feats = terreno.fetch_elevation_contours(puntos, intervalo)
            salida[f"curvas_{intervalo.split()[0]}"] = {"cantidad": len(feats), "features": [a_json(asdict(f)) for f in feats]}
    return salida


def _resumen_dataset(ds) -> dict:
    return {"capas": ds.layer_counts(), "geometrias": ds.geometry_counts(), "limites": ds.bounds(),
            "entidades": [{"nombre": f.name, "capa": f.layer, "tipo": f.geometry_type, "partes": [[(round(x, 6), round(y, 6), round(z, 3)) for (x, y, z) in part] for part in f.parts]}
                          for f in ds.features] if hasattr(ds, "features") and ds.features and True else None}


@caso("convertidor_dxf_a_kml_kmz")
def _(c: Contexto):
    gc = c.mod("geospatial_converter")
    ds = gc.read_geospatial(c.entradas["convertidor.dxf"], zone=13, hemisphere="N", curve_tolerance=0.5)
    salida = {"resumen": _resumen_dataset(ds), "utm_sugerida": list(gc.suggested_utm(ds)),
              "colores": {capa: gc.layer_color(capa) for capa in sorted(ds.layer_counts())}}
    salida["salida.kml"] = gc.convert_file(ds, c.salida / "salida.kml", zone=13, hemisphere="N", add_labels=True, clamp_to_ground=True)
    salida["sin_nombres.kml"] = gc.convert_file(ds, c.salida / "sin_nombres.kml", zone=13, hemisphere="N", add_labels=False, clamp_to_ground=False,
                                               selected_layers={"PREDIO", "CURVAS"})
    salida["salida.kmz"] = gc.convert_file(ds, c.salida / "salida.kmz", zone=13, hemisphere="N", add_labels=True, clamp_to_ground=True)
    return salida


@caso("convertidor_kml_kmz_a_dxf")
def _(c: Contexto):
    gc = c.mod("geospatial_converter")
    salida = {}
    for origen in ("capas.kml", "capas.kmz"):
        ds = gc.read_geospatial(c.entradas[origen])
        salida[f"resumen_{origen}"] = _resumen_dataset(ds)
        zona = gc.suggested_utm(ds)
        salida[f"salida_{origen}.dxf"] = gc.convert_file(ds, c.salida / f"salida_{origen}.dxf", zone=zona[0], hemisphere=zona[1], add_labels=True, add_hatches=True)
        salida[f"sin_hatch_{origen}.dxf"] = gc.convert_file(ds, c.salida / f"sin_hatch_{origen}.dxf", zone=13, hemisphere="N", add_labels=False, add_hatches=False)
    errores = []
    for ruta in ("archivo.txt",):
        try:
            gc.read_geospatial(ruta)
        except Exception as exc:
            errores.append([type(exc).__name__, str(exc)])
    try:
        gc.convert_file(gc.read_geospatial(c.entradas["capas.kml"]), c.salida / "x.shp")
    except Exception as exc:
        errores.append([type(exc).__name__, str(exc)])
    salida["errores"] = errores
    return salida


@caso("triangulacion_tin_pendientes_y_escurrimiento")
def _(c: Contexto):
    tri = c.mod("triangulation")
    modelo = tri.create_tin_model(c.entradas["puntos.dxf"], max_edge=120.0)
    tin = tri.write_tin_dxf(modelo, c.salida / "tin.dxf")
    triangulos = tri.extract_triangles(tin)
    pendientes = tri.analyze_slopes(triangulos, tri.clone_default_ranges(), "tin.dxf")
    flujo = tri.analyze_flow(triangulos, tri.clone_default_flow_ranges(), "tin.dxf", density=1, minimum_slope=0.5)
    flujo2 = tri.analyze_flow(triangulos, tri.clone_default_flow_ranges(), "tin.dxf", density=2, head_ratio=0.35, minimum_slope=1.0)
    return {
        "estadisticas": a_json(asdict(modelo.stats)), "puntos": len(modelo.points), "triangulos": len(modelo.triangles), "limites": modelo.bounds,
        "capas": tri.triangle_layers(tin), "pendiente_primer_triangulo": tri.triangle_slope_percent(triangulos[0]),
        "tin.dxf": tin,
        "pendientes": a_json({"rangos": [asdict(r) for r in pendientes.ranges] if hasattr(pendientes, "ranges") else None, "area_total": pendientes.total_area}),
        "pendientes.dxf": tri.write_slope_dxf(pendientes, c.salida / "pendientes.dxf", include_slope_text=True),
        "pendientes_sin_caras.dxf": tri.write_slope_dxf(pendientes, c.salida / "pendientes_sin_caras.dxf", include_3d_faces=False, decimals=3),
        "flechas": len(flujo.arrows), "flechas_densidad_2": len(flujo2.arrows),
        "escurrimiento.dxf": tri.write_flow_dxf(flujo, c.salida / "escurrimiento.dxf"),
        "escurrimiento_sin_tin.dxf": tri.write_flow_dxf(flujo2, c.salida / "escurrimiento_sin_tin.dxf", include_tin_reference=False, include_slope_text=True),
    }


@caso("triangulacion_limites_y_errores")
def _(c: Contexto):
    tri = c.mod("triangulation")
    errores = []
    for nombre, f in {
        "filtros_total": lambda: tri.create_tin_model(c.entradas["puntos.dxf"], max_edge=0.0001),
        "sin_puntos": lambda: tri.create_tin_model(c.entradas["puntos.dxf"], include_points=False, include_inserts=False),
        "rangos_invalidos": lambda: tri.validate_ranges([tri.SlopeRange("a", 5, 2, "#FF0000")]) if hasattr(tri.SlopeRange, "__dataclass_fields__") else None,
        "flujo_sin_triangulos": lambda: tri.analyze_flow([], tri.clone_default_flow_ranges()),
        "punta": lambda: tri.analyze_flow([((0, 0, 1), (10, 0, 0), (0, 10, 0))], tri.clone_default_flow_ranges(), head_ratio=0.9),
    }.items():
        try:
            f()
            errores.append([nombre, None, None])
        except Exception as exc:
            errores.append([nombre, type(exc).__name__, str(exc)])
    unicos, duplicados = tri.deduplicate_xy([(0, 0, 1), (0, 0, 2), (1, 1, 1)])
    return {"errores": errores, "dedup": [unicos, duplicados]}


TEXTOS_ORTOGRAFIA = {
    "es": "Levantamiento topográfico con ortomosaico y planimetria de la nave. Se instalarón mojoneras y blokes de concreto. El cliente reviso el avance. GNSS RTK UTM.",
    "en": "Topographic survey with orthomosaic and photogrametry of the building. Concrete blokes were installed and the client reviewed the progres. DXF and KMZ files.",
}


@caso("corrector_ortografico")
def _(c: Contexto):
    sc = c.mod("spellcheck")
    salida = {}
    for idioma, texto in TEXTOS_ORTOGRAFIA.items():
        servicio = sc.BilingualSpellService(idioma)
        campo = sc.ReviewField("Texto", texto, lambda nuevo: None)
        documentos, problemas = servicio.scan([campo])
        salida[idioma] = {"problemas": [[p.word, p.token_index, servicio.suggestions(p.word)] for p in problemas],
                          "conocidas": {w: servicio.is_known(w) for w in ("topografía", "ortomosaico", "casa", "house", "xyzzy", "gnss", "Chihuahua", "mojonera")},
                          "sugerencias_mayuscula": servicio.suggestions("Planimetria"),
                          "palabras": len(documentos[0].matches)}
        documentos[0].replace(problemas[0].token_index, "x")
        salida[idioma]["resultado"] = documentos[0].result()
    return salida


def claves() -> list[str]:
    """«perfil/caso» de todo lo que se compara."""
    return sorted(f"{perfil}/{nombre}" for perfil in PERFILES for nombre in CASOS)
