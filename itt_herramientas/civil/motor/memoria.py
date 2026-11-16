"""Memoria descriptiva de un polígono y reporte de puntos: el contenido (títulos, párrafos y tablas), sin formato de archivo.

Herramientas los convierte en PDF o DOCX. La redacción es nuestra: se recorre el polígono desde su vértice inicial, lado por lado, con
rumbo, distancia y colindancia, y se cierra con la superficie en m² y en ha-a-ca.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .angulos import formato_dms
from .cuadro import Cuadro, OpcionesCuadro, filas_texto, resumen_geo
from .superficie import formato_ha_a_ca, formato_miles

__all__ = ["Documento", "memoria_descriptiva", "reporte_puntos"]


@dataclass
class Documento:
    titulo: str
    datos: list[tuple[str, str]] = field(default_factory=list)  # (etiqueta, valor) del encabezado
    parrafos: list[str] = field(default_factory=list)
    tabla_titulo: str = ""
    columnas: list[str] = field(default_factory=list)
    filas: list[list[str]] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {"titulo": self.titulo, "datos": [list(d) for d in self.datos], "parrafos": self.parrafos, "tabla_titulo": self.tabla_titulo,
                "columnas": self.columnas, "filas": self.filas, "notas": self.notas}


def _coords(y: str, x: str) -> str:
    return f"Y = {y}, X = {x}"


def memoria_descriptiva(cuadro: Cuadro, op: OpcionesCuadro | None = None, colindancias: Sequence[str] | None = None,
                        datos: Sequence[tuple[str, str]] = ()) -> Documento:
    """La memoria del polígono del cuadro. `colindancias`: una por lado, en el orden del cuadro (vacía = no se menciona)."""
    op = op or OpcionesCuadro()
    dd, dc = op.decimales_distancia, op.decimales_coordenadas
    m = lambda v, d=dd: formato_miles(v, d, op.separador_miles or ",")
    col = list(colindancias or []) + [""] * len(cuadro.lados)
    v0 = cuadro.vertice_inicial
    utm = " UTM" if op.formato != "normal" else ""
    zona = f" (zona {op.zona} {'norte' if op.hemisferio == 'N' else 'sur'}, elipsoide GRS80)" if op.formato != "normal" else ""
    parrafos = [f"El polígono se describe partiendo del vértice {v0[0]}, de coordenadas{utm} {_coords(m(v0[2], dc), m(v0[1], dc))}{zona}, "
                f"y recorriéndolo en sentido {cuadro.sentido}."]
    tramos = []
    for k, lado in enumerate(cuadro.lados):
        destino = "al vértice de partida" if k == len(cuadro.lados) - 1 else f"al vértice {lado.pv}"
        if lado.curva:
            cv = lado.curva
            t = (f"Del vértice {lado.est} {destino}, en curva {'a la izquierda' if cv.sentido == 'antihorario' else 'a la derecha'} de radio "
                 f"{m(cv.radio)} m y ángulo central de {formato_dms(cv.delta, op.decimales_segundos)}, con una longitud de curva de "
                 f"{m(cv.longitud)} m (cuerda de rumbo {lado.rumbo} y {m(lado.distancia)} m)")
        else:
            t = f"Del vértice {lado.est} {destino}, con rumbo {lado.rumbo} y una distancia de {m(lado.distancia)} m"
        if col[k].strip():
            t += f", colindando con {col[k].strip()}"
        tramos.append(t)
    parrafos += [t + ("." if k == len(tramos) - 1 else ";") for k, t in enumerate(tramos)]
    sup = formato_miles(cuadro.superficie, op.decimales_superficie, op.separador_miles or ",")
    cierre = (f"Así se cierra el polígono, con una superficie de {sup} m² ({formato_ha_a_ca(cuadro.superficie)}) y un perímetro de "
              f"{m(cuadro.perimetro)} m.")
    notas = []
    if op.formato == "utm_gps":
        g = resumen_geo(cuadro, op)
        cierre = cierre[:-1] + (f", medidos en la cuadrícula UTM; sobre el terreno (factor de escala medio {g['factor_medio']:.8f}) la superficie es de "
                                f"{formato_miles(g['superficie_topografica'], op.decimales_superficie, op.separador_miles or ',')} m² y el perímetro de "
                                f"{m(g['perimetro_topografico'])} m.")
    if op.formato != "normal":
        notas.append(f"Coordenadas en la proyección UTM, zona {op.zona} {'norte' if op.hemisferio == 'N' else 'sur'}, elipsoide GRS80 (ITRF2008).")
    parrafos.append(cierre)
    claves = ["est", "pv", "rumbo", "distancia", "v", "y", "x"] + (["lat", "lon"] if op.formato != "normal" else [])
    nombres = ["EST", "PV", "RUMBO", "DISTANCIA", "V", "Y", "X"] + (["LATITUD", "LONGITUD"] if op.formato != "normal" else [])
    filas = [[f.get(c, "") for c in claves] for f in filas_texto(cuadro, op)]
    return Documento("MEMORIA DESCRIPTIVA", [(a, b) for a, b in datos if str(b).strip()], parrafos, op.titulo or "CUADRO DE CONSTRUCCIÓN",
                     nombres, filas, notas)


def reporte_puntos(puntos: Sequence[Sequence], decimales: int = 3, zona: int | None = None, hemisferio: str = "N", decimales_geo: int = 4,
                   oeste: str = "W", titulo: str = "REPORTE DE PUNTOS", datos: Sequence[tuple[str, str]] = ()) -> Documento:
    """Tabla de puntos [x, y, z, número, descripción] ordenada por número, con latitud y longitud si se da la zona."""
    import re

    natural = lambda t: [(0, int(s), "") if s.isdigit() else (1, 0, s.lower()) for s in re.findall(r"\d+|\D+", str(t or ""))]
    lista = sorted(puntos, key=lambda p: natural(p[3] if len(p) > 3 else ""))
    columnas = ["PUNTO", "Y (NORTE)", "X (ESTE)", "ELEVACIÓN", "DESCRIPCIÓN"]
    if zona:
        from .geodesia import datos_geo, formato_latitud, formato_longitud

        columnas += ["LATITUD", "LONGITUD"]
    filas = []
    for p in lista:
        z = p[2] if len(p) > 2 else None
        fila = [str(p[3] if len(p) > 3 else ""), formato_miles(float(p[1]), decimales, ","), formato_miles(float(p[0]), decimales, ","),
                "" if z is None else formato_miles(float(z), decimales, ","), str(p[4] if len(p) > 4 and p[4] else "")]
        if zona:
            g = datos_geo(float(p[0]), float(p[1]), zona, hemisferio)
            fila += [formato_latitud(g.latitud, decimales_geo), formato_longitud(g.longitud, decimales_geo, oeste)]
        filas.append(fila)
    notas = [f"{len(filas):,} puntos.".replace(",", " ")]
    if zona:
        notas.append(f"Coordenadas UTM zona {zona} {'norte' if hemisferio == 'N' else 'sur'}, elipsoide GRS80 (ITRF2008).")
    return Documento(titulo, [(a, b) for a, b in datos if str(b).strip()], [], "", columnas, filas, notas)
