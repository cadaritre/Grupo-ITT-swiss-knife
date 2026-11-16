"""Perfil de terreno a lo largo de un eje, rasante con curvas verticales y su dibujo.

El terreno se muestrea **exactamente**: además de las estaciones redondas, el perfil lleva un
punto en cada cruce del eje con una arista del TIN, así entre dos puntos del perfil el terreno es
recto, igual que en el TIN. En los tramos curvos del eje el arco se parte en cuerdas de 1 m (el
error de flecha es menor a 1 mm con radios de más de 125 m).

Rasante: lista de PIV (estación, elevación, longitud de curva vertical). Las curvas verticales son
parábolas simétricas, como en el proyecto geométrico de caminos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .eje import Eje, formato_estacion
from .entidades import Capa, Dibujo, EstiloTexto, Linea, Polilinea, Texto
from .escala import Escala

__all__ = ["Muestreador", "PuntoPerfil", "Perfil", "PIV", "Rasante", "perfil_terreno", "dibujar_perfil", "CAPAS_PERFIL"]

CAPAS_PERFIL = {
    "reticula": Capa("PERFIL_RETICULA", 8, descripcion="Retícula del perfil"),
    "terreno": Capa("PERFIL_TERRENO", 3, grosor_mm=0.35, descripcion="Terreno natural en el perfil"),
    "rasante": Capa("PERFIL_RASANTE", 1, grosor_mm=0.35, descripcion="Rasante de proyecto"),
    "textos": Capa("PERFIL_TEXTOS", 7, descripcion="Estaciones, elevaciones y datos del perfil"),
    "estaciones": Capa("EJE_ESTACIONES", 7, descripcion="Marcas de estación sobre el eje en planta"),
}


class Muestreador:
    """Elevaciones del TIN sobre segmentos, con un punto en cada cruce con una arista."""

    def __init__(self, tin):
        import shapely

        self.tin = tin
        self.aristas = np.array(sorted(tin.aristas()), dtype=np.int64)
        p = tin.puntos
        self.p = p
        self.arbol = shapely.STRtree(shapely.linestrings(p[self.aristas][:, :, :2]))

    def segmento(self, a: Sequence[float], b: Sequence[float]) -> list[tuple[float, float | None]]:
        """[(t, z)] ordenado por t ∈ [0, 1]: los extremos y cada cruce con una arista del TIN."""
        import shapely

        ax, ay, bx, by = float(a[0]), float(a[1]), float(b[0]), float(b[1])
        salida = [(0.0, self.tin.elevacion(ax, ay)), (1.0, self.tin.elevacion(bx, by))]
        dx, dy = bx - ax, by - ay
        for j in self.arbol.query(shapely.LineString([(ax, ay), (bx, by)]), predicate="intersects"):
            i1, i2 = self.aristas[j]
            p1, p2 = self.p[i1], self.p[i2]
            ex, ey = p2[0] - p1[0], p2[1] - p1[1]
            den = dx * ey - dy * ex
            if abs(den) < 1e-15 * max(dx * dx + dy * dy, ex * ex + ey * ey):
                continue  # paralela (sobre la arista): los extremos ya dan la elevación
            qx, qy = p1[0] - ax, p1[1] - ay
            t = (qx * ey - qy * ex) / den
            u = (qx * dy - qy * dx) / den
            if -1e-12 <= t <= 1 + 1e-12 and -1e-12 <= u <= 1 + 1e-12:
                salida.append((min(1.0, max(0.0, t)), float(p1[2] + u * (p2[2] - p1[2]))))
        salida.sort()
        limpio: list[tuple[float, float | None]] = []
        for t, z in salida:
            if limpio and abs(t - limpio[-1][0]) < 1e-12:
                if limpio[-1][1] is None and z is not None:
                    limpio[-1] = (t, z)
                continue
            limpio.append((t, z))
        return limpio


@dataclass
class PuntoPerfil:
    estacion: float
    x: float
    y: float
    z: float | None  # None: fuera de la triangulación
    redonda: bool = False  # estación del intervalo (las que se rotulan)


@dataclass
class Perfil:
    puntos: list[PuntoPerfil]
    eje: Eje
    intervalo: float

    def elevacion(self, estacion: float) -> float | None:
        """Elevación del terreno interpolada en la estación (None fuera del TIN)."""
        pts = self.puntos
        if not pts or estacion < pts[0].estacion - 1e-9 or estacion > pts[-1].estacion + 1e-9:
            return None
        for a, b in zip(pts, pts[1:]):
            if a.estacion - 1e-9 <= estacion <= b.estacion + 1e-9:
                if a.z is None or b.z is None:
                    return a.z if abs(estacion - a.estacion) < 1e-9 else (b.z if abs(estacion - b.estacion) < 1e-9 else None)
                if b.estacion == a.estacion:
                    return a.z
                t = (estacion - a.estacion) / (b.estacion - a.estacion)
                return a.z + t * (b.z - a.z)
        return pts[-1].z

    @property
    def redondas(self) -> list[PuntoPerfil]:
        return [p for p in self.puntos if p.redonda]


def perfil_terreno(tin, eje: Eje, intervalo: float = 20.0, paso_arco: float = 1.0) -> Perfil:
    """Perfil del terreno a lo largo del eje: estaciones cada `intervalo` más todos los quiebres del TIN."""
    m = Muestreador(tin)
    redondas = set(eje.estaciones(intervalo, incluir_vertices=False))
    lineas = eje.densificar(paso_arco)
    puntos: dict[float, PuntoPerfil] = {}
    for (x1, y1, e1), (x2, y2, e2) in zip(lineas, lineas[1:]):
        for t, z in m.segmento((x1, y1), (x2, y2)):
            e = round(e1 + t * (e2 - e1), 6)
            if e not in puntos or (puntos[e].z is None and z is not None):
                puntos[e] = PuntoPerfil(e, x1 + t * (x2 - x1), y1 + t * (y2 - y1), z)
    for e in redondas:
        x, y, _ = eje.punto(e)
        clave = round(e, 6)
        if clave in puntos:
            puntos[clave].redonda = True
        else:
            puntos[clave] = PuntoPerfil(clave, x, y, None, True)
    lista = sorted(puntos.values(), key=lambda p: p.estacion)
    # las estaciones redondas toman la elevación interpolada entre los quiebres vecinos
    perfil = Perfil(lista, eje, intervalo)
    for p in lista:
        if p.z is None and p.redonda:
            p.z = tin.elevacion(p.x, p.y)
    return perfil


@dataclass
class PIV:
    estacion: float
    elevacion: float
    curva: float = 0.0  # longitud de la curva vertical (0 = sin curva)


@dataclass
class Rasante:
    pivs: list[PIV]

    def __post_init__(self):
        self.pivs = sorted(self.pivs, key=lambda p: p.estacion)
        if len(self.pivs) < 2:
            raise ValueError("La rasante necesita al menos dos puntos de inflexión.")
        for a, b in zip(self.pivs, self.pivs[1:]):
            if b.estacion <= a.estacion:
                raise ValueError("Dos PIV no pueden estar en la misma estación.")
        for k, p in enumerate(self.pivs):
            if p.curva < 0:
                raise ValueError("La longitud de curva no puede ser negativa.")
            if p.curva and (k == 0 or k == len(self.pivs) - 1):
                raise ValueError("Las curvas verticales van en PIV intermedios.")
            if p.curva:
                antes = p.estacion - self.pivs[k - 1].estacion
                despues = self.pivs[k + 1].estacion - p.estacion
                if p.curva / 2 > antes + 1e-9 or p.curva / 2 > despues + 1e-9:
                    raise ValueError(f"La curva vertical en {formato_estacion(p.estacion)} no cabe entre sus PIV vecinos.")

    def pendiente(self, k: int) -> float:
        """Pendiente (m/m) del tramo k → k+1."""
        a, b = self.pivs[k], self.pivs[k + 1]
        return (b.elevacion - a.elevacion) / (b.estacion - a.estacion)

    def elevacion(self, e: float) -> float:
        p = self.pivs
        if e <= p[0].estacion:
            return p[0].elevacion + self.pendiente(0) * (e - p[0].estacion)
        if e >= p[-1].estacion:
            return p[-1].elevacion + self.pendiente(len(p) - 2) * (e - p[-1].estacion)
        for k in range(1, len(p) - 1):
            v = p[k]
            if v.curva and v.estacion - v.curva / 2 <= e <= v.estacion + v.curva / 2:
                g1, g2 = self.pendiente(k - 1), self.pendiente(k)
                pcv = v.estacion - v.curva / 2
                zpcv = v.elevacion - g1 * v.curva / 2
                x = e - pcv
                return zpcv + g1 * x + (g2 - g1) / (2 * v.curva) * x * x
        for k in range(len(p) - 1):
            if p[k].estacion <= e <= p[k + 1].estacion:
                return p[k].elevacion + self.pendiente(k) * (e - p[k].estacion)
        return p[-1].elevacion


def dibujar_perfil(
    perfil: Perfil,
    origen: tuple[float, float],
    escala: Escala | None = None,
    exageracion: float = 10.0,
    rasante: Rasante | None = None,
    estilo: str = "CIVIL",
    marcar_en_planta: bool = True,
    decimales: int = 2,
) -> Dibujo:
    """Perfil dibujado con la esquina inferior izquierda de la retícula en `origen`.

    Horizontal a la escala del plano (1 m = 1 unidad); vertical exagerada `exageracion` veces. La
    retícula tiene líneas cada estación redonda y cada cota redonda; debajo, la tabla de
    elevaciones de terreno, de proyecto y el espesor (+ corte, − terraplén).
    """
    esc = escala or Escala()
    d = Dibujo()
    for c in CAPAS_PERFIL.values():
        d.capa(c)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    h = esc.altura("cuadro_dato")
    zs = [p.z for p in perfil.puntos if p.z is not None]
    if rasante:
        zs += [rasante.elevacion(p.estacion) for p in perfil.redondas]
    if not zs:
        raise ValueError("El eje no pasa por la triangulación.")
    zmin, zmax = min(zs), max(zs)
    paso_z = _paso_cota(zmax - zmin)
    datum = math.floor(zmin / paso_z) * paso_z - paso_z
    tope = math.ceil(zmax / paso_z) * paso_z + paso_z
    x0, y0 = origen
    e0, e1 = perfil.eje.inicio, perfil.eje.fin
    X = lambda e: x0 + (e - e0)
    Y = lambda z: y0 + (z - datum) * exageracion
    ret, txt = CAPAS_PERFIL["reticula"].nombre, CAPAS_PERFIL["textos"].nombre
    # retícula
    z = datum
    while z <= tope + 1e-9:
        d.agregar(Linea(ret, inicio=(X(e0), Y(z), 0.0), fin=(X(e1), Y(z), 0.0)))
        d.agregar(Texto(txt, valor=f"{z:.{decimales}f}", posicion=(X(e0) - h, Y(z)), altura=h, estilo=estilo, alineacion_h="derecha", alineacion_v="medio"))
        z += paso_z
    for p in perfil.redondas:
        d.agregar(Linea(ret, inicio=(X(p.estacion), Y(datum), 0.0), fin=(X(p.estacion), Y(tope), 0.0)))
    if abs((e1 - e0) % perfil.intervalo) > 1e-6:
        d.agregar(Linea(ret, inicio=(X(e1), Y(datum), 0.0), fin=(X(e1), Y(tope), 0.0)))
    # terreno (en tramos continuos)
    tramo: list[tuple[float, float]] = []
    for p in perfil.puntos + [PuntoPerfil(0, 0, 0, None)]:
        if p.z is None:
            if len(tramo) >= 2:
                d.agregar(Polilinea(CAPAS_PERFIL["terreno"].nombre, vertices=tramo, datos={"TVCIVIL": [(1000, "perfil_terreno")]}))
            tramo = []
        else:
            tramo.append((X(p.estacion), Y(p.z)))
    if rasante:
        pts = sorted({e0, e1, *(p.estacion for p in perfil.puntos)} | _estaciones_rasante(rasante, e0, e1))
        d.agregar(Polilinea(CAPAS_PERFIL["rasante"].nombre, vertices=[(X(e), Y(rasante.elevacion(e))) for e in pts], datos={"TVCIVIL": [(1000, "perfil_rasante")]}))
    # tabla bajo la retícula
    filas = ["ESTACIÓN", "ELEV. TERRENO"] + (["ELEV. PROYECTO", "ESPESOR"] if rasante else [])
    alto = h * 2.2
    for k, nombre in enumerate(filas):
        yy = Y(datum) - alto * (k + 1)
        d.agregar(Linea(ret, inicio=(X(e0) - 30 * h, yy, 0.0), fin=(X(e1), yy, 0.0)))
        d.agregar(Texto(txt, valor=nombre, posicion=(X(e0) - h, yy + alto / 2), altura=h, estilo=estilo, alineacion_h="derecha", alineacion_v="medio"))
    for p in perfil.redondas:
        valores = [formato_estacion(p.estacion), "—" if p.z is None else f"{p.z:.{decimales}f}"]
        if rasante:
            zr = rasante.elevacion(p.estacion)
            valores += [f"{zr:.{decimales}f}", "—" if p.z is None else f"{p.z - zr:+.{decimales}f}"]
        for k, v in enumerate(valores):
            yy = Y(datum) - alto * (k + 1) + alto / 2
            d.agregar(Texto(txt, valor=v, posicion=(X(p.estacion), yy), altura=h * 0.8, rotacion=90.0, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
    d.agregar(Texto(txt, valor=f"PERFIL  {formato_estacion(e0)} a {formato_estacion(e1)}   ESC. H 1:{esc.denominador}  V 1:{esc.denominador / exageracion:g}",
                    posicion=(X(e0), Y(tope) + 2 * h), altura=esc.altura("cuadro_titulo"), estilo=estilo, alineacion_h="izquierda", alineacion_v="base"))
    if marcar_en_planta:
        hv = esc.altura("punto_numero")
        for p in perfil.redondas:
            x, y, direc = perfil.eje.punto(p.estacion)
            nx, ny = math.sin(direc), -math.cos(direc)
            d.agregar(Linea(CAPAS_PERFIL["estaciones"].nombre, inicio=(x - nx * hv, y - ny * hv, 0.0), fin=(x + nx * hv, y + ny * hv, 0.0)))
            d.agregar(Texto(CAPAS_PERFIL["estaciones"].nombre, valor=formato_estacion(p.estacion, 0), posicion=(x + nx * hv * 1.5, y + ny * hv * 1.5),
                            altura=hv, rotacion=math.degrees(direc) - 90.0, estilo=estilo, alineacion_h="izquierda", alineacion_v="medio"))
    return d


def _estaciones_rasante(r: Rasante, e0, e1) -> set[float]:
    s = set()
    for p in r.pivs:
        s.add(p.estacion)
        if p.curva:
            n = max(4, int(p.curva // 2))
            for k in range(n + 1):
                s.add(p.estacion - p.curva / 2 + p.curva * k / n)
    return {e for e in s if e0 <= e <= e1}


def _paso_cota(rango_z: float) -> float:
    """Separación de cotas redondas (1, 2 o 5 × 10ⁿ m) para unas 4 a 10 líneas de retícula."""
    bruto = max(rango_z, 0.1) / 8.0
    base = 10 ** math.floor(math.log10(bruto))
    for f in (1, 2, 5, 10):
        if f * base >= bruto:
            return f * base
    return 10 * base
