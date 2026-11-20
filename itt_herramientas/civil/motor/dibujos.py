"""Dibujo de puntos (entidad POINT con rótulos TEXT) y de la triangulación (caras 3D) con las convenciones de Civil."""

from __future__ import annotations

from typing import Iterable, Sequence

from .entidades import Bloque, Capa, Cara3D, DefAtributo, Dibujo, Enlace, EstiloTexto, Insercion, Linea, PuntoCad, Texto
from .escala import ALTURAS_MM, Escala
from .puntos import PROYECTO, TERRENO, Punto

__all__ = ["CAPAS_PUNTOS", "CAPAS_TIN", "BLOQUES", "bloque_punto", "agregar_punto", "dibujar_puntos", "dibujar_tin", "APP"]

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
    """Bloque del punto del modelo anterior (solo para leer y convertir dibujos viejos y para las pruebas) dibujado en mm de papel (se inserta a escala E/1000): símbolo en cruz con círculo y tres atributos.

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


ROLES = {"NUM": "num", "ELEV": "elev", "DESC": "desc"}
CAPA_DE_ROL = {"num": "numero", "elev": "elevacion", "desc": "descripcion"}
PDMODE = 34  # cruz con círculo: el símbolo de siempre, ahora como estilo de punto del dibujo


def tamano_marcador(escala: Escala) -> float:
    """PDSIZE: el símbolo mide lo mismo en el papel a cualquier escala (unidades de dibujo)."""
    return escala.altura("punto_simbolo")


def variables_de_puntos(escala: Escala) -> dict[str, float]:
    return {"PDMODE": PDMODE, "PDSIZE": tamano_marcador(escala)}


def posiciones_rotulos(escala: Escala) -> dict[str, tuple[float, float, float]]:
    """(dx, dy, altura) de cada rótulo respecto del punto, en unidades de dibujo: número arriba a la derecha, elevación a la derecha y
    descripción abajo a la derecha (las mismas posiciones que tenían los atributos del bloque)."""
    f = escala.altura("punto_simbolo") / escala.alturas_mm["punto_simbolo"]  # unidades de dibujo por mm de papel
    x = escala.alturas_mm["punto_simbolo"] / 2 * 1.6 * f
    hn, he, hd = (escala.alturas_mm[k] for k in ("punto_numero", "punto_elevacion", "punto_descripcion"))
    return {
        "num": (x, he * 0.9 * f, hn * f),
        "elev": (x, -he * 0.45 * f, he * f),
        "desc": (x, (-he * 0.45 - hd * 1.5) * f, hd * f),
    }


def agregar_punto(d: Dibujo, escala: Escala, tipo: str, x: float, y: float, z: float, numero: str, elevacion: str, descripcion: str, estilo: str = "CIVIL",
                  rotulos: dict | None = None, ver_numero: bool = True) -> int:
    """Un punto: la entidad POINT en (x, y, z) y un TEXT por cada rótulo con valor (número, elevación, descripción).

    XDATA del punto: [punto, número, tipo]. XDATA de cada rótulo: [rotulo, número, tipo, rol, enlace al punto].
    `rotulos` (opcional) trae, por rol («num», «elev», «desc»), la posición, la altura y la rotación que ya tenía el rótulo
    ({"posicion": (x, y), "altura": h, "rotacion": grados}); lo que no trae sale en su lugar de siempre. El número del punto va siempre en
    el XDATA; con `ver_numero` en falso no se dibuja su rótulo. Devuelve el índice del punto.
    """
    if tipo not in CAPAS_PUNTOS or tipo not in BLOQUES:
        raise ValueError(f"Tipo de punto desconocido: {tipo}")
    d.capa(CAPAS_PUNTOS[tipo])
    d.variables.update(variables_de_puntos(escala))
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    indice = len(d.entidades)
    d.agregar(PuntoCad(CAPAS_PUNTOS[tipo].nombre, posicion=(float(x), float(y), float(z)), datos={APP: [(1000, "punto"), (1000, numero), (1000, tipo)]}))
    pos = posiciones_rotulos(escala)
    valores = {"num": numero if ver_numero else "", "elev": elevacion, "desc": descripcion}
    for rol, valor in valores.items():
        if not valor:
            continue
        dx, dy, h = pos[rol]
        capa = CAPAS_PUNTOS[CAPA_DE_ROL[rol]]
        d.capa(capa)
        px, py, rot = float(x) + dx, float(y) + dy, 0.0
        if rotulos and rol in rotulos:
            r = rotulos[rol]
            if r.get("posicion"):
                px, py = float(r["posicion"][0]), float(r["posicion"][1])
            if r.get("altura") and r["altura"] > 0:
                h = float(r["altura"])
            rot = float(r.get("rotacion") or 0.0)
        d.agregar(Texto(capa.nombre, valor=str(valor), posicion=(px, py), altura=h, rotacion=rot, estilo=estilo, elevacion=float(z),
                        datos={APP: [(1000, "rotulo"), (1000, numero), (1000, tipo), (1000, rol), (1005, Enlace(indice))]}))
    return indice


def dibujar_puntos(
    puntos: Iterable[Punto],
    escala: Escala | None = None,
    decimales_z: int = 3,
    numero: bool = True,
    elevacion: bool = True,
    descripcion: bool = True,
    estilo: str = "CIVIL",
) -> Dibujo:
    """Un POINT por punto, en su X, Y, Z, y un TEXT por rótulo (número, elevación, descripción), a la escala del plano.

    El marcador lo da el dibujo (PDMODE 34, PDSIZE por la escala), no un bloque. Un rótulo apagado no se dibuja.
    """
    esc = escala or Escala()
    d = Dibujo()
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    d.variables.update(variables_de_puntos(esc))
    for k in ("numero", "elevacion", "descripcion"):
        d.capa(CAPAS_PUNTOS[k])
    for p in puntos:
        if p.tipo not in BLOQUES:
            raise ValueError(f"Tipo de punto desconocido: {p.tipo}")
        z = 0.0 if p.z is None else float(p.z)
        agregar_punto(d, esc, p.tipo, p.x, p.y, z, p.numero, (f"{p.z:.{decimales_z}f}" if p.z is not None else "") if elevacion else "",
                      p.descripcion if descripcion else "", estilo, ver_numero=numero)
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
