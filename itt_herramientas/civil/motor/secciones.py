"""Secciones transversales a lo largo de un eje, sección de proyecto con plantilla y sus áreas.

Desplazamientos: negativos a la izquierda del eje, positivos a la derecha (mirando en el sentido
del cadenamiento). El terreno de cada sección se muestrea exactamente sobre el TIN (un punto en
cada cruce con una arista).

Plantilla de proyecto: corona de `ancho_izq` + `ancho_der` con bombeo hacia afuera, y taludes
que salen de la orilla de la corona hasta tocar el terreno: de corte si el terreno queda arriba,
de terraplén si queda abajo. El talud se da como horizontal por 1 vertical (1.5 = 1.5:1).

Plantilla de carretera (`PlantillaCarretera`): carriles y acotamientos con su pendiente transversal, cuneta en corte, espesor de
pavimento y despalme. Las terracerías se miden a la **subrasante** (la corona bajada el espesor del pavimento); la cuneta solo va en
corte, del borde del acotamiento hacia abajo hasta su profundidad, y de su fondo sale el talud de corte. El despalme es el espesor de
terreno vegetal que se quita bajo todo el ancho de la obra (entre los chaflanes). Bajo el terraplén ese hueco se vuelve a llenar, así
que su volumen se **suma al terraplén**; en el corte ese espesor ya salió como despalme, así que se **resta del corte** (no se cuenta
dos veces). El despalme se reporta aparte, y por zona (bajo terraplén y en corte).

Áreas: la integral exacta (el terreno y el proyecto son poligonales) de la diferencia
terreno − proyecto entre los dos puntos de chaflán: lo positivo es corte, lo negativo terraplén.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .eje import Eje, formato_estacion
from .entidades import Capa, Dibujo, EstiloTexto, Linea, Polilinea, Sombreado, Texto
from .escala import Escala
from .perfiles import Muestreador, Rasante

__all__ = ["Plantilla", "PlantillaCarretera", "Seccion", "seccion_terreno", "aplicar_plantilla", "secciones", "areas_entre", "dibujar_secciones", "CAPAS_SECCION",
           "HojaSecciones", "acomodo", "regiones", "medidas_secciones"]

CAPAS_SECCION = {
    "terreno": Capa("SECCION_TERRENO", 3, grosor_mm=0.25, descripcion="Terreno en las secciones"),
    "proyecto": Capa("SECCION_PROYECTO", 1, grosor_mm=0.35, descripcion="Sección de proyecto (subrasante y taludes)"),
    "rasante": Capa("SECCION_RASANTE", 5, grosor_mm=0.25, descripcion="Superficie terminada de la carretera"),
    "reticula": Capa("SECCION_RETICULA", 8, descripcion="Retícula de cada sección"),
    "textos": Capa("SECCION_TEXTOS", 7, descripcion="Estación, desplazamientos, cotas y áreas de las secciones"),
    "eje": Capa("SECCION_EJE", 1, descripcion="Eje de cada sección"),
    "corte": Capa("SECCION_CORTE", 30, descripcion="Área de corte (sombreado)"),
    "terraplen": Capa("SECCION_TERRAPLEN", 140, descripcion="Área de terraplén (sombreado)"),
    "hoja": Capa("SECCION_HOJA", 7, descripcion="Marco y título de cada hoja de secciones"),
}


@dataclass
class Plantilla:
    ancho_izq: float = 3.5
    ancho_der: float = 3.5
    bombeo: float = 0.02  # m/m, hacia afuera
    talud_corte: float = 1.0  # horizontal por 1 vertical
    talud_terraplen: float = 1.5

    def __post_init__(self):
        if self.ancho_izq < 0 or self.ancho_der < 0 or self.talud_corte <= 0 or self.talud_terraplen <= 0:
            raise ValueError("Revisa la plantilla: anchos no negativos y taludes positivos.")


@dataclass
class PlantillaCarretera:
    """Plantilla simétrica de carretera (medidas en m; pendientes en m/m, negativas hacia afuera = bombeo)."""

    carriles_por_lado: int = 1
    ancho_carril: float = 3.5
    pendiente_carril: float = -0.02
    acotamiento: float = 2.5
    pendiente_acotamiento: float = -0.04
    cuneta_ancho: float = 1.0
    cuneta_profundidad: float = 0.5
    espesor_pavimento: float = 0.30
    despalme: float = 0.20
    talud_corte: float = 1.0
    talud_terraplen: float = 1.5
    sct: object | None = None  # ParametrosSCT: sobreelevación y ampliación en curvas (tvcivil.sobreelevacion)

    def __post_init__(self):
        if self.carriles_por_lado < 1 or self.ancho_carril <= 0:
            raise ValueError("La plantilla necesita al menos un carril por lado con ancho.")
        if min(self.acotamiento, self.cuneta_ancho, self.cuneta_profundidad, self.espesor_pavimento, self.despalme) < 0:
            raise ValueError("Revisa la plantilla: anchos, profundidades y espesores no negativos.")
        if self.talud_corte <= 0 or self.talud_terraplen <= 0:
            raise ValueError("Los taludes deben ser positivos.")
        if abs(self.pendiente_carril) > 0.2 or abs(self.pendiente_acotamiento) > 0.2:
            raise ValueError("Las pendientes transversales pasan del 20 %.")

    @property
    def semiancho(self) -> float:
        return self.carriles_por_lado * self.ancho_carril + self.acotamiento

    def corona(self, z_eje: float, pendiente_carril: float | None = None, ampliacion: float = 0.0) -> list[tuple[float, float]]:
        """Rasante (superficie terminada) de un lado, del eje hacia afuera: [(desplazamiento ≥ 0, z)].

        `pendiente_carril`: la del lado en esa estación (sobreelevación en curva); el acotamiento la sigue cuando es más fuerte que la
        suya hacia el mismo lado. `ampliacion`: ancho que se agrega a la calzada de ese lado (interior de la curva)."""
        pc = self.pendiente_carril if pendiente_carril is None else pendiente_carril
        pa = self.pendiente_acotamiento
        if pendiente_carril is not None and (pc > 0 or pc < pa):
            pa = pc                                      # en curva el acotamiento sigue a la calzada
        o, z = 0.0, z_eje
        pts = [(0.0, z_eje)]
        ancho = self.carriles_por_lado * self.ancho_carril + ampliacion
        o += ancho
        z += pc * ancho
        pts.append((o, z))
        if self.acotamiento > 0:
            o += self.acotamiento
            z += pa * self.acotamiento
            pts.append((o, z))
        return pts


@dataclass
class Seccion:
    estacion: float
    terreno: list[tuple[float, float]]  # (desplazamiento, z) ordenado
    proyecto: list[tuple[float, float]] = field(default_factory=list)
    corte: float = 0.0
    terraplen: float = 0.0
    avisos: list[str] = field(default_factory=list)
    rasante: list[tuple[float, float]] = field(default_factory=list)  # superficie terminada (plantilla de carretera)
    peralte: dict | None = None  # pendientes y ampliación de la estación (curvas con sobreelevación)
    despalme: float = 0.0  # área de despalme, m²
    despalme_terraplen: float = 0.0  # parte bajo el terraplén (se suma al terraplén)
    despalme_corte: float = 0.0  # parte en el corte (se resta del corte)

    def z_terreno(self, o: float) -> float | None:
        return _interp(self.terreno, o)


def _interp(pl: Sequence[tuple[float, float]], o: float) -> float | None:
    if not pl or o < pl[0][0] - 1e-9 or o > pl[-1][0] + 1e-9:
        return None
    for (o1, z1), (o2, z2) in zip(pl, pl[1:]):
        if o1 - 1e-9 <= o <= o2 + 1e-9:
            return z1 if o2 == o1 else z1 + (o - o1) / (o2 - o1) * (z2 - z1)
    return pl[-1][1]


def seccion_terreno(muestreador: Muestreador, eje: Eje, estacion: float, izq: float, der: float) -> Seccion:
    a = eje.normal(estacion, -izq)
    b = eje.normal(estacion, der)
    puntos = []
    for t, z in muestreador.segmento(a, b):
        if z is not None:
            puntos.append((-izq + t * (izq + der), z))
    return Seccion(estacion, puntos)


def _cruce_talud(terreno, o0, z0, sentido, pendiente):
    """Primer cruce, saliendo de (o0, z0) hacia `sentido` (±1), de la recta z = z0 + pendiente·|o − o0| con el terreno."""
    pts = terreno if sentido > 0 else [(-o, z) for o, z in reversed(terreno)]
    oo = o0 * sentido
    f = lambda o, z: z - (z0 + pendiente * (o - oo))
    previo = None
    for o, z in pts:
        if o < oo - 1e-9:
            continue
        if previo is None:
            zt = _interp(pts, oo)
            if zt is None:
                return None
            previo = (oo, zt)
            if abs(f(*previo)) < 1e-12:
                return previo[0] * sentido, previo[1]
        fa, fb = f(*previo), f(o, z)
        if fa == 0:
            return previo[0] * sentido, previo[1]
        if fa * fb < 0 or fb == 0:
            t = fa / (fa - fb)
            oc = previo[0] + t * (o - previo[0])
            return oc * sentido, z0 + pendiente * (oc - oo)
        previo = (o, z)
    return None


def aplicar_plantilla(s: Seccion, z_eje: float, p: Plantilla) -> Seccion:
    """Sección de proyecto sobre el terreno de `s`, con la rasante `z_eje` en el eje; calcula las áreas."""
    if len(s.terreno) < 2:
        s.avisos.append(f"{formato_estacion(s.estacion)}: sin terreno.")
        return s
    lados = []
    for sentido, ancho in ((-1, p.ancho_izq), (1, p.ancho_der)):
        o_orilla = sentido * ancho
        z_orilla = z_eje - p.bombeo * ancho
        zt = s.z_terreno(o_orilla)
        corona = [(o_orilla, z_orilla)]
        if zt is None:
            s.avisos.append(f"{formato_estacion(s.estacion)}: la corona sale del terreno muestreado; amplía el ancho de las secciones.")
            lados.append(corona)
            continue
        if abs(zt - z_orilla) < 1e-9:
            lados.append(corona)
            continue
        pendiente = 1.0 / p.talud_corte if zt > z_orilla else -1.0 / p.talud_terraplen
        cruce = _cruce_talud(s.terreno, o_orilla, z_orilla, sentido, pendiente)
        if cruce is None:
            s.avisos.append(f"{formato_estacion(s.estacion)}: el talud no alcanza el terreno dentro del ancho de la sección.")
            ext = s.terreno[0][0] if sentido < 0 else s.terreno[-1][0]
            cruce = (ext, z_orilla + pendiente * abs(ext - o_orilla))
        lados.append(corona + [cruce])
    izq, der = lados
    s.proyecto = list(reversed(izq)) + [(0.0, z_eje)] + der
    s.proyecto = [q for k, q in enumerate(s.proyecto) if k == 0 or q[0] > s.proyecto[k - 1][0] + 1e-12]
    s.corte, s.terraplen = areas_entre(s.terreno, s.proyecto)
    return s


def aplicar_plantilla_carretera(s: Seccion, z_eje: float, p: PlantillaCarretera, peralte: dict | None = None) -> Seccion:
    """Sección de carretera: subrasante con cuneta y taludes hasta el terreno, rasante terminada, áreas y despalme.

    `peralte` (de `Peraltes.en`): pendiente de cada lado y ampliación en esa estación; sin él, el bombeo de la plantilla."""
    if len(s.terreno) < 2:
        s.avisos.append(f"{formato_estacion(s.estacion)}: sin terreno.")
        return s
    lados_corona = {}
    for sentido, clave in ((-1, "izq"), (1, "der")):
        if peralte is None:
            lados_corona[sentido] = p.corona(z_eje)
        else:
            lados_corona[sentido] = p.corona(z_eje, peralte[clave], peralte["amp_" + clave])
    lados, chaflanes = [], []
    for sentido in (-1, 1):
        sub = [(o, z - p.espesor_pavimento) for o, z in lados_corona[sentido]]    # subrasante, paralela a la rasante
        pts = [(sentido * o, z) for o, z in sub]
        o_b, z_b = pts[-1]
        zt = s.z_terreno(o_b)
        if zt is None:
            s.avisos.append(f"{formato_estacion(s.estacion)}: la corona sale del terreno muestreado; amplía el ancho de las secciones.")
            lados.append(pts)
            chaflanes.append(o_b)
            continue
        if zt > z_b + 1e-9:                                     # corte: cuneta y talud de corte desde su fondo
            if p.cuneta_ancho > 0 and p.cuneta_profundidad > 0:
                o_b, z_b = o_b + sentido * p.cuneta_ancho, z_b - p.cuneta_profundidad
                pts.append((o_b, z_b))
            pendiente = 1.0 / p.talud_corte
        elif zt < z_b - 1e-9:
            pendiente = -1.0 / p.talud_terraplen
        else:
            lados.append(pts)
            chaflanes.append(o_b)
            continue
        cruce = _cruce_talud(s.terreno, o_b, z_b, sentido, pendiente)
        if cruce is None:
            s.avisos.append(f"{formato_estacion(s.estacion)}: el talud no alcanza el terreno dentro del ancho de la sección.")
            ext = s.terreno[0][0] if sentido < 0 else s.terreno[-1][0]
            cruce = (ext, z_b + pendiente * abs(ext - o_b))
        pts.append(cruce)
        lados.append(pts)
        chaflanes.append(cruce[0])
    izq, der = lados
    proyecto = list(reversed(izq[1:])) + der
    s.proyecto = [q for k, q in enumerate(sorted(proyecto)) if k == 0 or q[0] > sorted(proyecto)[k - 1][0] + 1e-12]
    s.rasante = sorted({(-o, z) for o, z in lados_corona[-1]} | {(o, z) for o, z in lados_corona[1]})
    s.corte, s.terraplen = areas_entre(s.terreno, s.proyecto)
    s.peralte = {k: v for k, v in (peralte or {}).items() if k != "curva"} or None
    if p.despalme > 0:
        a_corte, a_terraplen = anchos_entre(s.terreno, s.proyecto, chaflanes[0], chaflanes[1])
        s.despalme_corte, s.despalme_terraplen = p.despalme * a_corte, p.despalme * a_terraplen
        s.despalme = s.despalme_corte + s.despalme_terraplen
        s.terraplen += s.despalme_terraplen
        s.corte = max(0.0, s.corte - s.despalme_corte)
    return s


def anchos_entre(terreno, proyecto, o0: float, o1: float) -> tuple[float, float]:
    """Ancho horizontal, entre o0 y o1, donde el terreno queda arriba del proyecto (corte) y donde queda abajo (terraplén)."""
    a, b = max(o0, terreno[0][0], proyecto[0][0]), min(o1, terreno[-1][0], proyecto[-1][0])
    if b <= a:
        return 0.0, 0.0
    cortes = sorted({a, b, *(o for o, _ in terreno if a < o < b), *(o for o, _ in proyecto if a < o < b)})
    w_c = w_t = 0.0
    for x0, x1 in zip(cortes, cortes[1:]):
        d0 = _interp(terreno, x0) - _interp(proyecto, x0)
        d1 = _interp(terreno, x1) - _interp(proyecto, x1)
        ancho = x1 - x0
        if d0 >= 0 and d1 >= 0:
            if d0 > 0 or d1 > 0:
                w_c += ancho
            else:
                w_t += ancho           # terreno y proyecto juntos: el despalme se rellena
        elif d0 <= 0 and d1 <= 0:
            w_t += ancho
        else:
            t = d0 / (d0 - d1)
            (w_c, w_t) = (w_c + t * ancho, w_t + (1 - t) * ancho) if d0 > 0 else (w_c + (1 - t) * ancho, w_t + t * ancho)
    return w_c, w_t


def areas_entre(terreno: Sequence[tuple[float, float]], proyecto: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """(corte, terraplén): ∫ max(t − p, 0) y ∫ max(p − t, 0) donde ambas poligonales existen."""
    if len(terreno) < 2 or len(proyecto) < 2:
        return 0.0, 0.0
    o0 = max(terreno[0][0], proyecto[0][0])
    o1 = min(terreno[-1][0], proyecto[-1][0])
    if o1 <= o0:
        return 0.0, 0.0
    cortes = sorted({o0, o1, *(o for o, _ in terreno if o0 < o < o1), *(o for o, _ in proyecto if o0 < o < o1)})
    corte = terraplen = 0.0
    for a, b in zip(cortes, cortes[1:]):
        da = _interp(terreno, a) - _interp(proyecto, a)
        db = _interp(terreno, b) - _interp(proyecto, b)
        ancho = b - a
        if da >= 0 and db >= 0:
            corte += (da + db) / 2 * ancho
        elif da <= 0 and db <= 0:
            terraplen += -(da + db) / 2 * ancho
        else:
            t = da / (da - db)
            x = t * ancho
            if da > 0:
                corte += da * x / 2
                terraplen += -db * (ancho - x) / 2
            else:
                terraplen += -da * x / 2
                corte += db * (ancho - x) / 2
    return corte, terraplen


def secciones(tin, eje: Eje, estaciones: Sequence[float], izq: float, der: float, rasante: Rasante | None = None,
              plantilla: Plantilla | PlantillaCarretera | None = None) -> list[Seccion]:
    m = Muestreador(tin)
    salida = []
    peraltes = None
    if isinstance(plantilla, PlantillaCarretera) and plantilla.sct is not None:
        from .sobreelevacion import Peraltes

        peraltes = Peraltes(eje, plantilla.sct, plantilla.pendiente_carril, 2 * plantilla.carriles_por_lado, 2 * plantilla.carriles_por_lado * plantilla.ancho_carril)
        # las estaciones donde cambia la sección (inicio y fin de transiciones y de la sobreelevación completa) también van
        extra = [x for c in peraltes.curvas for x in (c.inicio, c.inicio + c.n, c.completa_desde, c.completa_hasta, c.fin - c.n, c.fin)]
        estaciones = sorted({round(x, 6) for x in list(estaciones) + extra if eje.inicio - 1e-9 <= x <= eje.fin + 1e-9})
    for e in estaciones:
        s = seccion_terreno(m, eje, e, izq, der)
        if rasante is not None:
            if isinstance(plantilla, PlantillaCarretera):
                aplicar_plantilla_carretera(s, rasante.elevacion(e), plantilla, peraltes.en(e) if peraltes else None)
            else:
                aplicar_plantilla(s, rasante.elevacion(e), plantilla or Plantilla())
        salida.append(s)
    return salida


# --- hoja de secciones ---------------------------------------------------------------------------------------------------------------

ORDENES_HOJA = ("columnas_abajo_arriba", "columnas_arriba_abajo", "renglones")


@dataclass
class HojaSecciones:
    """Cómo se acomodan y dibujan las secciones (como la hoja de secciones de CivilCAD, con diseño propio).

    - `escala_h`, `escala_v`: denominadores de la escala horizontal y vertical (None = la del plano);
    - `paso_h`, `paso_v`: separación de la retícula en m (0 = automática: un paso redondo, nunca a menos de 5 mm en el papel);
    - `reticula`: con retícula completa; sin ella, el marco con marcas en cada paso;
    - `columnas` y `renglones` por hoja, `separacion_mm` entre secciones (mm de papel);
    - `orden`: por columnas de abajo arriba (la estación crece hacia arriba, como en las hojas de secciones de carretera), por columnas de
      arriba abajo, o por renglones de izquierda a derecha;
    - `marco`: el marco de cada hoja con su título; `sombrear`: las áreas de corte y terraplén; `chaflanes`: desplazamiento y cota de cada
      chaflán.
    """

    escala_h: float | None = None
    escala_v: float | None = None
    paso_h: float = 0.0
    paso_v: float = 0.0
    reticula: bool = True
    columnas: int = 3
    renglones: int = 5
    separacion_mm: float = 10.0
    orden: str = "columnas_abajo_arriba"
    marco: bool = True
    sombrear: bool = True
    chaflanes: bool = True

    def __post_init__(self):
        if not 1 <= int(self.columnas) <= 20 or not 1 <= int(self.renglones) <= 50:
            raise ValueError("Una hoja lleva de 1 a 20 columnas y de 1 a 50 secciones por columna.")
        self.columnas, self.renglones = int(self.columnas), int(self.renglones)
        for e in (self.escala_h, self.escala_v):
            if e is not None and not 1 <= e <= 100000:
                raise ValueError("Las escalas de las secciones van de 1:1 a 1:100 000.")
        if self.paso_h < 0 or self.paso_v < 0 or self.separacion_mm < 0:
            raise ValueError("La retícula y la separación no pueden ser negativas.")
        if self.orden not in ORDENES_HOJA:
            raise ValueError("Orden de las secciones desconocido.")


def acomodo(n: int, columnas: int, renglones: int, orden: str = "columnas_abajo_arriba") -> list[tuple[int, int, int]]:
    """(hoja, columna, renglón contado desde arriba) de cada una de `n` secciones."""
    por_hoja = columnas * renglones
    salida = []
    for k in range(n):
        hoja, j = divmod(k, por_hoja)
        if orden == "renglones":
            fila, col = divmod(j, columnas)
        else:
            col, f = divmod(j, renglones)
            fila = renglones - 1 - f if orden == "columnas_abajo_arriba" else f
        salida.append((hoja, col, fila))
    return salida


def regiones(terreno, proyecto) -> list[tuple[str, list[tuple[float, float]]]]:
    """Polígonos de corte («corte») y de terraplén («terraplen») entre el terreno y el proyecto; sus áreas suman lo de `areas_entre`."""
    if len(terreno) < 2 or len(proyecto) < 2:
        return []
    o0, o1 = max(terreno[0][0], proyecto[0][0]), min(terreno[-1][0], proyecto[-1][0])
    if o1 <= o0:
        return []
    d = lambda o: _interp(terreno, o) - _interp(proyecto, o)
    cortes = sorted({o0, o1, *(o for o, _ in terreno if o0 < o < o1), *(o for o, _ in proyecto if o0 < o < o1)})
    xs = []
    for a, b in zip(cortes, cortes[1:]):
        xs.append(a)
        da, db = d(a), d(b)
        if da * db < 0:
            xs.append(a + da / (da - db) * (b - a))
    xs.append(o1)
    grupos: list[list] = []
    actual = None
    for a, b in zip(xs, xs[1:]):
        if b - a < 1e-12:
            continue
        dm = d((a + b) / 2)
        signo = 1 if dm > 1e-12 else (-1 if dm < -1e-12 else 0)
        if signo == 0:
            actual = None
            continue
        if actual is not None and actual[0] == signo and abs(actual[1][-1] - a) < 1e-12:
            actual[1].append(b)
        else:
            actual = [signo, [a, b]]
            grupos.append(actual)
    salida = []
    for signo, offs in grupos:
        poli = [(o, _interp(terreno, o)) for o in offs] + [(o, _interp(proyecto, o)) for o in reversed(offs)]
        limpio = [q for k, q in enumerate(poli) if k == 0 or abs(q[0] - poli[k - 1][0]) > 1e-12 or abs(q[1] - poli[k - 1][1]) > 1e-12]
        if len(limpio) >= 3:
            salida.append(("corte" if signo > 0 else "terraplen", limpio))
    return salida


def area_poligono(pts) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def _ancho_texto(texto: str, h: float) -> float:
    return len(texto) * 0.72 * h


def medidas_secciones(lista: Sequence[Seccion], hoja: HojaSecciones, denominador: float) -> dict:
    """Pasos de la retícula, rango de desplazamientos y alto máximo, iguales para todas las secciones de la orden."""
    from .reticula import paso_bonito

    eh = hoja.escala_h or denominador
    ev = hoja.escala_v or denominador
    con = [s for s in lista if s.terreno]
    if not con:
        return {"paso_h": hoja.paso_h or 5.0, "paso_v": hoja.paso_v or 1.0, "omin": -10.0, "omax": 10.0, "rango": 1.0}
    omin = min(min(s.terreno[0][0], s.proyecto[0][0] if s.proyecto else 0.0) for s in con)
    omax = max(max(s.terreno[-1][0], s.proyecto[-1][0] if s.proyecto else 0.0) for s in con)
    rango = max(max(z for _, z in s.terreno + s.proyecto + s.rasante) - min(z for _, z in s.terreno + s.proyecto + s.rasante) for s in con)
    ph = hoja.paso_h or paso_bonito(omax - omin, 8, 5.0 * eh / 1000.0)
    pv = hoja.paso_v or paso_bonito(rango, 5, 5.0 * ev / 1000.0)
    return {"paso_h": ph, "paso_v": pv, "omin": math.floor(omin / ph + 1e-9) * ph, "omax": math.ceil(omax / ph - 1e-9) * ph, "rango": rango}


def dibujar_secciones(
    lista: Sequence[Seccion],
    origen: tuple[float, float],
    escala: Escala | None = None,
    hoja: HojaSecciones | None = None,
    rotulos: dict[float, str] | None = None,
    estilo: str = "CIVIL",
    decimales: int = 2,
    tramo: str = "",
    opciones: dict | None = None,
) -> Dibujo:
    """Hojas de secciones desde la esquina superior izquierda `origen`: cada sección en su retícula, con su estación de título, los
    desplazamientos y las cotas rotulados, la cota del plano de comparación al pie, terreno y proyecto en sus capas, las áreas de corte y
    terraplén sombreadas y rotuladas. `rotulos`: el tipo de cada estación crítica («PC», «PIV»…), por estación."""
    from .reticula import decimales_de, factor, opciones_xdata, pasos_en

    esc = escala or Escala()
    hoja = hoja or HojaSecciones()
    d = Dibujo()
    for c in CAPAS_SECCION.values():
        d.capa(c)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    if not lista:
        return d
    E = float(esc.denominador)
    eh, ev = hoja.escala_h or E, hoja.escala_v or E
    fh, fv = factor(E, hoja.escala_h), factor(E, hoja.escala_v)
    h = esc.altura("cuadro_dato")
    m = medidas_secciones(lista, hoja, E)
    ph, pv, gmin, gmax = m["paso_h"], m["paso_v"], m["omin"], m["omax"]
    dh = decimales_de(ph)
    rotulos = {round(k, 3): v for k, v in (rotulos or {}).items()}

    def rango_z(s):
        zs = [z for _, z in s.terreno + s.proyecto + s.rasante]
        datum = math.floor(min(zs) / pv + 1e-9) * pv
        return datum, max(math.ceil(max(zs) / pv - 1e-9) * pv, datum + pv)

    rangos = {id(s): rango_z(s) for s in lista if s.terreno}
    alto_max = max(((t - b) * fv for b, t in rangos.values()), default=pv * fv)
    zmax = max((t for _, t in rangos.values()), default=0.0)
    lab_izq = _ancho_texto(f"P.C. {zmax:.{decimales}f}", 0.8 * h) + 1.2 * h
    ancho_ret = (gmax - gmin) * fh
    celda_w = lab_izq + ancho_ret + 2.0 * h
    alto_titulo, alto_pie = 3.4 * h, 4.6 * h
    celda_h = alto_titulo + alto_max + alto_pie
    gap = hoja.separacion_mm * E / 1000.0
    margen, banda = 3.0 * h, 4.5 * h
    hoja_w = 2 * margen + hoja.columnas * celda_w + (hoja.columnas - 1) * gap
    hoja_h = banda + 2 * margen + hoja.renglones * celda_h + (hoja.renglones - 1) * gap
    entre_hojas = max(2 * gap, 10 * h)
    lugares = acomodo(len(lista), hoja.columnas, hoja.renglones, hoja.orden)
    n_hojas = lugares[-1][0] + 1
    ret, txt = CAPAS_SECCION["reticula"].nombre, CAPAS_SECCION["textos"].nombre
    x_origen, y_origen = float(origen[0]), float(origen[1])
    for k in range(n_hojas):
        sx = x_origen + k * (hoja_w + entre_hojas)
        if hoja.marco:
            capa_h = CAPAS_SECCION["hoja"].nombre
            d.agregar(Polilinea(capa_h, vertices=[(sx, y_origen), (sx + hoja_w, y_origen), (sx + hoja_w, y_origen - hoja_h), (sx, y_origen - hoja_h)], cerrada=True))
            d.agregar(Linea(capa_h, inicio=(sx, y_origen - banda, 0.0), fin=(sx + hoja_w, y_origen - banda, 0.0)))
        titulo = f"SECCIONES TRANSVERSALES   {tramo}   ESC. H 1:{eh:g}   V 1:{ev:g}".replace("      ", "   ") + (f"   HOJA {k + 1} DE {n_hojas}" if n_hojas > 1 else "")
        t = Texto(txt, valor=titulo, posicion=(sx + margen, y_origen - banda / 2), altura=esc.altura("cuadro_titulo"), estilo=estilo, alineacion_h="izquierda", alineacion_v="medio")
        if k == 0:
            t.datos = {"TVCIVIL": [(1000, "secciones_titulo"), *opciones_xdata(opciones or {})]}
        d.agregar(t)
    for s, (k, col, fila) in zip(lista, lugares):
        sx = x_origen + k * (hoja_w + entre_hojas)
        izq = sx + margen + col * (celda_w + gap)
        arriba = y_origen - banda - margen - fila * (celda_h + gap)
        g_izq = izq + lab_izq
        g_abajo = arriba - celda_h + alto_pie
        X = lambda o: g_izq + (o - gmin) * fh
        cx = X(0.0) if gmin <= 0.0 <= gmax else X((gmin + gmax) / 2)
        rot = rotulos.get(round(s.estacion, 3), "")
        nombre = formato_estacion(s.estacion) + (f"   {rot}" if rot else "")
        if not s.terreno:
            d.agregar(Texto(txt, valor=nombre, posicion=(cx, arriba - 1.2 * h), altura=1.25 * h, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
            d.agregar(Texto(txt, valor="Sin terreno en esta estación", posicion=(cx, arriba - celda_h / 2), altura=0.9 * h, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
            continue
        datum, tope = rangos[id(s)]
        Y = lambda z: g_abajo + (z - datum) * fv
        g_arriba, g_der = Y(tope), X(gmax)
        cotas = pasos_en(datum, tope, pv)
        offs = pasos_en(gmin, gmax, ph)
        if hoja.reticula:
            for o in offs:
                d.agregar(Linea(ret, inicio=(X(o), g_abajo, 0.0), fin=(X(o), g_arriba, 0.0)))
            for z in cotas:
                d.agregar(Linea(ret, inicio=(g_izq, Y(z), 0.0), fin=(g_der, Y(z), 0.0)))
        else:
            d.agregar(Polilinea(ret, vertices=[(g_izq, g_abajo), (g_der, g_abajo), (g_der, g_arriba), (g_izq, g_arriba)], cerrada=True))
            marca = 0.6 * h
            for o in offs[1:-1]:
                d.agregar(Linea(ret, inicio=(X(o), g_abajo, 0.0), fin=(X(o), g_abajo + marca, 0.0)))
            for z in cotas[1:-1]:
                d.agregar(Linea(ret, inicio=(g_izq, Y(z), 0.0), fin=(g_izq + marca, Y(z), 0.0)))
        if gmin <= 0.0 <= gmax:
            d.agregar(Linea(CAPAS_SECCION["eje"].nombre, inicio=(X(0.0), g_abajo, 0.0), fin=(X(0.0), g_arriba + 0.4 * h, 0.0)))
        # desplazamientos (sin signo; IZQ. y DER. abajo en las orillas) y cotas, la de abajo es el plano de comparación
        cada = max(1, math.ceil((_ancho_texto(f"{max(abs(gmin), abs(gmax)):.{dh}f}", 0.8 * h) + 0.8 * h) / (ph * fh)))
        for j, o in enumerate(offs):
            if round(o / ph) % cada == 0:
                d.agregar(Texto(txt, valor=f"{abs(o):.{dh}f}", posicion=(X(o), g_abajo - 0.9 * h), altura=0.8 * h, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
        if gmin < 0:
            d.agregar(Texto(txt, valor="IZQ.", posicion=(X(gmin), g_abajo - 2.2 * h), altura=0.75 * h, estilo=estilo, alineacion_h="izquierda", alineacion_v="medio"))
        if gmax > 0:
            d.agregar(Texto(txt, valor="DER.", posicion=(g_der, g_abajo - 2.2 * h), altura=0.75 * h, estilo=estilo, alineacion_h="derecha", alineacion_v="medio"))
        cada_z = max(1, math.ceil(1.3 * h / (pv * fv)))
        for j, z in enumerate(cotas):
            if j == 0 or j % cada_z == 0:
                d.agregar(Texto(txt, valor=(f"P.C. {z:.{decimales}f}" if j == 0 else f"{z:.{decimales}f}"), posicion=(g_izq - 0.5 * h, Y(z)), altura=0.8 * h, estilo=estilo,
                                alineacion_h="derecha", alineacion_v="medio"))
        # título y elevaciones en el eje
        d.agregar(Texto(txt, valor=nombre, posicion=(cx, g_arriba + 2.3 * h), altura=1.25 * h, estilo=estilo, alineacion_h="centro", alineacion_v="medio",
                        datos={"TVCIVIL": [(1000, "seccion_estacion"), (1040, float(s.estacion))]}))
        zt = s.z_terreno(0.0)
        zr = _interp(s.rasante, 0.0) if s.rasante else (_interp(s.proyecto, 0.0) if s.proyecto else None)
        partes = ([f"Terreno {zt:.{decimales}f}"] if zt is not None else []) + ([f"Rasante {zr:.{decimales}f}"] if zr is not None else [])
        if partes:
            d.agregar(Texto(txt, valor="   ".join(partes), posicion=(cx, g_arriba + 0.9 * h), altura=0.75 * h, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
        # sombreado de corte y terraplén, debajo de las líneas
        if hoja.sombrear and s.proyecto:
            escala_patron = 1.5 * E / 1000.0 / 3.175
            for tipo, poli in regiones(s.terreno, s.proyecto):
                d.agregar(Sombreado(CAPAS_SECCION[tipo].nombre, contorno=[(X(o), Y(z)) for o, z in poli], solido=False,
                                    patron="ANSI31" if tipo == "corte" else "ANSI37", escala_patron=escala_patron))
        d.agregar(Polilinea(CAPAS_SECCION["terreno"].nombre, vertices=[(X(o), Y(z)) for o, z in s.terreno], datos={"TVCIVIL": [(1000, "seccion_terreno"), (1040, float(s.estacion))]}))
        if s.proyecto:
            d.agregar(Polilinea(CAPAS_SECCION["proyecto"].nombre, vertices=[(X(o), Y(z)) for o, z in s.proyecto], datos={"TVCIVIL": [(1000, "seccion_proyecto"), (1040, float(s.estacion))]}))
        if s.rasante:
            d.agregar(Polilinea(CAPAS_SECCION["rasante"].nombre, vertices=[(X(o), Y(z)) for o, z in s.rasante]))
        if s.proyecto and hoja.chaflanes:
            for (o, z), lado in ((s.proyecto[0], -1), (s.proyecto[-1], 1)):
                if abs(o) < 1e-9:
                    continue
                d.agregar(Texto(txt, valor=f"{abs(o):.2f} / {z:.{decimales}f}", posicion=(X(o) + lado * 0.4 * h, Y(z) + 0.5 * h), altura=0.7 * h, estilo=estilo,
                                alineacion_h="izquierda" if lado > 0 else "derecha", alineacion_v="abajo"))
        if s.proyecto:
            datos = [f"CORTE {s.corte:.{decimales}f} m²", f"TERRAPLÉN {s.terraplen:.{decimales}f} m²"]
            if s.despalme:
                datos.append(f"DESPALME {s.despalme:.{decimales}f} m²")
            if s.peralte:
                datos.append(f"S {s.peralte['izq'] * 100:+.1f} % / {s.peralte['der'] * 100:+.1f} %")
                amp = max(s.peralte.get("amp_izq", 0.0), s.peralte.get("amp_der", 0.0))
                if amp > 0:
                    datos.append(f"AMP. {amp:.2f} m")
            d.agregar(Texto(txt, valor="   ".join(datos), posicion=((g_izq + g_der) / 2, g_abajo - 3.6 * h), altura=0.85 * h, estilo=estilo,
                            alineacion_h="centro", alineacion_v="medio", datos={"TVCIVIL": [(1000, "seccion_areas"), (1040, float(s.estacion)), (1040, float(s.corte)),
                                                                                           (1040, float(s.terraplen))]}))
    return d
