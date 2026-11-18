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

__all__ = ["Muestreador", "PuntoPerfil", "Perfil", "PIV", "Rasante", "perfil_terreno", "dibujar_perfil", "CAPAS_PERFIL", "HojaPerfil", "FILAS_GUITARRA"]

CAPAS_PERFIL = {
    "reticula": Capa("PERFIL_RETICULA", 8, descripcion="Retícula del perfil"),
    "terreno": Capa("PERFIL_TERRENO", 3, grosor_mm=0.35, descripcion="Terreno natural en el perfil"),
    "rasante": Capa("PERFIL_RASANTE", 1, grosor_mm=0.35, descripcion="Rasante de proyecto"),
    "textos": Capa("PERFIL_TEXTOS", 7, descripcion="Cotas, guitarra, curvas verticales y pendientes del perfil"),
    "curvas_v": Capa("PERFIL_CURVAS_VERTICALES", 6, descripcion="PCV, PIV y PTV y tangentes de las curvas verticales"),
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


FILAS_GUITARRA = {
    "estacion": "CADENAMIENTO",
    "terreno": "ELEV. TERRENO",
    "rasante": "ELEV. RASANTE",
    "corte": "ESPESOR CORTE",
    "terraplen": "ESPESOR TERRAPLÉN",
}


@dataclass
class HojaPerfil:
    """Cómo se dibuja el perfil (como el perfil con su guitarra de CivilCAD, con diseño propio).

    - `escala_h`, `escala_v`: denominadores (None: horizontal la del plano y vertical diez veces más grande);
    - `paso_estacion`, `paso_cota`: separación de la retícula en m (0: las estaciones del intervalo y una cota redonda, nunca a menos de
      5 mm en el papel);
    - `datum`: cota del plano de comparación, al pie de la retícula (None: la cota redonda bajo el punto más bajo);
    - `guitarra`: los renglones bajo el perfil, además del cadenamiento: «terreno», «rasante», «corte», «terraplen»;
    - `curvas_verticales`: PCV, PIV y PTV rotulados con estación y elevación, tangentes y longitud; `pendientes`: la de cada tangente.
    """

    escala_h: float | None = None
    escala_v: float | None = None
    paso_estacion: float = 0.0
    paso_cota: float = 0.0
    datum: float | None = None
    guitarra: tuple[str, ...] = ("terreno", "rasante", "corte", "terraplen")
    curvas_verticales: bool = True
    pendientes: bool = True

    def __post_init__(self):
        for e in (self.escala_h, self.escala_v):
            if e is not None and not 1 <= e <= 100000:
                raise ValueError("Las escalas del perfil van de 1:1 a 1:100 000.")
        if self.paso_estacion < 0 or self.paso_cota < 0:
            raise ValueError("La separación de la retícula no puede ser negativa.")
        malos = [g for g in self.guitarra if g not in FILAS_GUITARRA or g == "estacion"]
        if malos:
            raise ValueError(f"Renglón de la guitarra desconocido: {malos[0]}.")
        self.guitarra = tuple(self.guitarra)


def dibujar_perfil(
    perfil: Perfil,
    origen: tuple[float, float],
    escala: Escala | None = None,
    exageracion: float = 10.0,
    rasante: Rasante | None = None,
    estilo: str = "CIVIL",
    marcar_en_planta: bool = True,
    decimales: int = 2,
    hoja: HojaPerfil | None = None,
    guitarra: Sequence | None = None,
    opciones: dict | None = None,
) -> Dibujo:
    """Perfil con la esquina inferior izquierda de la retícula en `origen`.

    Retícula con líneas de cadenamiento y de cota rotuladas, el plano de comparación al pie; terreno y rasante en sus capas; curvas
    verticales y pendientes rotuladas; debajo la guitarra (cadenamiento, elevaciones de terreno y rasante, espesores de corte y de
    terraplén) en las estaciones de `guitarra` (`tvcivil.estaciones.Estacion`; por omisión las del intervalo del perfil). `exageracion`
    solo cuenta sin `hoja` (escala vertical = la del plano / exageración).
    """
    from .reticula import factor, opciones_xdata, paso_bonito, pasos_en

    esc = escala or Escala()
    E = float(esc.denominador)
    hoja = hoja or HojaPerfil(escala_v=E / exageracion)
    eh = hoja.escala_h or E
    ev = hoja.escala_v or E / 10.0
    fh, fv = factor(E, eh), factor(E, ev)
    d = Dibujo()
    for c in CAPAS_PERFIL.values():
        d.capa(c)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    h = esc.altura("cuadro_dato")
    e0, e1 = perfil.eje.inicio, perfil.eje.fin
    zs = [p.z for p in perfil.puntos if p.z is not None]
    if rasante:
        zs += [rasante.elevacion(e) for e in [e0, e1, *(p.estacion for p in rasante.pivs if e0 <= p.estacion <= e1)]]
    if not zs:
        raise ValueError("El eje no pasa por la triangulación.")
    zmin, zmax = min(zs), max(zs)
    pc = hoja.paso_cota or paso_bonito(zmax - zmin, 8, 5.0 * ev / 1000.0)
    if hoja.datum is not None:
        if hoja.datum > zmin + 1e-9:
            raise ValueError(f"El plano de comparación ({hoja.datum:.2f}) queda arriba del punto más bajo del perfil ({zmin:.2f}).")
        datum = float(hoja.datum)
    else:
        datum = math.floor(zmin / pc + 1e-9) * pc - pc
    tope = math.ceil(zmax / pc - 1e-9) * pc + pc
    if hoja.datum is not None:
        tope = datum + math.ceil((tope - datum) / pc - 1e-9) * pc
    x0, y0 = origen
    X = lambda e: x0 + (e - e0) * fh
    Y = lambda z: y0 + (z - datum) * fv
    ret, txt = CAPAS_PERFIL["reticula"].nombre, CAPAS_PERFIL["textos"].nombre
    # retícula: cotas cada `pc` (rotuladas a los dos lados; la de abajo es el plano de comparación) y cadenamientos cada `pe`
    cotas = [datum + k * pc for k in range(int(round((tope - datum) / pc)) + 1)]
    for k, z in enumerate(cotas):
        d.agregar(Linea(ret, inicio=(X(e0), Y(z), 0.0), fin=(X(e1), Y(z), 0.0)))
        valor = f"{z:.{decimales}f}"
        d.agregar(Texto(txt, valor=(f"P.C. {valor}" if k == 0 else valor), posicion=(X(e0) - 0.6 * h, Y(z) + (0.25 * h if k == 0 else 0.0)), altura=0.85 * h, estilo=estilo, alineacion_h="derecha", alineacion_v=("abajo" if k == 0 else "medio")))
        d.agregar(Texto(txt, valor=valor, posicion=(X(e1) + 0.6 * h, Y(z) + (0.25 * h if k == 0 else 0.0)), altura=0.85 * h, estilo=estilo, alineacion_h="izquierda", alineacion_v=("abajo" if k == 0 else "medio")))
    pe = hoja.paso_estacion or perfil.intervalo
    verticales = sorted({e0, e1, *pasos_en(e0, e1, pe)})
    for e in verticales:
        d.agregar(Linea(ret, inicio=(X(e), Y(datum), 0.0), fin=(X(e), Y(tope), 0.0)))
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
        _rotular_rasante(d, rasante, e0, e1, X, Y, fh, fv, h, estilo, decimales, hoja, perfil)
    # guitarra
    if guitarra is None:
        from .estaciones import Estacion

        guitarra = [Estacion(p.estacion, ("intervalo",)) for p in perfil.redondas]
    filas = ["estacion"] + [g for g in hoja.guitarra if g == "terreno" or rasante is not None]
    valores: list[list[str]] = []
    for st in guitarra:
        e = st.estacion
        zt = perfil.elevacion(e)
        zr = rasante.elevacion(e) if rasante else None
        esp = None if zt is None or zr is None else zt - zr
        fila = {"estacion": formato_estacion(e) + (f" {st.rotulo}" if getattr(st, "rotulo", "") else ""),
                "terreno": "—" if zt is None else f"{zt:.{decimales}f}",
                "rasante": "" if zr is None else f"{zr:.{decimales}f}",
                "corte": f"{esp:.{decimales}f}" if esp is not None and esp > 0.0005 else "",
                "terraplen": f"{-esp:.{decimales}f}" if esp is not None and esp < -0.0005 else ""}
        valores.append([fila[f] for f in filas])
    hv = 0.8 * h
    xs = _posiciones_guitarra(guitarra, X(e0), X(e1), hv, X)
    altos = [max(2.4 * h, max((_ancho(v[i], hv) for v in valores), default=0.0) + 1.4 * h) for i in range(len(filas))]
    nombres = [FILAS_GUITARRA[f] for f in filas]
    cabeza = max(_ancho(n, 0.85 * h) for n in nombres) * 1.2 + 2.0 * h
    y = Y(datum)
    d.agregar(Linea(ret, inicio=(X(e0) - cabeza, y, 0.0), fin=(X(e1), y, 0.0)))
    for i, nombre in enumerate(nombres):
        y_fila = y - altos[i]
        d.agregar(Linea(ret, inicio=(X(e0) - cabeza, y_fila, 0.0), fin=(X(e1), y_fila, 0.0)))
        d.agregar(Texto(txt, valor=nombre, posicion=(X(e0) - cabeza + 0.8 * h, y - altos[i] / 2), altura=0.85 * h, estilo=estilo, alineacion_h="izquierda", alineacion_v="medio"))
        for st, v, xp in zip(guitarra, valores, xs):
            if v[i] and xp is not None:
                d.agregar(Texto(txt, valor=v[i], posicion=(xp, y - altos[i] / 2), altura=hv, rotacion=90.0, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
        y = y_fila
    for xx in (X(e0) - cabeza, X(e0), X(e1)):
        d.agregar(Linea(ret, inicio=(xx, Y(datum), 0.0), fin=(xx, y, 0.0)))
    titulo = Texto(txt, valor=f"PERFIL   {formato_estacion(e0)} a {formato_estacion(e1)}   ESC. H 1:{eh:g}   V 1:{ev:g}",
                   posicion=(X(e0), Y(tope) + 2 * h), altura=esc.altura("cuadro_titulo"), estilo=estilo, alineacion_h="izquierda", alineacion_v="base",
                   datos={"TVCIVIL": [(1000, "perfil_titulo"), *opciones_xdata(opciones or {})]})
    d.agregar(titulo)
    if marcar_en_planta:
        hm = esc.altura("punto_numero")
        for p in perfil.redondas:
            x, yy, direc = perfil.eje.punto(p.estacion)
            nx, ny = math.sin(direc), -math.cos(direc)
            d.agregar(Linea(CAPAS_PERFIL["estaciones"].nombre, inicio=(x - nx * hm, yy - ny * hm, 0.0), fin=(x + nx * hm, yy + ny * hm, 0.0)))
            d.agregar(Texto(CAPAS_PERFIL["estaciones"].nombre, valor=formato_estacion(p.estacion, 0), posicion=(x + nx * hm * 1.5, yy + ny * hm * 1.5),
                            altura=hm, rotacion=math.degrees(direc) - 90.0, estilo=estilo, alineacion_h="izquierda", alineacion_v="medio"))
    return d


def _posiciones_guitarra(guitarra, xa: float, xb: float, hv: float, X) -> list[float | None]:
    """X de cada estación de la guitarra, o None si su rótulo se descarta. Los rótulos van girados 90°: cada uno ocupa `hv` de ancho.
    Se respeta una separación mínima de 4·hv (dos alturas de texto de aire entre uno y otro) entre rótulos; si dos chocan se queda el más importante (extremo del tramo, luego punto
    crítico o adicional, luego estación del intervalo) y el otro se descarta. Ningún rótulo cruza el marco: se meten una altura del marco."""
    margen = 1.0 * hv
    minimo = 4.0 * hv
    def prioridad(st):
        if st.estacion <= guitarra_e(guitarra, 0) + 1e-6 or st.estacion >= guitarra_e(guitarra, -1) - 1e-6:
            return 0
        return 1 if getattr(st, "rotulo", "") or "adicional" in getattr(st, "tipos", ()) else 2
    pos = [min(max(X(st.estacion), xa + margen), xb - margen) for st in guitarra]
    orden = sorted(range(len(guitarra)), key=lambda i: (prioridad(guitarra[i]), i))
    puestos: list[float] = []
    salida: list[float | None] = [None] * len(guitarra)
    for i in orden:
        if all(abs(pos[i] - q) >= minimo - 1e-9 for q in puestos):
            puestos.append(pos[i])
            salida[i] = pos[i]
    return salida


def guitarra_e(guitarra, k: int) -> float:
    return guitarra[k].estacion if guitarra else 0.0


def _ancho(texto: str, h: float) -> float:
    return len(texto) * 0.8 * h


def _rotular_rasante(d, r: Rasante, e0, e1, X, Y, fh, fv, h, estilo, decimales, hoja: HojaPerfil, perfil: Perfil | None = None) -> None:
    """PCV, PIV y PTV con estación y elevación (texto vertical sobre una línea de referencia), tangentes de las curvas verticales,
    su longitud, y la pendiente de cada tangente."""
    cv, txt = CAPAS_PERFIL["curvas_v"].nombre, CAPAS_PERFIL["textos"].nombre
    pivs = r.pivs
    if hoja.curvas_verticales:
        for k, p in enumerate(pivs):
            if not 0 < k < len(pivs) - 1 or not e0 - 1e-9 <= p.estacion <= e1 + 1e-9:
                continue
            marcas = [("PIV", p.estacion, p.elevacion)]
            if p.curva:
                g1, g2 = r.pendiente(k - 1), r.pendiente(k)
                a, b = p.estacion - p.curva / 2, p.estacion + p.curva / 2
                za, zb = p.elevacion - g1 * p.curva / 2, p.elevacion + g2 * p.curva / 2
                d.agregar(Polilinea(cv, vertices=[(X(a), Y(za)), (X(p.estacion), Y(p.elevacion)), (X(b), Y(zb))]))
                marcas = [("PCV", a, za)] + marcas + [("PTV", b, zb)]
            for nombre, e, z in marcas:
                extra = f"  CV L={p.curva:.2f}" if nombre == "PIV" and p.curva else ""
                if not e0 - 1e-9 <= e <= e1 + 1e-9:
                    continue
                zr = r.elevacion(e) if nombre != "PIV" else p.elevacion
                base = Y(max(zr, r.elevacion(e)))
                largo = 2.5 * h + _ancho(f"{nombre} {formato_estacion(e)}  {zr:.{decimales}f}{extra}", 0.75 * h)
                d.agregar(Linea(cv, inicio=(X(e), base, 0.0), fin=(X(e), base + largo, 0.0)))
                d.agregar(Texto(txt, valor=f"{nombre} {formato_estacion(e)}  {zr:.{decimales}f}{extra}", posicion=(X(e) - 0.3 * h, base + 2.2 * h), altura=0.75 * h, rotacion=90.0,
                                estilo=estilo, alineacion_h="izquierda", alineacion_v="abajo"))
    if hoja.pendientes:
        for k in range(len(pivs) - 1):
            a, b = pivs[k], pivs[k + 1]
            ini = a.estacion + a.curva / 2
            fin = b.estacion - b.curva / 2
            ini, fin = max(ini, e0), min(fin, e1)
            if fin - ini < 1e-6:
                continue
            g = r.pendiente(k)
            em = (ini + fin) / 2
            ang = math.degrees(math.atan2(g * fv, fh))
            texto = f"{g * 100:+.2f} %"
            if _ancho(texto, 0.8 * h) > (fin - ini) * fh:
                continue                                    # la tangente es más corta que su rótulo
            nx, ny = -math.sin(math.radians(ang)), math.cos(math.radians(ang))
            em, lado = _sitio_pendiente(r, perfil, ini, fin, em, ang, texto, X, Y, fh, h)
            d.agregar(Texto(txt, valor=texto, posicion=(X(em) + lado * nx * 0.6 * h, Y(r.elevacion(em)) + lado * ny * 0.6 * h), altura=0.8 * h, rotacion=ang, estilo=estilo,
                            alineacion_h="centro", alineacion_v=("abajo" if lado > 0 else "arriba")))


def _sitio_pendiente(r, perfil, ini, fin, em, ang, texto, X, Y, fh, h):
    """Donde poner la pendiente para que no pise el terreno: prueba el centro de la tangente y otros puntos, arriba y abajo de la
    rasante, y se queda con el primero que deja al terreno a más de 0.4·h del rótulo (si ninguno, el de mayor holgura)."""
    if perfil is None:
        return em, 1
    semi = _ancho(texto, 0.8 * h) / 2 / fh
    co, si = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    mejor = (-1e9, em, 1)
    for f in (0.5, 0.35, 0.65, 0.22, 0.78):
        c = ini + (fin - ini) * f
        if c - semi < ini - 1e-9 or c + semi > fin + 1e-9:
            c = min(max(c, ini + semi), fin - semi)
        for lado in (1, -1):
            holgura = 1e9
            for k in range(9):
                e = c - semi + 2 * semi * k / 8
                zt = perfil.elevacion(e)
                if zt is None:
                    continue
                yr = Y(r.elevacion(e))
                a, b = yr + lado * 0.6 * h / co, yr + lado * 1.4 * h / co   # banda que cubre el rótulo (en vertical)
                lo, hi = min(a, b), max(a, b)
                y = Y(zt)
                holgura = min(holgura, lo - y if y <= lo else (y - hi if y >= hi else -min(y - lo, hi - y)))
            if holgura >= 0.4 * h:
                return c, lado
            if holgura > mejor[0]:
                mejor = (holgura, c, lado)
    return mejor[1], mejor[2]


def _estaciones_rasante(r: Rasante, e0, e1) -> set[float]:
    s = set()
    for p in r.pivs:
        s.add(p.estacion)
        if p.curva:
            n = max(4, int(p.curva // 2))
            for k in range(n + 1):
                s.add(p.estacion - p.curva / 2 + p.curva * k / n)
    return {e for e in s if e0 <= e <= e1}


