"""Sobreelevación y ampliación en las curvas horizontales del eje, según el Manual de Proyecto Geométrico de Carreteras de la SCT
(Dirección General de Servicios Técnicos, tercera edición, julio de 2018), capítulo III:

- H.1 Sobreelevación: Smáx = 10 % en carreteras (8 % con heladas o nevadas frecuentes, 6 % en vialidades urbanas). Para un grado G:
  línea a S = (Smáx/Gmáx)·G; línea b S = G·Vp²/146 000 (hasta Smáx); la de proyecto es la línea c, una parábola asimétrica entre
  ellas (figura III.16), tangente a la línea b en G = 0 y horizontal en Gmáx.
- H.2 Gmáx = 146 000 (μ + Smáx) / Vp², con μ de la tabla III.3 (pavimento mojado).
- H.3 Longitud mínima de la transición: Le = 8·Vp·S (m). Para cuatro carriles el doble y para seis el triple (capítulo II, C.2).
- Figura III.17: en una curva circular simple, de la transición, de 0.5 Le a 0.7 Le va en la tangente y el resto dentro de la curva; la
  sobreelevación completa debe durar al menos Lc/3. Antes de la transición se quita el bombeo del lado exterior (de −b a 0) con la
  misma pendiente de orilla: N = b·Le/S.
- H.4 a H.6 Ampliación: acc = N (U + C) + (N − 1) FA + Z, con U = u + R − √(R² − ΣLi²), C = (act − n·Ev)/n, FA = √(R² + A(2L + A)) − R
  y Z = 0.1 V/√R; ac = acc − act. Menos de 0.50 m no se amplía; lo demás se redondea al decímetro superior. La ampliación se da del
  lado interior de la curva y se desarrolla junto con la sobreelevación.

El grado es el de un arco de 20 m: G = 1145.92 / R.

Vehículos de proyecto: las tablas III.4 a III.7 del manual tabulan acc para dos carriles de 3.50 m (III.4 y III.6 impresas como
imagen, transcritas a mano en `pruebas/datos/tablas_sct.py`). Las cuatro se reproducen con estas fórmulas y los coeficientes de
`VEHICULOS` (ΣLi² y A(2L + A), calibrados contra cada tabla: todas las celdas a ±0.005 m, el redondeo de la tabla). Para otro vehículo
se dan sus medidas.

Con espirales (figura III.17, curva con espirales): la transición de la sobreelevación y la ampliación se hacen a lo largo de la espiral,
del TE al EC (y del CE al ET); la sobreelevación es completa en toda la curva circular; antes del TE se quita el bombeo exterior. Si la
espiral es más corta que 8·Vp·S se avisa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .eje import Eje, formato_estacion

__all__ = ["MU_SCT", "VEHICULOS", "Vehiculo", "ParametrosSCT", "CurvaSCT", "grado", "gmax", "sobreelevacion_sct", "longitud_transicion",
           "ancho_en_curva", "ampliacion_sct", "Peraltes"]

#: Tabla III.3: coeficientes de fricción transversal para proyecto (pavimento mojado), por velocidad de proyecto en km/h.
MU_SCT = {30: 0.28, 40: 0.23, 50: 0.19, 60: 0.17, 70: 0.15, 80: 0.14, 90: 0.13, 100: 0.12, 110: 0.11}


@dataclass(frozen=True)
class Vehiculo:
    nombre: str
    u: float  # entrevía en tangente, m (tabla III.2: 2.60 m)
    ev: float  # entrevía para la distancia libre lateral C, m (2.44 a 2.60)
    suma_li2: float  # ΣLi², m²
    q_vuelo: float  # A (2L + A), m²


VEHICULOS = {
    "T3S2R4": Vehiculo("T3S2R4 de 31.00 m (caminos tipo ET y A, DE-2836)", 2.60, 2.59, 222.77, 15.56),
    "C3R3": Vehiculo("C3R3 de 28.50 m (camino tipo B, DE-2434)", 2.60, 2.59, 134.89, 24.11),
    "T3S3": Vehiculo("T3S3 de 18.50 m (camino tipo C, DE-1594)", 2.60, 2.59, 131.0, 23.8),
    "C3": Vehiculo("C3 de 12.50 m (camino tipo D, DE-790)", 2.60, 2.59, 62.67, 20.79),
}


def grado(radio: float) -> float:
    return 1145.92 / radio


def _mu(velocidad: float) -> float:
    if velocidad in MU_SCT:
        return MU_SCT[velocidad]
    ks = sorted(MU_SCT)
    if not ks[0] <= velocidad <= ks[-1]:
        raise ValueError("La velocidad de proyecto va de 30 a 110 km/h.")
    for a, b in zip(ks, ks[1:]):
        if a <= velocidad <= b:
            return MU_SCT[a] + (velocidad - a) / (b - a) * (MU_SCT[b] - MU_SCT[a])
    return MU_SCT[ks[-1]]


def gmax(velocidad: float, smax: float = 0.10) -> float:
    """Grado máximo de curvatura (H.2)."""
    return 146000.0 * (_mu(velocidad) + smax) / velocidad ** 2


def sobreelevacion_sct(g: float, velocidad: float, smax: float = 0.10) -> float:
    """Sobreelevación de proyecto (línea c de la figura III.16) para el grado g, en m/m."""
    if g < 0:
        raise ValueError("El grado de curvatura no puede ser negativo.")
    g_mu = gmax(velocidad, smax)
    if g > g_mu * (1 + 1e-9):
        raise ValueError(f"Una curva de {g:.2f}° pasa del grado máximo ({g_mu:.2f}°) para {velocidad:g} km/h.")
    g_b = 146000.0 * smax / velocidad ** 2                   # donde la línea b llega a Smáx
    l1, l2 = g_b, g_mu - g_b
    p1 = smax / g_b
    e = p1 * l1 * l2 / (2.0 * (l1 + l2))                     # ordenada de la parábola asimétrica en su vértice
    if g <= l1:
        return p1 * g - e * (g / l1) ** 2
    return smax - e * ((g_mu - g) / l2) ** 2


def longitud_transicion(velocidad: float, s: float, carriles: int = 2) -> float:
    """Le = 8·Vp·S (H.3), por dos carriles: el doble con cuatro y el triple con seis."""
    return 8.0 * velocidad * s * max(1.0, carriles / 2.0)


def ancho_en_curva(radio: float, velocidad: float, carriles: int, ancho_tangente: float, vehiculo: Vehiculo) -> float:
    """acc (H.4 y H.5)."""
    if radio * radio <= vehiculo.suma_li2:
        raise ValueError("El radio es demasiado chico para el vehículo de proyecto.")
    u_curva = vehiculo.u + radio - math.sqrt(radio * radio - vehiculo.suma_li2)
    c = (ancho_tangente - carriles * vehiculo.ev) / carriles
    fa = math.sqrt(radio * radio + vehiculo.q_vuelo) - radio
    z = 0.1 * velocidad / math.sqrt(radio)
    return carriles * (u_curva + c) + (carriles - 1) * fa + z


def ampliacion_sct(radio: float, velocidad: float, carriles: int, ancho_tangente: float, vehiculo: Vehiculo) -> float:
    """ac = acc − act (H.5), con la regla de H.6: menos de 0.50 m no se amplía; lo demás, al decímetro superior.

    Con cuatro carriles o más (carretera no dividida) la ampliación es el doble de la de dos carriles (cap. III, después de las tablas
    III.4 a III.7, página 129), y el mínimo de 0.50 m se revisa ya duplicada."""
    if carriles >= 4:
        dos = ancho_tangente * 2.0 / carriles
        ac = 2.0 * (ancho_en_curva(radio, velocidad, 2, dos, vehiculo) - dos)
    else:
        ac = ancho_en_curva(radio, velocidad, carriles, ancho_tangente, vehiculo) - ancho_tangente
    if ac < 0.5:
        return 0.0
    return math.ceil(round(ac * 10.0, 6)) / 10.0


@dataclass
class ParametrosSCT:
    velocidad: float = 60.0  # km/h
    smax: float = 0.10
    vehiculo: str | Vehiculo = "C3"
    reparto_tangente: float = 0.6  # fracción de Le en la tangente (0.5 a 0.7)
    ampliar: bool = True

    def __post_init__(self):
        if not 0.5 <= self.reparto_tangente <= 0.7:
            raise ValueError("De la transición, entre la mitad y el 70 % va en la tangente.")
        if not 0.02 <= self.smax <= 0.12:
            raise ValueError("La sobreelevación máxima va de 2 % a 12 %.")
        _mu(self.velocidad)

    @property
    def vehiculo_de_proyecto(self) -> Vehiculo:
        if isinstance(self.vehiculo, Vehiculo):
            return self.vehiculo
        if self.vehiculo not in VEHICULOS:
            raise ValueError("Elige el vehículo de proyecto.")
        return VEHICULOS[self.vehiculo]


@dataclass
class CurvaSCT:
    pc: float  # estación de inicio de la curva
    pt: float
    radio: float
    grado: float
    izquierda: bool  # la curva gira a la izquierda (su interior es el lado izquierdo)
    sobreelevacion: float
    transicion: float  # Le
    n: float  # tramo para quitar el bombeo exterior
    ampliacion: float
    inicio: float  # donde empieza a cambiar la sección (antes del PC)
    completa_desde: float
    completa_hasta: float
    fin: float


class Peraltes:
    """Pendientes transversales y ampliación en cada estación del eje, curva por curva."""

    def __init__(self, eje: Eje, params: ParametrosSCT, bombeo: float, carriles: int, ancho_tangente: float):
        self.params = params
        self.bombeo = abs(bombeo)
        self.curvas: list[CurvaSCT] = []
        self.avisos: list[str] = []
        veh = params.vehiculo_de_proyecto
        for ce in eje.curvas:
            g = grado(ce.radio)
            s = sobreelevacion_sct(g, params.velocidad, params.smax)
            le_min = longitud_transicion(params.velocidad, s, carriles)
            amp = ampliacion_sct(ce.radio, params.velocidad, carriles, ancho_tangente, veh) if params.ampliar else 0.0
            if ce.le > 0:
                # con espirales (figura III.17): la transición es la espiral, del TE al EC; el bombeo exterior se quita antes del TE
                le = ce.le
                n = self.bombeo * le / s if s > 0 else 0.0
                if le < le_min - 1e-6:
                    self.avisos.append(f"Curva en {formato_estacion(ce.te)}: la espiral de {le:.2f} m es menor que la longitud mínima de "
                                       f"{le_min:.2f} m (8·Vp·S).")
                c = CurvaSCT(ce.te, ce.et, ce.radio, g, ce.izquierda, s, le, n, amp, ce.te - n, ce.ec, ce.ce, ce.et + n)
            else:
                le = le_min
                n = self.bombeo * le / s if s > 0 else 0.0
                pc, pt = ce.te, ce.et
                antes = params.reparto_tangente * le
                dentro = le - antes
                c = CurvaSCT(pc, pt, ce.radio, g, ce.izquierda, s, le, n, amp, pc - antes - n, pc + dentro, pt - dentro, pt + antes + n)
                if c.completa_hasta - c.completa_desde < (pt - pc) / 3.0 - 1e-6:
                    self.avisos.append(f"Curva en {formato_estacion(pc)}: la sobreelevación completa dura menos de un tercio de la curva.")
            if self.curvas and c.inicio < self.curvas[-1].fin - 1e-6:
                self.avisos.append(f"Curvas en {formato_estacion(self.curvas[-1].pc)} y {formato_estacion(pc)}: la tangente entre ellas no alcanza "
                                   "para las dos transiciones.")
            self.curvas.append(c)

    def en(self, estacion: float) -> dict:
        """{"izq": pendiente, "der": pendiente, "amp_izq": m, "amp_der": m}: pendientes con signo (negativa hacia afuera)."""
        b = self.bombeo
        salida = {"izq": -b, "der": -b, "amp_izq": 0.0, "amp_der": 0.0, "curva": None}
        mejor = None
        for c in self.curvas:
            if c.inicio <= estacion <= c.fin:
                if mejor is None or abs(estacion - (c.pc + c.pt) / 2) < abs(estacion - (mejor.pc + mejor.pt) / 2):
                    mejor = c
        if mejor is None:
            return salida
        c = mejor
        tasa = c.sobreelevacion / c.transicion if c.transicion > 0 else math.inf
        if estacion <= c.completa_desde:
            x = estacion - c.inicio
        elif estacion >= c.completa_hasta:
            x = c.fin - estacion
        else:
            x = math.inf
        exterior = min(c.sobreelevacion, -b + tasa * x)
        interior = -b if exterior < b else -exterior
        interior = max(interior, -c.sobreelevacion) if exterior >= b else interior
        # la ampliación crece con la sobreelevación: de 0 donde empieza la transición a todo donde la sobreelevación es completa
        fraccion = 1.0 if x == math.inf else max(0.0, min(1.0, (x - c.n) / c.transicion)) if c.transicion > 0 else 1.0
        if c.izquierda:
            salida.update(izq=interior, der=exterior, amp_izq=c.ampliacion * fraccion)
        else:
            salida.update(izq=exterior, der=interior, amp_der=c.ampliacion * fraccion)
        salida["curva"] = c
        return salida
