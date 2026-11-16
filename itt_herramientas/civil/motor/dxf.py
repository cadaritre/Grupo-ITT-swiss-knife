"""Escritura de un `Dibujo` como DXF con ezdxf (dependencia opcional `tvcivil[dxf]`)."""

from __future__ import annotations

import io
from pathlib import Path

from .entidades import (
    POR_CAPA,
    Bloque,
    Cara3D,
    Dibujo,
    Insercion,
    Linea,
    Polilinea,
    Polilinea3D,
    PuntoCad,
    Sombreado,
    Texto,
    TextoMultiple,
)

__all__ = ["a_documento", "a_bytes", "guardar"]

_H = {"izquierda": 0, "centro": 1, "derecha": 2}
_V = {"base": 0, "abajo": 1, "medio": 2, "arriba": 3}
_ANCLAJE = {
    "arriba_izquierda": 1, "arriba_centro": 2, "arriba_derecha": 3,
    "medio_izquierda": 4, "medio_centro": 5, "medio_derecha": 6,
    "abajo_izquierda": 7, "abajo_centro": 8, "abajo_derecha": 9,
}


def _attrs(e, extra=None):
    a = {"layer": e.capa}
    if e.color != POR_CAPA:
        a["color"] = e.color
    if extra:
        a.update(extra)
    return a


def _xdata(doc, entidad, datos):
    if not datos:
        return
    for app, valores in datos.items():
        if app not in doc.appids:
            doc.appids.add(app)
        entidad.set_xdata(app, list(valores))


def _texto_alineado(espacio, t: Texto):
    ent = espacio.add_text(t.valor, height=t.altura, rotation=t.rotacion, dxfattribs=_attrs(t, {"style": t.estilo}))
    h, v = _H[t.alineacion_h], _V[t.alineacion_v]
    ent.dxf.halign = h
    ent.dxf.valign = v
    ent.dxf.insert = (t.posicion[0], t.posicion[1])
    if h or v:
        ent.dxf.align_point = (t.posicion[0], t.posicion[1])
    return ent


def _entidad(doc, espacio, e):
    if isinstance(e, Linea):
        ent = espacio.add_line(e.inicio, e.fin, dxfattribs=_attrs(e))
    elif isinstance(e, Polilinea):
        combas = e.combas or [0.0] * len(e.vertices)
        ent = espacio.add_lwpolyline([(x, y, 0, 0, b) for (x, y), b in zip(e.vertices, combas)], format="xyseb", close=e.cerrada, dxfattribs=_attrs(e, {"elevation": e.elevacion}))
    elif isinstance(e, Polilinea3D):
        ent = espacio.add_polyline3d(e.vertices, close=e.cerrada, dxfattribs=_attrs(e))
    elif isinstance(e, PuntoCad):
        ent = espacio.add_point(e.posicion, dxfattribs=_attrs(e))
    elif isinstance(e, Texto):
        ent = _texto_alineado(espacio, e)
    elif isinstance(e, TextoMultiple):
        ent = espacio.add_mtext(e.valor.replace("\n", "\\P"), dxfattribs=_attrs(e, {
            "style": e.estilo, "char_height": e.altura, "insert": e.posicion, "rotation": e.rotacion,
            "attachment_point": _ANCLAJE[e.anclaje], **({"width": e.ancho} if e.ancho else {}),
        }))
        if e.fondo:
            ent.set_bg_color("canvas", scale=min(5.0, max(1.0, e.fondo_escala)))
            ent.dxf.bg_fill_transparency = 0                  # 441: AutoCAD lo escribe siempre con el fondo (opaco)
    elif isinstance(e, Cara3D):
        v = list(e.vertices)
        if len(v) == 3:
            v.append(v[2])
        ent = espacio.add_3dface(v, dxfattribs=_attrs(e))
    elif isinstance(e, Insercion):
        ent = espacio.add_blockref(e.bloque, e.posicion, dxfattribs=_attrs(e, {"xscale": e.escala, "yscale": e.escala, "zscale": e.escala, "rotation": e.rotacion}))
        if e.atributos:
            ent.add_auto_attribs(e.atributos)
    elif isinstance(e, Sombreado):
        ent = espacio.add_hatch(color=e.color if e.color != POR_CAPA else 256, dxfattribs={"layer": e.capa})
        if not e.solido:
            ent.set_pattern_fill(e.patron, scale=e.escala_patron)
        ent.paths.add_polyline_path(e.contorno, is_closed=True)
    else:
        raise TypeError(f"Entidad no soportada: {type(e).__name__}")
    _xdata(doc, ent, getattr(e, "datos", None))
    return ent


def _bloque(doc, b: Bloque):
    if b.nombre in doc.blocks:
        return
    blk = doc.blocks.new(b.nombre, base_point=b.base)
    for e in b.entidades:
        _entidad(doc, blk, e)
    for a in b.atributos:
        ad = blk.add_attdef(a.etiqueta, a.posicion, a.valor, dxfattribs={"height": a.altura, "style": a.estilo, "layer": a.capa})
        if a.invisible:
            ad.dxf.flags = 1
        if a.alineacion_h != "izquierda" or a.alineacion_v != "base":
            ad.dxf.halign = _H[a.alineacion_h]
            ad.dxf.valign = _V[a.alineacion_v]
            ad.dxf.align_point = a.posicion


def a_documento(dibujo: Dibujo, version: str = "R2018"):
    import ezdxf

    doc = ezdxf.new(version, setup=True, units=6)  # 6 = metros
    for c in dibujo.capas.values():
        if c.nombre == "0":
            continue
        capa = doc.layers.add(c.nombre, color=c.color, linetype=c.tipo_linea if c.tipo_linea in doc.linetypes else "Continuous")
        if c.grosor_mm is not None:
            capa.dxf.lineweight = int(round(c.grosor_mm * 100))
        if c.descripcion:
            capa.description = c.descripcion
    for s in dibujo.estilos.values():
        if s.nombre in doc.styles:
            st = doc.styles.get(s.nombre)
            st.dxf.font = s.fuente
        else:
            st = doc.styles.add(s.nombre, font=s.fuente)
        st.dxf.width = s.factor_ancho
    for b in dibujo.bloques.values():
        _bloque(doc, b)
    msp = doc.modelspace()
    for e in dibujo.entidades:
        _entidad(doc, msp, e)
    return doc


def a_bytes(dibujo: Dibujo, version: str = "R2018") -> bytes:
    doc = a_documento(dibujo, version)
    s = io.StringIO()
    doc.write(s)
    return s.getvalue().encode("utf-8")


def guardar(dibujo: Dibujo, ruta: str | Path, version: str = "R2018") -> Path:
    ruta = Path(ruta)
    a_documento(dibujo, version).saveas(ruta)
    return ruta
