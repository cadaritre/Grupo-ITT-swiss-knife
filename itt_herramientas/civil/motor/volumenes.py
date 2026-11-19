"""Volúmenes: por secciones (áreas medias) y entre dos superficies (exacto, por superposición de TIN).

Convención: **corte** es material que se quita (el terreno está arriba del proyecto) y
**terraplén** el que se pone (el terreno está abajo).

Entre superficies: cada par de triángulos que se traslapan en planta se recorta (polígono convexo);
dentro del recorte la diferencia d = z_terreno − z_proyecto es lineal, así que se parte por la
línea d = 0 y la integral de cada parte es su área por d en su centroide. El resultado es exacto
para los dos TIN tal como son.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .eje import formato_estacion
from .entidades import Capa, Dibujo, EstiloTexto, Linea, Texto
from .escala import Escala
from .secciones import Seccion

__all__ = ["RenglonVolumen", "volumenes_por_secciones", "ResultadoSuperficies", "volumen_entre_superficies", "volumen_por_rejilla", "dibujar_tabla_volumenes", "csv_volumenes", "CAPAS_VOLUMEN"]

CAPAS_VOLUMEN = {
    "linea_cero": Capa("VOLUMEN_LINEA_CERO", 6, grosor_mm=0.35, descripcion="Línea de ceros entre superficies"),
    "tabla": Capa("VOLUMEN_TABLA", 7, descripcion="Tabla de volúmenes"),
}


@dataclass
class RenglonVolumen:
    estacion: float
    area_corte: float
    area_terraplen: float
    vol_corte: float  # del tramo anterior a esta estación
    vol_terraplen: float
    acum_corte: float
    acum_terraplen: float
    ordenada: float  # curva masa: corte·abundamiento − terraplén, acumulado
    vol_despalme: float = 0.0  # del tramo anterior (plantilla de carretera); ya va sumado al terraplén donde está bajo él
    acum_despalme: float = 0.0


def volumenes_por_secciones(lista: Sequence[Seccion], abundamiento: float = 1.0) -> list[RenglonVolumen]:
    """Método de áreas medias: V = (A₁ + A₂) / 2 · distancia, para corte y terraplén."""
    if abundamiento <= 0:
        raise ValueError("El coeficiente de abundamiento debe ser positivo.")
    secs = sorted(lista, key=lambda s: s.estacion)
    salida: list[RenglonVolumen] = []
    ac = at = om = ad = 0.0
    previo = None
    for s in secs:
        vc = vt = vd = 0.0
        if previo is not None:
            dist = s.estacion - previo.estacion
            vc = (previo.corte + s.corte) / 2 * dist
            vt = (previo.terraplen + s.terraplen) / 2 * dist
            vd = (previo.despalme + s.despalme) / 2 * dist
        ac += vc
        at += vt
        ad += vd
        om += vc * abundamiento - vt
        salida.append(RenglonVolumen(s.estacion, s.corte, s.terraplen, vc, vt, ac, at, om, vd, ad))
        previo = s
    return salida


def _recortar(poligono: list[tuple[float, float, float]], a, b, c, conservar_izquierda=True):
    """Sutherland–Hodgman de un polígono (con d en la 3.ª coordenada) contra la recta a·x + b·y + c ≥ 0."""
    salida = []
    n = len(poligono)
    for i in range(n):
        p, q = poligono[i], poligono[(i + 1) % n]
        fp = a * p[0] + b * p[1] + c
        fq = a * q[0] + b * q[1] + c
        if not conservar_izquierda:
            fp, fq = -fp, -fq
        if fp >= 0:
            salida.append(p)
        if (fp >= 0) != (fq >= 0):
            t = fp / (fp - fq)
            salida.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]), p[2] + t * (q[2] - p[2])))
    return salida


def _area_centroide_d(poli):
    """(área, d en el centroide) de un polígono (x, y, d) con d lineal."""
    if len(poli) < 3:
        return 0.0, 0.0, 0.0
    x0, y0 = poli[0][0], poli[0][1]
    area = cx = cy = 0.0
    for i in range(len(poli)):
        x1, y1 = poli[i][0] - x0, poli[i][1] - y0
        x2, y2 = poli[(i + 1) % len(poli)][0] - x0, poli[(i + 1) % len(poli)][1] - y0
        cr = x1 * y2 - x2 * y1
        area += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    if abs(area) < 1e-14:
        return 0.0, 0.0, 0.0
    cx, cy = cx / (3 * area) + x0, cy / (3 * area) + y0
    area /= 2
    return abs(area), cx, cy


@dataclass
class ResultadoSuperficies:
    corte: float
    terraplen: float
    area_comun: float
    linea_cero: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)

    @property
    def neto(self) -> float:
        return self.corte - self.terraplen


def _plano(tri):
    """Coeficientes de z = A·x + B·y + C de un triángulo (n, 3, 3) → (A, B, C)."""
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
    det = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
    A = ((z2 - z1) * (y3 - y1) - (z3 - z1) * (y2 - y1)) / det
    B = ((x2 - x1) * (z3 - z1) - (x3 - x1) * (z2 - z1)) / det
    return A, B, z1 - A * x1 - B * y1


def volumen_entre_superficies(terreno, proyecto) -> ResultadoSuperficies:
    """Volumen exacto entre dos TIN (`Tin` o caras (m, 3, 3)) en su zona común."""
    import shapely

    ta = _caras(terreno)
    tb = _caras(proyecto)
    origen = np.vstack([ta.reshape(-1, 3), tb.reshape(-1, 3)])[:, :2].mean(axis=0)
    ta = ta.copy(); ta[:, :, :2] -= origen
    tb = tb.copy(); tb[:, :, :2] -= origen
    arbol = shapely.STRtree(shapely.polygons(tb[:, :, :2]))
    pares_a, pares_b = arbol.query(shapely.polygons(ta[:, :, :2]), predicate="intersects")
    planos_a = [_plano(t) for t in ta]
    planos_b = [_plano(t) for t in tb]
    corte = terraplen = area = 0.0
    ceros = []
    for i, j in zip(pares_a.tolist(), pares_b.tolist()):
        A1, B1, C1 = planos_a[i]
        A2, B2, C2 = planos_b[j]
        dA, dB, dC = A1 - A2, B1 - B2, C1 - C2
        poli = [(p[0], p[1], dA * p[0] + dB * p[1] + dC) for p in _ccw(ta[i])]
        bb = _ccw_xy(tb[j])
        for k in range(3):
            p, q = bb[k], bb[(k + 1) % 3]
            # semiplano a la izquierda de p→q (triángulo antihorario)
            a, b, c = -(q[1] - p[1]), (q[0] - p[0]), (q[1] - p[1]) * p[0] - (q[0] - p[0]) * p[1]
            poli = _recortar(poli, a, b, c)
            if len(poli) < 3:
                break
        if len(poli) < 3:
            continue
        r = _area_centroide_d(poli)
        if r[0] <= 1e-12:
            continue
        area += r[0]
        mas = _recortar(poli, dA, dB, dC, True)
        menos = _recortar(poli, dA, dB, dC, False)
        for parte, signo in ((mas, 1), (menos, -1)):
            rr = _area_centroide_d(parte)
            if rr[0] > 0:
                v = rr[0] * (dA * rr[1] + dB * rr[2] + dC)
                if signo > 0:
                    corte += max(v, 0.0)
                else:
                    terraplen += max(-v, 0.0)
        if len(mas) >= 3 and len(menos) >= 3:
            # los vértices nuevos del recorte por d = 0 son el tramo de la línea de ceros dentro de este traslape
            sobre = [(p[0] + origen[0], p[1] + origen[1]) for p in mas if abs(dA * p[0] + dB * p[1] + dC) <= 1e-9 * (1 + abs(dA) + abs(dB) + abs(dC))]
            if len(sobre) >= 2:
                ceros.append((sobre[0], sobre[-1]))
    return ResultadoSuperficies(corte, terraplen, area, ceros)


def _ccw(t):
    pts = [tuple(map(float, p)) for p in t]
    (x1, y1, _), (x2, y2, _), (x3, y3, _) = pts
    if (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1) < 0:
        pts = [pts[0], pts[2], pts[1]]
    return pts


def _ccw_xy(t):
    return [(p[0], p[1]) for p in _ccw(t)]


def _caras(x) -> np.ndarray:
    if hasattr(x, "triangulos"):
        return x.puntos[x.triangulos].astype(float)
    a = np.asarray(x, dtype=float)
    return a[:, :3, :3]


def volumen_por_rejilla(terreno, proyecto, paso: float) -> tuple[float, float]:
    """Control por rejilla: (corte, terraplén) con celdas de `paso` × `paso` evaluadas en su centro."""
    xs = np.vstack([_caras(terreno).reshape(-1, 3), _caras(proyecto).reshape(-1, 3)])
    x0, y0 = xs[:, 0].min(), xs[:, 1].min()
    x1, y1 = xs[:, 0].max(), xs[:, 1].max()
    corte = terraplen = 0.0
    ta = terreno if hasattr(terreno, "elevacion") else None
    tb = proyecto if hasattr(proyecto, "elevacion") else None
    if ta is None or tb is None:
        raise ValueError("La rejilla de control necesita dos TIN.")
    for x in np.arange(x0 + paso / 2, x1, paso):
        for y in np.arange(y0 + paso / 2, y1, paso):
            za, zb = ta.elevacion(x, y), tb.elevacion(x, y)
            if za is None or zb is None:
                continue
            d = za - zb
            if d > 0:
                corte += d * paso * paso
            else:
                terraplen -= d * paso * paso
    return corte, terraplen


def _columnas_volumen(renglones):
    con_despalme = any(r.vol_despalme for r in renglones)
    titulos = ["ESTACIÓN", "ÁREA CORTE", "ÁREA TERRAPLÉN", "VOL. CORTE", "VOL. TERRAPLÉN", "ACUM. CORTE", "ACUM. TERRAPLÉN", "ORDENADA"]
    if con_despalme:
        titulos[3:3] = ["VOL. DESPALME"]

    def valores(r):
        v = [r.area_corte, r.area_terraplen, r.vol_corte, r.vol_terraplen, r.acum_corte, r.acum_terraplen, r.ordenada]
        if con_despalme:
            v[2:2] = [r.vol_despalme]
        return v

    def totales():
        t = ["", "", sum(r.vol_corte for r in renglones), sum(r.vol_terraplen for r in renglones), "", "", ""]
        if con_despalme:
            t[2:2] = [sum(r.vol_despalme for r in renglones)]
        return t

    return con_despalme, titulos, valores, totales


def csv_volumenes(renglones: Sequence[RenglonVolumen], rotulos: dict[float, str] | None = None, decimales: int = 3) -> str:
    """La tabla de volúmenes como CSV (coma, punto decimal, UTF-8 con BOM para que Excel lea los acentos), con su renglón de totales."""
    import csv
    import io

    rotulos = {round(k, 3): v for k, v in (rotulos or {}).items()}
    _, titulos, valores, totales = _columnas_volumen(renglones)
    salida = io.StringIO()
    w = csv.writer(salida, lineterminator="\r\n")
    w.writerow(["ESTACIÓN", "PUNTO", *titulos[1:]])
    for r in renglones:
        w.writerow([formato_estacion(r.estacion), rotulos.get(round(r.estacion, 3), ""), *(f"{v:.{decimales}f}" for v in valores(r))])
    w.writerow(["TOTALES", "", *(f"{v:.{decimales}f}" if isinstance(v, float) else v for v in totales())])
    return "\ufeff" + salida.getvalue()


def dibujar_tabla_volumenes(renglones: Sequence[RenglonVolumen], esquina: tuple[float, float], escala: Escala | None = None, estilo: str = "CIVIL", decimales: int = 2,
                            rotulos: dict[float, str] | None = None, titulo: str = "") -> Dibujo:
    """Tabla de volúmenes por estaciones (áreas medias) con la ordenada de curva masa y el renglón de totales; `rotulos`: el tipo de las
    estaciones críticas («PC», «PIV»…), que se escribe junto al cadenamiento."""
    esc = escala or Escala()
    d = Dibujo()
    for c in CAPAS_VOLUMEN.values():
        d.capa(c)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    f = float(esc.denominador) / 1000.0
    h = esc.altura("cuadro_dato")
    rotulos = {round(k, 3): v for k, v in (rotulos or {}).items()}
    con_despalme, titulos, valores, totales = _columnas_volumen(renglones)
    largo_est = max([len(formato_estacion(r.estacion) + (" " + rotulos[round(r.estacion, 3)] if round(r.estacion, 3) in rotulos else "")) for r in renglones] + [8])
    anchos = [max(22, largo_est * 2.0 + 4), 20, 24, 20, 24, 22, 26, 22]
    if con_despalme:
        anchos[3:3] = [24]
    xs = [esquina[0]]
    for a in anchos:
        xs.append(xs[-1] + a * f)
    alto = 2 * h
    capa = CAPAS_VOLUMEN["tabla"].nombre
    def estacion(r):
        rot = rotulos.get(round(r.estacion, 3), "")
        return formato_estacion(r.estacion) + (f" {rot}" if rot else "")
    filas = [titulos] + [[estacion(r), *(f"{v:,.{decimales}f}" for v in valores(r))] for r in renglones]
    filas.append(["TOTALES", *(f"{v:,.{decimales}f}" if isinstance(v, float) else v for v in totales())])
    y = esquina[1]
    if titulo:
        d.agregar(Texto(capa, valor=titulo, posicion=(xs[0], y + 0.8 * h), altura=esc.altura("cuadro_titulo"), estilo=estilo, alineacion_h="izquierda", alineacion_v="base"))
    for k, fila in enumerate(filas):
        d.agregar(Linea(capa, inicio=(xs[0], y, 0.0), fin=(xs[-1], y, 0.0)))
        for c, v in enumerate(fila):
            d.agregar(Texto(capa, valor=v, posicion=((xs[c] + xs[c + 1]) / 2, y - alto / 2), altura=h if k else h * 0.9, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
        y -= alto
    d.agregar(Linea(capa, inicio=(xs[0], y, 0.0), fin=(xs[-1], y, 0.0)))
    for x in xs:
        d.agregar(Linea(capa, inicio=(x, esquina[1], 0.0), fin=(x, y, 0.0)))
    return d
