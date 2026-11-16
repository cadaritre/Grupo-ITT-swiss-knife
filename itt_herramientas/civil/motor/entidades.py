"""Descripción neutral de lo que la extensión dibuja.

El motor no habla con el editor: produce un `Dibujo` con capas, estilos, bloques y entidades.
La página lo traduce después a lotes de la API de control del editor, y `tvcivil.dxf` lo
escribe como DXF (para pruebas, para «Cuadros a DXF» y como vía de inserción masiva).

Unidades: las del dibujo (metros). Ángulos en grados, antihorario desde el eje X (como en CAD).
Colores: índice ACI (1 rojo, 2 amarillo, 3 verde, 4 cian, 5 azul, 6 magenta, 7 blanco/negro,
8 gris, 256 = por capa).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Union

__all__ = [
    "Capa",
    "EstiloTexto",
    "Linea",
    "Polilinea",
    "Polilinea3D",
    "PuntoCad",
    "Texto",
    "TextoMultiple",
    "Cara3D",
    "DefAtributo",
    "Bloque",
    "Insercion",
    "Sombreado",
    "Entidad",
    "Dibujo",
    "POR_CAPA",
]

POR_CAPA = 256

AlineacionH = Literal["izquierda", "centro", "derecha"]
AlineacionV = Literal["base", "abajo", "medio", "arriba"]


@dataclass
class Capa:
    nombre: str
    color: int = 7
    tipo_linea: str = "Continuous"
    grosor_mm: float | None = None  # None = por omisión
    descripcion: str = ""


@dataclass
class EstiloTexto:
    nombre: str
    fuente: str = "romans.shx"
    factor_ancho: float = 1.0


@dataclass
class _Base:
    capa: str
    color: int = POR_CAPA
    datos: dict[str, list[tuple[int, object]]] | None = None  # XDATA: aplicación → [(código, valor)]


@dataclass
class Linea(_Base):
    inicio: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    tipo: str = "linea"


@dataclass
class Polilinea(_Base):
    """Polilínea 2D (LWPOLYLINE) con elevación constante y comba (bulge) por vértice."""

    vertices: list[tuple[float, float]] = field(default_factory=list)
    combas: list[float] | None = None  # una por vértice: la del tramo que sale de él
    cerrada: bool = False
    elevacion: float = 0.0
    tipo: str = "polilinea"


@dataclass
class Polilinea3D(_Base):
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    cerrada: bool = False
    tipo: str = "polilinea3d"


@dataclass
class PuntoCad(_Base):
    posicion: tuple[float, float, float] = (0.0, 0.0, 0.0)
    tipo: str = "punto"


@dataclass
class Texto(_Base):
    valor: str = ""
    posicion: tuple[float, float] = (0.0, 0.0)
    altura: float = 1.0
    rotacion: float = 0.0
    estilo: str = "Standard"
    alineacion_h: AlineacionH = "izquierda"
    alineacion_v: AlineacionV = "base"
    tipo: str = "texto"


@dataclass
class TextoMultiple(_Base):
    valor: str = ""  # renglones separados por "\n"
    posicion: tuple[float, float] = (0.0, 0.0)
    altura: float = 1.0
    ancho: float = 0.0
    rotacion: float = 0.0
    estilo: str = "Standard"
    anclaje: str = "arriba_izquierda"
    fondo: bool = False         # máscara de fondo con el color del fondo del dibujo (tapa lo de abajo sin cortarlo)
    fondo_escala: float = 1.3   # margen del fondo, veces la altura (1 a 5, como el «factor de borde» de MTEXT)
    tipo: str = "texto_multiple"


@dataclass
class Cara3D(_Base):
    vertices: tuple[tuple[float, float, float], ...] = ()  # 3 o 4
    tipo: str = "cara3d"


@dataclass
class DefAtributo:
    etiqueta: str
    posicion: tuple[float, float]
    altura: float
    valor: str = ""
    estilo: str = "Standard"
    capa: str = "0"
    invisible: bool = False
    alineacion_h: AlineacionH = "izquierda"
    alineacion_v: AlineacionV = "base"


@dataclass
class Bloque:
    nombre: str
    entidades: list["Entidad"] = field(default_factory=list)
    atributos: list[DefAtributo] = field(default_factory=list)
    base: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class Insercion(_Base):
    bloque: str = ""
    posicion: tuple[float, float, float] = (0.0, 0.0, 0.0)
    escala: float = 1.0
    rotacion: float = 0.0
    atributos: dict[str, str] = field(default_factory=dict)
    tipo: str = "insercion"


@dataclass
class Sombreado(_Base):
    contorno: list[tuple[float, float]] = field(default_factory=list)
    solido: bool = True
    patron: str = "SOLID"
    escala_patron: float = 1.0
    tipo: str = "sombreado"


Entidad = Union[Linea, Polilinea, Polilinea3D, PuntoCad, Texto, TextoMultiple, Cara3D, Insercion, Sombreado]


@dataclass
class Dibujo:
    capas: dict[str, Capa] = field(default_factory=dict)
    estilos: dict[str, EstiloTexto] = field(default_factory=dict)
    bloques: dict[str, Bloque] = field(default_factory=dict)
    entidades: list[Entidad] = field(default_factory=list)

    def capa(self, capa: Capa) -> str:
        self.capas.setdefault(capa.nombre, capa)
        return capa.nombre

    def estilo(self, estilo: EstiloTexto) -> str:
        self.estilos.setdefault(estilo.nombre, estilo)
        return estilo.nombre

    def agregar(self, *entidades: Entidad) -> None:
        for e in entidades:
            if e.capa not in self.capas and e.capa != "0":
                self.capas[e.capa] = Capa(e.capa)
            self.entidades.append(e)

    def unir(self, otro: "Dibujo") -> "Dibujo":
        for c in otro.capas.values():
            self.capa(c)
        for s in otro.estilos.values():
            self.estilo(s)
        for b in otro.bloques.values():
            self.bloques.setdefault(b.nombre, b)
        self.entidades.extend(otro.entidades)
        return self

    def caja(self) -> tuple[float, float, float, float] | None:
        """(xmin, ymin, xmax, ymax) aproximada de lo dibujado (los textos cuentan por su punto de inserción y su altura)."""
        xs, ys = [], []
        for e in self.entidades:
            for x, y in _puntos_xy(e):
                xs.append(x)
                ys.append(y)
        return (min(xs), min(ys), max(xs), max(ys)) if xs else None

    def trasladar(self, dx: float, dy: float) -> "Dibujo":
        """Mueve todo lo dibujado (en su lugar)."""
        for e in self.entidades:
            _mover(e, dx, dy)
        return self

    def contar(self) -> dict[str, int]:
        cuenta: dict[str, int] = {}
        for e in self.entidades:
            cuenta[e.tipo] = cuenta.get(e.tipo, 0) + 1
        return cuenta

    def como_dict(self) -> dict:
        """JSON neutral para la página (la traducción a la API de control vive allá)."""
        return {
            "capas": [asdict(c) for c in self.capas.values()],
            "estilos": [asdict(s) for s in self.estilos.values()],
            "bloques": [
                {"nombre": b.nombre, "base": b.base, "atributos": [asdict(a) for a in b.atributos], "entidades": [asdict(e) for e in b.entidades]}
                for b in self.bloques.values()
            ],
            "entidades": [asdict(e) for e in self.entidades],
        }


def _puntos_xy(e) -> list[tuple[float, float]]:
    if isinstance(e, Linea):
        return [e.inicio[:2], e.fin[:2]]
    if isinstance(e, (Polilinea, Polilinea3D, Cara3D)):
        return [tuple(v[:2]) for v in e.vertices]
    if isinstance(e, (Texto, TextoMultiple)):
        return [tuple(e.posicion[:2]), (e.posicion[0], e.posicion[1] + e.altura)]
    if isinstance(e, (PuntoCad, Insercion)):
        return [tuple(e.posicion[:2])]
    if isinstance(e, Sombreado):
        return [tuple(v[:2]) for v in e.contorno]
    return []


def _mover(e, dx: float, dy: float) -> None:
    m2 = lambda v: (v[0] + dx, v[1] + dy, *v[2:])
    if isinstance(e, Linea):
        e.inicio, e.fin = m2(e.inicio), m2(e.fin)
    elif isinstance(e, (Polilinea, Polilinea3D)):
        e.vertices = [m2(v) for v in e.vertices]
    elif isinstance(e, Cara3D):
        e.vertices = tuple(m2(v) for v in e.vertices)
    elif isinstance(e, (Texto, TextoMultiple, PuntoCad, Insercion)):
        e.posicion = m2(e.posicion)
    elif isinstance(e, Sombreado):
        e.contorno = [m2(v) for v in e.contorno]
