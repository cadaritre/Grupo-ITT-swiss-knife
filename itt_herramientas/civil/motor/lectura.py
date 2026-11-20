"""Lectura de los puntos de Civil de un dibujo DXF/DWG (ezdxf), en los dos modelos.

- **Modelo actual:** una entidad POINT en la capa de puntos de terreno o de proyecto, con XDATA `TVCIVIL` [punto, número, tipo], y un TEXT
  por rótulo en su capa con XDATA [rotulo, número, tipo, rol, handle del punto].
- **Modelo anterior:** un INSERT del bloque `PT_TERRENO` o `PT_PROYECTO` con los atributos NUM, ELEV y DESC.

La página hace lo mismo con la API de control del editor; este módulo es la referencia para las pruebas y para quien lea un archivo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .dibujos import APP, BLOQUES, CAPA_DE_ROL, CAPAS_PUNTOS
from .puntos import PROYECTO, TERRENO

__all__ = ["PuntoLeido", "leer_puntos"]


@dataclass
class PuntoLeido:
    handle: str
    tipo: str
    x: float
    y: float
    z: float
    numero: str = ""
    elevacion: str = ""
    descripcion: str = ""
    rotulos: dict[str, str] = field(default_factory=dict)  # rol → handle del TEXT
    legado: bool = False  # bloque del modelo anterior
    escala: float | None = None  # solo el bloque: su escala de inserción (E/1000)


def _datos(entidad) -> list | None:
    try:
        return entidad.get_xdata(APP)
    except Exception:
        return None


def _valores(xdata) -> list:
    return [v for _, v in xdata]


def leer_puntos(doc, tipos=(TERRENO, PROYECTO)) -> list[PuntoLeido]:
    msp = doc.modelspace()
    capa_tipo = {CAPAS_PUNTOS[t].nombre: t for t in tipos}
    salida: list[PuntoLeido] = []
    por_handle: dict[str, PuntoLeido] = {}
    for e in msp.query("POINT"):
        if e.dxf.layer not in capa_tipo:
            continue
        v = _valores(_datos(e) or [])
        if v and v[0] != "punto":
            continue
        x, y, z = (float(c) for c in e.dxf.location)
        p = PuntoLeido(e.dxf.handle, capa_tipo[e.dxf.layer], x, y, z, numero=str(v[1]) if len(v) > 1 else "")
        salida.append(p)
        por_handle[p.handle.upper()] = p
    # rótulos: primero por el handle del punto; los que no lo resuelven, por (tipo, número, rol) y cercanía
    libres: list[tuple[str, str, str, str, float, float, str]] = []
    capas_rol = {CAPAS_PUNTOS[c].nombre: rol for rol, c in CAPA_DE_ROL.items()}
    for e in msp.query("TEXT"):
        if e.dxf.layer not in capas_rol:
            continue
        v = _valores(_datos(e) or [])
        if len(v) < 4 or v[0] != "rotulo":
            continue
        numero, tipo, rol = str(v[1]), str(v[2]), str(v[3])
        destino = por_handle.get(str(v[4]).upper()) if len(v) > 4 else None
        if destino is not None and (destino.numero, destino.tipo) == (numero, tipo) and rol not in destino.rotulos:
            _poner(destino, rol, e)
        else:
            libres.append((e.dxf.handle, numero, tipo, rol, float(e.dxf.insert[0]), float(e.dxf.insert[1]), e.dxf.text))
    candidatos: dict[tuple[str, str], list[PuntoLeido]] = {}
    for p in salida:
        candidatos.setdefault((p.tipo, p.numero), []).append(p)
    for handle, numero, tipo, rol, x, y, texto in libres:
        cerca = [p for p in candidatos.get((tipo, numero), []) if rol not in p.rotulos]
        if cerca:
            p = min(cerca, key=lambda q: math.hypot(q.x - x, q.y - y))
            p.rotulos[rol] = handle
            _asignar(p, rol, texto)
    # modelo anterior
    nombres = {b: t for t, b in BLOQUES.items() if t in tipos}
    for e in msp.query("INSERT"):
        if e.dxf.name not in nombres:
            continue
        a = {x.dxf.tag: x.dxf.text for x in e.attribs}
        ins = tuple(float(c) for c in e.dxf.insert)
        salida.append(PuntoLeido(e.dxf.handle, nombres[e.dxf.name], *ins, numero=a.get("NUM", ""), elevacion=a.get("ELEV", ""), descripcion=a.get("DESC", ""),
                                 legado=True, escala=float(e.dxf.get("xscale", 1.0))))
    return salida


def _poner(p: PuntoLeido, rol: str, texto) -> None:
    p.rotulos[rol] = texto.dxf.handle
    _asignar(p, rol, texto.dxf.text)


def _asignar(p: PuntoLeido, rol: str, valor: str) -> None:
    setattr(p, {"num": "numero", "elev": "elevacion", "desc": "descripcion"}[rol], valor)
