"""Dibujo de puntos (bloque con atributos) y de la triangulación (caras 3D) con las convenciones de Civil."""

from __future__ import annotations

from typing import Iterable, Sequence

from .entidades import Bloque, Capa, Cara3D, DefAtributo, Dibujo, EstiloTexto, Insercion, Linea, PuntoCad
from .escala import ALTURAS_MM, Escala
from .puntos import PROYECTO, TERRENO, Punto

__all__ = ["CAPAS_PUNTOS", "CAPAS_TIN", "BLOQUES", "bloque_punto", "dibujar_puntos", "dibujar_tin", "APP"]

APP = "TVCIVIL"

CAPAS_PUNTOS = {
    TERRENO: Capa("PUNTOS_TERRENO", 3, descripcion="Puntos de terreno"),
    PROYECTO: Capa("PUNTOS_PROYECTO", 5, descripcion="Puntos de proyecto"),
    "numero": Capa("PUNTOS_NUMERO", 7, descripcion="Número de punto"),
    "elevacion": Capa("PUNTOS_ELEVACION", 2, descripcion="Elevación de punto"),
    "descripcion": Capa("PUNTOS_DESCRIPCION", 4, descripcion="Descripción de punto"),
}
CAPAS_TIN = {
    TERRENO: Capa("TRIANGULACION", 8, descripcion="Triangulación del terreno"),
    PROYECTO: Capa("TRIANGULACION_PROYECTO", 6, descripcion="Triangulación de proyecto"),
}
BLOQUES = {TERRENO: "PT_TERRENO", PROYECTO: "PT_PROYECTO"}


def bloque_punto(tipo: str, estilo: str = "CIVIL") -> Bloque:
    """Bloque del punto dibujado en mm de papel (se inserta a escala E/1000): símbolo en cruz con círculo y tres atributos.

    Número arriba a la derecha, elevación a la derecha y descripción abajo a la derecha, cada uno en su capa.
    """
    r = ALTURAS_MM["punto_simbolo"] / 2
    capa = "0"  # la geometría del bloque toma la capa de la inserción
    ents = [
        PuntoCad(capa, posicion=(0.0, 0.0, 0.0)),
        Linea(capa, inicio=(-r, 0.0, 0.0), fin=(r, 0.0, 0.0)),
        Linea(capa, inicio=(0.0, -r, 0.0), fin=(0.0, r, 0.0)),
    ]
    hn, he, hd = ALTURAS_MM["punto_numero"], ALTURAS_MM["punto_elevacion"], ALTURAS_MM["punto_descripcion"]
    x = r * 1.6
    atributos = [
        DefAtributo("NUM", (x, he * 0.9), hn, estilo=estilo, capa=CAPAS_PUNTOS["numero"].nombre),
        DefAtributo("ELEV", (x, -he * 0.45), he, estilo=estilo, capa=CAPAS_PUNTOS["elevacion"].nombre),
        DefAtributo("DESC", (x, -he * 0.45 - hd * 1.5), hd, estilo=estilo, capa=CAPAS_PUNTOS["descripcion"].nombre),
    ]
    return Bloque(BLOQUES[tipo], ents, atributos)


def dibujar_puntos(
    puntos: Iterable[Punto],
    escala: Escala | None = None,
    decimales_z: int = 3,
    numero: bool = True,
    elevacion: bool = True,
    descripcion: bool = True,
    estilo: str = "CIVIL",
) -> Dibujo:
    """Un bloque PT_TERRENO o PT_PROYECTO por punto, en la Z del punto, a la escala del plano.

    Un campo apagado queda con el atributo vacío (se puede volver a llenar). XDATA: [punto, número, tipo].
    """
    esc = escala or Escala()
    f = float(esc.denominador) / 1000.0 * esc.unidades_por_metro
    d = Dibujo()
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    for k in ("numero", "elevacion", "descripcion"):
        d.capa(CAPAS_PUNTOS[k])
    for p in puntos:
        if p.tipo not in BLOQUES:
            raise ValueError(f"Tipo de punto desconocido: {p.tipo}")
        if BLOQUES[p.tipo] not in d.bloques:
            d.bloques[BLOQUES[p.tipo]] = bloque_punto(p.tipo, estilo)
            d.capa(CAPAS_PUNTOS[p.tipo])
        z = 0.0 if p.z is None else float(p.z)
        attrs = {
            "NUM": p.numero if numero else "",
            "ELEV": (f"{p.z:.{decimales_z}f}" if p.z is not None else "") if elevacion else "",
            "DESC": p.descripcion if descripcion else "",
        }
        d.agregar(Insercion(CAPAS_PUNTOS[p.tipo].nombre, bloque=BLOQUES[p.tipo], posicion=(float(p.x), float(p.y), z), escala=f,
                            atributos=attrs, datos={APP: [(1000, "punto"), (1000, p.numero), (1000, p.tipo)]}))
    return d


def dibujar_tin(tin_o_caras, tipo: str = TERRENO) -> Dibujo:
    """Una cara 3D por triángulo en la capa de la triangulación (XDATA [cara, tipo])."""
    d = Dibujo()
    capa = d.capa(CAPAS_TIN[tipo])
    if hasattr(tin_o_caras, "triangulos"):
        caras: Sequence = tin_o_caras.puntos[tin_o_caras.triangulos].tolist()
    else:
        caras = tin_o_caras
    for c in caras:
        d.agregar(Cara3D(capa, vertices=tuple(tuple(float(v) for v in p[:3]) for p in c[:3]), datos={APP: [(1000, "cara"), (1000, tipo)]}))
    return d
