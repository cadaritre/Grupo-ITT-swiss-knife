"""Civil: la memoria descriptiva y el reporte de puntos como PDF (ReportLab, Liberation Sans) o DOCX (Office Open XML).

El motor da el contenido (`tvcivil.memoria.Documento.como_dict()`: título, datos, párrafos, tabla y notas); aquí solo se le da forma.
El DOCX se escribe a mano (unos cuantos XML en un ZIP): abre en Word, LibreOffice y Google Docs, sin otra dependencia.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date
from xml.sax.saxutils import escape

from ..dominio import fuentes

FORMATOS = {"pdf": ("application/pdf", "pdf"), "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx")}


def _texto(v) -> str:
    return "".join(ch for ch in str(v or "") if ch in "\t\n" or ord(ch) >= 32)


# -- PDF -------------------------------------------------------------------------------------------------------------------------

def a_pdf(doc: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    regular, negrita = fuentes.registrar_pdf()
    columnas = doc.get("columnas") or []
    ancho_pagina = landscape(letter) if len(columnas) > 7 else letter
    salida = io.BytesIO()
    plantilla = SimpleDocTemplate(salida, pagesize=ancho_pagina, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                                  title=_texto(doc.get("titulo")), author="", creator="")
    titulo = ParagraphStyle("t", fontName=negrita, fontSize=14, leading=18, alignment=TA_CENTER, spaceAfter=10)
    normal = ParagraphStyle("n", fontName=regular, fontSize=10, leading=14, alignment=TA_JUSTIFY, spaceAfter=6)
    dato = ParagraphStyle("d", fontName=regular, fontSize=10, leading=13)
    nota = ParagraphStyle("o", fontName=regular, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
    celda = ParagraphStyle("c", fontName=regular, fontSize=7.5 if len(columnas) > 7 else 8.5, leading=10, alignment=TA_CENTER)
    cabeza = ParagraphStyle("h", parent=celda, fontName=negrita)
    partes = [Paragraph(escape(_texto(doc.get("titulo"))), titulo)]
    for etiqueta, valor in doc.get("datos") or []:
        partes.append(Paragraph(f"<font name='{negrita}'>{escape(_texto(etiqueta))}:</font> {escape(_texto(valor))}", dato))
    if doc.get("datos"):
        partes.append(Spacer(1, 6))
    for p in doc.get("parrafos") or []:
        partes.append(Paragraph(escape(_texto(p)), normal))
    if columnas:
        if doc.get("tabla_titulo"):
            partes += [Spacer(1, 6), Paragraph(escape(_texto(doc["tabla_titulo"])), ParagraphStyle("tt", parent=titulo, fontSize=11, spaceAfter=4))]
        filas = [[Paragraph(escape(_texto(c)), cabeza) for c in columnas]]
        filas += [[Paragraph(escape(_texto(c)), celda) for c in f] for f in doc.get("filas") or []]
        tabla = LongTable(filas, repeatRows=1, hAlign="CENTER")
        tabla.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#444444")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                                   ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        partes.append(tabla)
    if doc.get("notas"):
        partes.append(Spacer(1, 8))
        partes += [Paragraph(escape(_texto(n)), nota) for n in doc["notas"]]

    def pie(lienzo, plantilla_):
        lienzo.saveState()
        lienzo.setFont(regular, 8)
        lienzo.setFillColor(colors.HexColor("#777777"))
        lienzo.drawRightString(ancho_pagina[0] - 18 * mm, 10 * mm, f"Página {lienzo.getPageNumber()} · {date.today():%d/%m/%Y}")
        lienzo.restoreState()

    plantilla.build(partes, onFirstPage=pie, onLaterPages=pie)
    return salida.getvalue()


# -- DOCX ------------------------------------------------------------------------------------------------------------------------

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _run(texto: str, negrita: bool = False, tam: int | None = None) -> str:
    props = ("<w:b/>" if negrita else "") + (f'<w:sz w:val="{tam}"/>' if tam else "")
    return f'<w:r>{f"<w:rPr>{props}</w:rPr>" if props else ""}<w:t xml:space="preserve">{escape(_texto(texto))}</w:t></w:r>'


def _parrafo(runs: str, alinear: str = "both", espacio: int = 120) -> str:
    return f'<w:p><w:pPr><w:spacing w:after="{espacio}"/><w:jc w:val="{alinear}"/></w:pPr>{runs}</w:p>'


def a_docx(doc: dict) -> bytes:
    columnas = doc.get("columnas") or []
    horizontal = len(columnas) > 7
    cuerpo = [_parrafo(_run(doc.get("titulo", ""), True, 28), "center", 240)]
    for etiqueta, valor in doc.get("datos") or []:
        cuerpo.append(_parrafo(_run(f"{etiqueta}: ", True) + _run(valor), "left", 60))
    for p in doc.get("parrafos") or []:
        cuerpo.append(_parrafo(_run(p)))
    if columnas:
        if doc.get("tabla_titulo"):
            cuerpo.append(_parrafo(_run(doc["tabla_titulo"], True, 22), "center", 80))
        borde = "".join(f'<w:{b} w:val="single" w:sz="4" w:space="0" w:color="444444"/>' for b in ("top", "left", "bottom", "right", "insideH", "insideV"))

        def fila(celdas, cabeza=False):
            tr = '<w:trPr><w:tblHeader/></w:trPr>' if cabeza else ""
            sombra = '<w:shd w:val="clear" w:color="auto" w:fill="EEEEEE"/>' if cabeza else ""
            return f"<w:tr>{tr}" + "".join(f"<w:tc><w:tcPr>{sombra}</w:tcPr>{_parrafo(_run(c, cabeza, 16), 'center', 0)}</w:tc>" for c in celdas) + "</w:tr>"

        cuerpo.append(f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="5000" w:type="pct"/><w:jc w:val="center"/><w:tblBorders>{borde}</w:tblBorders></w:tblPr>'
                      + fila(columnas, True) + "".join(fila(f) for f in doc.get("filas") or []) + "</w:tbl>")
    for n in doc.get("notas") or []:
        cuerpo.append(_parrafo(_run(n, False, 17), "left", 40))
    ancho, alto = (15840, 12240) if horizontal else (12240, 15840)
    seccion = (f'<w:sectPr><w:pgSz w:w="{ancho}" w:h="{alto}"{" w:orient=\"landscape\"" if horizontal else ""}/>'
               '<w:pgMar w:top="1020" w:right="1020" w:bottom="1020" w:left="1020" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
    documento = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_W}><w:body>{"".join(cuerpo)}{seccion}</w:body></w:document>'
    estilos = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {_W}><w:docDefaults><w:rPrDefault><w:rPr>'
               '<w:rFonts w:ascii="Liberation Sans" w:hAnsi="Liberation Sans" w:cs="Liberation Sans"/><w:sz w:val="20"/><w:lang w:val="es-MX"/></w:rPr>'
               '</w:rPrDefault></w:docDefaults><w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/></w:style></w:styles>')
    tipos = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    raiz = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", tipos)
        z.writestr("_rels/.rels", raiz)
        z.writestr("word/document.xml", documento)
        z.writestr("word/styles.xml", estilos)
        z.writestr("word/_rels/document.xml.rels", rels)
    return salida.getvalue()


def archivo(doc: dict, formato: str) -> tuple[bytes, str, str]:
    """(contenido, tipo MIME, extensión) del documento en `formato` («pdf» o «docx»)."""
    if formato not in FORMATOS:
        raise ValueError(formato)
    contenido = a_pdf(doc) if formato == "pdf" else a_docx(doc)
    tipo, ext = FORMATOS[formato]
    return contenido, tipo, ext
