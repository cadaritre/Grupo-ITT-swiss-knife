"""Eje de trazo: polilínea con tramos rectos y curvos, cadenamiento y estaciones.

Cadenamiento en la forma mexicana «0+000.00» (kilómetros + metros). Un eje se construye con los
vértices y combas (bulge) de una polilínea; `inicio` es el cadenamiento del primer vértice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

__all__ = ["Tramo", "TramoEspiral", "Eje", "EjePI", "CurvaEje", "clotoide", "elementos_espiral", "formato_estacion", "leer_estacion"]


def formato_estacion(m: float, decimales: int = 2) -> str:
    """1234.5 → «1+234.50»; −12 → «−0+012.00»."""
    signo = "-" if m < 0 else ""
    m = abs(round(m, decimales))
    km, resto = divmod(m, 1000.0)
    if round(resto, decimales) >= 1000.0:
        km, resto = km + 1, 0.0
    ancho = 4 + decimales if decimales else 3
    return f"{signo}{int(km)}+{resto:0{ancho}.{decimales}f}"


def leer_estacion(texto: str) -> float:
    t = texto.strip().replace(" ", "")
    signo = -1.0 if t.startswith("-") else 1.0
    t = t.lstrip("+-")
    if "+" in t:
        km, m = t.split("+", 1)
        return signo * (float(km) * 1000.0 + float(m))
    return signo * float(t)


@dataclass(frozen=True)
class Tramo:
    a: tuple[float, float]
    b: tuple[float, float]
    comba: float
    inicio: float  # distancia acumulada al empezar el tramo
    largo: float
    centro: tuple[float, float] | None = None
    radio: float = 0.0
    theta: float = 0.0  # ángulo barrido con signo (antihorario +)

    def punto(self, s: float) -> tuple[float, float, float]:
        """(x, y, dirección en radianes desde el eje X) a la distancia s dentro del tramo."""
        if self.centro is None:
            t = s / self.largo if self.largo else 0.0
            x = self.a[0] + t * (self.b[0] - self.a[0])
            y = self.a[1] + t * (self.b[1] - self.a[1])
            return x, y, math.atan2(self.b[1] - self.a[1], self.b[0] - self.a[0])
        ang0 = math.atan2(self.a[1] - self.centro[1], self.a[0] - self.centro[0])
        phi = ang0 + math.copysign(s / self.radio, self.theta)
        x = self.centro[0] + self.radio * math.cos(phi)
        y = self.centro[1] + self.radio * math.sin(phi)
        direccion = phi + math.copysign(math.pi / 2, self.theta)
        return x, y, direccion


class Eje:
    def __init__(self, vertices: Sequence[Sequence[float]], combas: Sequence[float] | None = None, inicio: float = 0.0):
        v = [(float(p[0]), float(p[1])) for p in vertices]
        b = list(combas) if combas else [0.0] * len(v)
        if len(v) < 2:
            raise ValueError("El eje necesita al menos dos vértices.")
        self.inicio = float(inicio)
        self.tramos: list[Tramo] = []
        acumulado = 0.0
        for i in range(len(v) - 1):
            a, c = v[i], v[i + 1]
            cuerda = math.hypot(c[0] - a[0], c[1] - a[1])
            if cuerda == 0:
                continue
            if b[i]:
                theta = 4.0 * math.atan(b[i])
                r = cuerda * (1 + b[i] ** 2) / (4 * abs(b[i]))
                d = cuerda * (1 - b[i] ** 2) / (4 * b[i])
                nx, ny = -(c[1] - a[1]) / cuerda, (c[0] - a[0]) / cuerda
                centro = ((a[0] + c[0]) / 2 + nx * d, (a[1] + c[1]) / 2 + ny * d)
                largo = r * abs(theta)
                self.tramos.append(Tramo(a, c, b[i], acumulado, largo, centro, r, theta))
            else:
                largo = cuerda
                self.tramos.append(Tramo(a, c, 0.0, acumulado, largo))
            acumulado += largo
        if not self.tramos:
            raise ValueError("El eje no tiene longitud.")
        self.longitud = acumulado
        self.curvas = [CurvaEje(k, t.radio, 0.0, t.theta > 0, self.inicio + t.inicio, self.inicio + t.inicio, self.inicio + t.inicio + t.largo,
                                self.inicio + t.inicio + t.largo, math.degrees(abs(t.theta))) for k, t in enumerate(self.tramos) if t.centro is not None]
        self.elementos = []

    @property
    def fin(self) -> float:
        return self.inicio + self.longitud

    def punto(self, estacion: float) -> tuple[float, float, float]:
        """(x, y, dirección) en el cadenamiento dado (se recorta a los extremos del eje)."""
        s = min(max(estacion - self.inicio, 0.0), self.longitud)
        for t in self.tramos:
            if s <= t.inicio + t.largo + 1e-9:
                return t.punto(min(s - t.inicio, t.largo))
        t = self.tramos[-1]
        return t.punto(t.largo)

    def normal(self, estacion: float, desplazamiento: float) -> tuple[float, float]:
        """Punto a `desplazamiento` de la estación: positivo a la derecha del sentido del eje."""
        x, y, d = self.punto(estacion)
        return x + desplazamiento * math.sin(d), y - desplazamiento * math.cos(d)

    def vertices_estaciones(self) -> list[float]:
        """Cadenamiento de cada vértice (inicio y fin de tramos)."""
        return [self.inicio + t.inicio for t in self.tramos] + [self.fin]

    def estaciones(self, intervalo: float, incluir_vertices: bool = True) -> list[float]:
        """Estaciones redondas cada `intervalo` (múltiplos desde 0+000), más el inicio, el fin y los vértices."""
        if not intervalo > 0:
            raise ValueError("El intervalo de estaciones debe ser positivo.")
        e = {round(self.inicio, 6), round(self.fin, 6)}
        k = math.ceil(self.inicio / intervalo - 1e-9)
        while k * intervalo < self.fin - 1e-9:
            e.add(round(k * intervalo, 6))
            k += 1
        if incluir_vertices:
            e.update(round(x, 6) for x in self.vertices_estaciones())
        return sorted(x for x in e if self.inicio - 1e-9 <= x <= self.fin + 1e-9)

    def densificar(self, paso_arco: float = 1.0) -> list[tuple[float, float, float]]:
        """Puntos (x, y, estación) del eje, con los arcos partidos cada `paso_arco` m."""
        salida = []
        for t in self.tramos:
            curvo = t.centro is not None or isinstance(t, TramoEspiral)
            n = 1 if not curvo else max(2, math.ceil(t.largo / paso_arco))
            for k in range(n):
                x, y, _ = t.punto(t.largo * k / n)
                salida.append((x, y, self.inicio + t.inicio + t.largo * k / n))
        x, y, _ = self.tramos[-1].punto(self.tramos[-1].largo)
        salida.append((x, y, self.fin))
        return salida


# --- espirales de transición (clotoide) ---------------------------------------------------------------------------------------

def clotoide(l: float, le: float, radio: float) -> tuple[float, float]:
    """Coordenadas locales (x a lo largo de la tangente, y hacia el lado de la curva) a la distancia l del TE de una espiral de Euler
    de longitud le que termina con radio `radio` (R·le = K²). Serie de Fresnel hasta que los términos no cuentan."""
    if l <= 0:
        return 0.0, 0.0
    a2 = 2.0 * radio * le                       # 2K²
    t = l * l / a2                              # ángulo de la tangente en l (rad)
    x = y = 0.0
    termino_x, termino_y = l, l * t / 3.0
    n = 0
    while True:
        x += termino_x
        y += termino_y
        n += 1
        # x = l Σ (−1)^n t^(2n) / ((4n+1)(2n)!),   y = l Σ (−1)^n t^(2n+1) / ((4n+3)(2n+1)!)
        termino_x = l * ((-1) ** n) * t ** (2 * n) / ((4 * n + 1) * math.factorial(2 * n))
        termino_y = l * ((-1) ** n) * t ** (2 * n + 1) / ((4 * n + 3) * math.factorial(2 * n + 1))
        if abs(termino_x) < 1e-13 * max(1.0, l) and abs(termino_y) < 1e-13 * max(1.0, l) or n > 30:
            return x, y


@dataclass(frozen=True)
class TramoEspiral:
    """Espiral de transición: de la tangente (TE) al radio `radio` (EC) si `entrada`, o del radio a la tangente (CE → ET)."""

    a: tuple[float, float]  # punto inicial del tramo
    rumbo: float  # dirección (rad, desde el eje X) en el punto inicial
    radio: float
    largo: float  # le
    giro: int  # +1 a la izquierda (antihorario), −1 a la derecha
    entrada: bool
    inicio: float
    centro: None = None
    comba: float = 0.0

    def punto(self, s: float) -> tuple[float, float, float]:
        s = min(max(s, 0.0), self.largo)
        if self.entrada:
            x, y = clotoide(s, self.largo, self.radio)
            tau = s * s / (2.0 * self.radio * self.largo)
            c, sn = math.cos(self.rumbo), math.sin(self.rumbo)
            return self.a[0] + x * c - self.giro * y * sn, self.a[1] + x * sn + self.giro * y * c, self.rumbo + self.giro * tau
        # salida: se recorre la espiral de entrada al revés desde su ET (que se calcula con el rumbo final del tramo)
        theta = self.largo / (2.0 * self.radio)
        rumbo_et = self.rumbo + self.giro * theta
        xe, ye = clotoide(self.largo, self.largo, self.radio)
        # ET visto desde el CE: el CE en coordenadas locales del ET (mirando hacia atrás) es (xe, ye)
        cb, sb = math.cos(rumbo_et + math.pi), math.sin(rumbo_et + math.pi)
        et = (self.a[0] - (xe * cb + self.giro * ye * sb), self.a[1] - (xe * sb - self.giro * ye * cb))
        r = self.largo - s                       # distancia que falta hasta el ET
        x, y = clotoide(r, self.largo, self.radio)
        tau = r * r / (2.0 * self.radio * self.largo)
        return et[0] + x * cb + self.giro * y * sb, et[1] + x * sb - self.giro * y * cb, rumbo_et - self.giro * tau


@dataclass
class CurvaEje:
    """Una curva horizontal del eje: circular simple (le = 0, TE = PC, ET = PT) o espiral–circular–espiral."""

    pi: int  # índice del PI (vértice de las tangentes) o del tramo curvo
    radio: float
    le: float
    izquierda: bool
    te: float  # estaciones
    ec: float
    ce: float
    et: float
    delta: float = 0.0  # deflexión entre tangentes, grados (sin signo)

    @property
    def grado(self) -> float:
        return 1145.92 / self.radio


def elementos_espiral(delta_grados: float, radio: float, le: float) -> dict:
    """Elementos de la curva circular con espirales de transición (manual SCT 2018, cap. II, C.2.3, figura II.4), con la clotoide exacta.

    Ángulos en grados; longitudes en m."""
    d = math.radians(abs(delta_grados))
    theta = le / (2.0 * radio)                   # θe = le / 2Rc = Gc·le/40 (grados)
    if 2 * theta >= d:
        raise ValueError(f"Con R = {radio:.2f} m, dos espirales de {le:.2f} m no caben en una deflexión de {abs(delta_grados):.4f}°.")
    xc, yc = clotoide(le, le, radio)
    k = xc - radio * math.sin(theta)
    p = yc - radio * (1.0 - math.cos(theta))
    ste = k + (radio + p) * math.tan(d / 2.0)
    delta_c = d - 2 * theta
    return {"theta_e": math.degrees(theta), "delta_c": math.degrees(delta_c), "xc": xc, "yc": yc, "k": k, "p": p, "ste": ste,
            "tl": xc - yc / math.tan(theta), "tc": yc / math.sin(theta), "cle": math.hypot(xc, yc), "phi_c": math.degrees(math.atan2(yc, xc)),
            "externa": (radio + p) / math.cos(d / 2.0) - radio, "lc": radio * delta_c, "longitud": 2 * le + radio * delta_c}


class EjePI(Eje):
    """Eje definido por sus PI (vértices de las tangentes) y, en cada PI interior, el radio y la longitud de las espirales (0 = curva
    circular simple). Estaciones por TE, EC, CE y ET."""

    def __init__(self, pis: Sequence[Sequence[float]], curvas: Sequence[Sequence[float] | None], inicio: float = 0.0):
        v = [(float(p[0]), float(p[1])) for p in pis]
        if len(v) < 2:
            raise ValueError("El eje necesita al menos dos PI.")
        if len(curvas) != max(0, len(v) - 2):
            raise ValueError("Da el radio y la espiral de cada PI interior.")
        self.inicio = float(inicio)
        self.tramos = []
        self.curvas: list[CurvaEje] = []
        self.elementos: list[dict] = []
        actual = v[0]
        acumulado = 0.0
        for i in range(1, len(v) - 1):
            a, b, c = v[i - 1], v[i], v[i + 1]
            r1 = math.atan2(b[1] - a[1], b[0] - a[0])
            r2 = math.atan2(c[1] - b[1], c[0] - b[0])
            defl = (r2 - r1 + math.pi) % (2 * math.pi) - math.pi
            dato = curvas[i - 1]
            if not dato or abs(defl) < 1e-9:
                self.elementos.append({})
                continue
            radio, le = float(dato[0]), float(dato[1]) if len(dato) > 1 and dato[1] else 0.0
            if radio <= 0 or le < 0:
                raise ValueError("El radio debe ser positivo y la espiral no negativa.")
            giro = 1 if defl > 0 else -1
            d = abs(defl)
            if le > 0:
                el = elementos_espiral(math.degrees(d), radio, le)
                ste = el["ste"]
            else:
                el = {"theta_e": 0.0, "delta_c": math.degrees(d), "ste": radio * math.tan(d / 2), "lc": radio * d, "longitud": radio * d,
                      "externa": radio / math.cos(d / 2) - radio}
                ste = el["ste"]
            el.update(pi=i, delta=math.degrees(d), radio=radio, le=le, grado=1145.92 / radio, izquierda=giro > 0)
            te = (b[0] - ste * math.cos(r1), b[1] - ste * math.sin(r1))
            disp = math.hypot(te[0] - actual[0], te[1] - actual[1])
            if (te[0] - actual[0]) * math.cos(r1) + (te[1] - actual[1]) * math.sin(r1) < -1e-6:
                raise ValueError(f"La curva del PI {i} se encima con la anterior: reduce el radio o las espirales.")
            if disp > 1e-9:
                self.tramos.append(Tramo(actual, te, 0.0, acumulado, disp))
                acumulado += disp
            est_te = self.inicio + acumulado
            punto_ec, rumbo_ec = te, r1
            if le > 0:
                esp = TramoEspiral(te, r1, radio, le, giro, True, acumulado)
                self.tramos.append(esp)
                acumulado += le
                x, y, rumbo_ec = esp.punto(le)
                punto_ec = (x, y)
            est_ec = self.inicio + acumulado
            delta_c = math.radians(el["delta_c"])
            centro = (punto_ec[0] - giro * radio * math.sin(rumbo_ec), punto_ec[1] + giro * radio * math.cos(rumbo_ec))
            ang0 = math.atan2(punto_ec[1] - centro[1], punto_ec[0] - centro[0])
            punto_ce = (centro[0] + radio * math.cos(ang0 + giro * delta_c), centro[1] + radio * math.sin(ang0 + giro * delta_c))
            lc = radio * delta_c
            if lc > 1e-9:
                self.tramos.append(Tramo(punto_ec, punto_ce, math.tan(giro * delta_c / 4), acumulado, lc, centro, radio, giro * delta_c))
                acumulado += lc
            est_ce = self.inicio + acumulado
            rumbo_ce = rumbo_ec + giro * delta_c
            fin = punto_ce
            if le > 0:
                esp = TramoEspiral(punto_ce, rumbo_ce, radio, le, giro, False, acumulado)
                self.tramos.append(esp)
                acumulado += le
                x, y, _ = esp.punto(le)
                fin = (x, y)
            est_et = self.inicio + acumulado
            el.update(te=est_te, ec=est_ec, ce=est_ce, et=est_et)
            self.elementos.append(el)
            self.curvas.append(CurvaEje(i, radio, le, giro > 0, est_te, est_ec, est_ce, est_et, math.degrees(d)))
            actual = fin
        ultimo = v[-1]
        d = math.hypot(ultimo[0] - actual[0], ultimo[1] - actual[1])
        r_fin = math.atan2(ultimo[1] - v[-2][1], ultimo[0] - v[-2][0])
        if (ultimo[0] - actual[0]) * math.cos(r_fin) + (ultimo[1] - actual[1]) * math.sin(r_fin) < -1e-6:
            raise ValueError("La última curva pasa del final del eje.")
        if d > 1e-9:
            self.tramos.append(Tramo(actual, ultimo, 0.0, acumulado, d))
            acumulado += d
        if not self.tramos:
            raise ValueError("El eje no tiene longitud.")
        self.longitud = acumulado

    def vertices_estaciones(self) -> list[float]:
        return sorted({self.inicio + t.inicio for t in self.tramos} | {self.fin})
