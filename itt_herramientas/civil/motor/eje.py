"""Eje de trazo: polilínea con tramos rectos y curvos, cadenamiento y estaciones.

Cadenamiento en la forma mexicana «0+000.00» (kilómetros + metros). Un eje se construye con los
vértices y combas (bulge) de una polilínea; `inicio` es el cadenamiento del primer vértice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

__all__ = ["Tramo", "Eje", "formato_estacion", "leer_estacion"]


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
            n = 1 if t.centro is None else max(2, math.ceil(t.largo / paso_arco))
            for k in range(n):
                x, y, _ = t.punto(t.largo * k / n)
                salida.append((x, y, self.inicio + t.inicio + t.largo * k / n))
        x, y, _ = self.tramos[-1].punto(self.tramos[-1].largo)
        salida.append((x, y, self.fin))
        return salida
