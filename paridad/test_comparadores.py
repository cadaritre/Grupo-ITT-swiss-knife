"""Los comparadores detectan diferencias reales y toleran solo lo que deben."""
from __future__ import annotations

import zipfile

import comparadores as C


def test_json_ignora_uuid_y_fechas_pero_no_valores(tmp_path):
    a, b, c = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    a.write_text('{"id": "123e4567-e89b-12d3-a456-426614174000", "t": "2026-11-13T10:00:00", "n": 1}')
    b.write_text('{"id": "aaaaaaaa-e89b-12d3-a456-426614174000", "t": "2027-01-01T00:00:00.5", "n": 1}')
    c.write_text('{"id": "123e4567-e89b-12d3-a456-426614174000", "t": "2026-11-13T10:00:00", "n": 2}')
    assert C.comparar(a, b).nivel == C.EXACTO
    r = C.comparar(a, c)
    assert r.nivel == C.DISTINTO and "$.n" in r.motivo


def test_pdf_unifica_nombres_derivados_de_la_ruta_y_detecta_contenido():
    def pdf(nombre: bytes, ref: bytes = b"3 0 R") -> bytes:
        return b"%PDF-1.4\n/XObject << /FormXob." + nombre * 32 + b" " + ref + b" >>\n%%EOF"
    assert C.normalizar_pdf(pdf(b"a")) == C.normalizar_pdf(pdf(b"b"))
    assert C.normalizar_pdf(pdf(b"a")) != C.normalizar_pdf(pdf(b"a", b"4 0 R"))


def test_pdf_distinto_se_reporta(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"%PDF-1.4 uno")
    b.write_bytes(b"%PDF-1.4 dos")
    r = C.comparar(a, b)
    assert r.nivel == C.DISTINTO and "bytes distintos" in r.motivo


def test_dxf_ignora_cabecera_de_ejecucion_y_no_el_contenido(tmp_path):
    def dxf(guid, fecha, x):
        return (f"0\nSECTION\n2\nHEADER\n9\n$FINGERPRINTGUID\n2\n{guid}\n9\n$TDCREATE\n40\n{fecha}\n9\n$INSUNITS\n70\n6\n0\nENDSEC\n"
                f"0\nSECTION\n2\nENTITIES\n0\nPOINT\n10\n{x}\n20\n2.0\n0\nENDSEC\n0\nEOF\n")
    a, b, c = tmp_path / "a.dxf", tmp_path / "b.dxf", tmp_path / "c.dxf"
    a.write_text(dxf("AAA", "2461000.1", "1.0"))
    b.write_text(dxf("BBB", "2461999.9", "1.0"))
    c.write_text(dxf("AAA", "2461000.1", "1.5"))
    assert C.comparar(a, b).ok
    assert C.comparar(a, c).nivel == C.DISTINTO


def test_dxf_normaliza_la_marca_de_ezdxf():
    assert C.normalizar_dxf("1\n1.4.5 @ 2026-11-13T20:30:14.216932+00:00\n") == [("1", "1.4.5 @ <fecha>")]


KML = '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>{n}</name></Document></kml>'


def test_kml_canonico_ignora_espacios_y_kmz_compara_contenido(tmp_path):
    a, b = tmp_path / "a.kml", tmp_path / "b.kml"
    a.write_text(KML.format(n="x"))
    b.write_text("<kml  xmlns='http://www.opengis.net/kml/2.2'>\n<Document>  <name>x</name></Document></kml>")
    assert C.comparar(a, b).nivel == C.EXACTO
    b.write_text(KML.format(n="y"))
    assert C.comparar(a, b).nivel == C.DISTINTO
    za, zb = tmp_path / "a.kmz", tmp_path / "b.kmz"
    for ruta, nombre in ((za, "x"), (zb, "x")):
        with zipfile.ZipFile(ruta, "w") as zf:
            zf.writestr("doc.kml", KML.format(n=nombre))
    assert C.comparar(za, zb).nivel == C.EXACTO
    with zipfile.ZipFile(zb, "w") as zf:
        zf.writestr("doc.kml", KML.format(n="otro"))
    assert C.comparar(za, zb).nivel == C.DISTINTO


def test_png_exacto_tolerante_y_distinto(tmp_path):
    from PIL import Image
    a, b, c = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"
    Image.new("RGB", (100, 100), "white").save(a)
    Image.new("RGB", (100, 100), "white").save(b)
    imagen = Image.new("RGB", (100, 100), "white")
    for x in range(10):
        imagen.putpixel((x, 0), (0, 0, 0))
    imagen.save(c)
    assert C.comparar(a, b).nivel == C.EXACTO
    assert C.comparar(a, c).nivel == C.DISTINTO
    assert C.comparar(a, c, tolerante=True).nivel == C.TOLERANTE


def test_pdf_tolerante_acepta_otra_tipografia_y_rechaza_otro_texto(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    def pdf(nombre, texto, fuente):
        ruta = tmp_path / nombre
        c = Canvas(str(ruta), invariant=1)
        c.setFont(fuente, 14)
        c.drawString(72, 700, texto)
        c.save()
        return ruta
    a = pdf("a.pdf", "Cotización 123", "Helvetica")
    b = pdf("b.pdf", "Cotización 123", "Helvetica-Bold")
    c = pdf("c.pdf", "Cotización 124", "Helvetica")
    assert C.comparar(a, b, tolerante=True).ok
    r = C.comparar(a, c, tolerante=True)
    assert r.nivel == C.DISTINTO and "texto" in r.motivo
