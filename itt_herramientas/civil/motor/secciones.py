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
from .entidades import Capa, Dibujo, EstiloTexto, Linea, Polilinea, Texto
from .escala import Escala
from .perfiles import Muestreador, Rasante

__all__ = ["Plantilla", "PlantillaCarretera", "Seccion", "seccion_terreno", "aplicar_plantilla", "secciones", "areas_entre", "dibujar_secciones", "CAPAS_SECCION"]

CAPAS_SECCION = {
    "terreno": Capa("SECCION_TERRENO", 3, grosor_mm=0.25, descripcion="Terreno en las secciones"),
    "proyecto": Capa("SECCION_PROYECTO", 1, grosor_mm=0.35, descripcion="Sección de proyecto (subrasante y taludes)"),
    "rasante": Capa("SECCION_RASANTE", 5, grosor_mm=0.25, descripcion="Superficie terminada de la carretera"),
    "reticula": Capa("SECCION_RETICULA", 8, descripcion="Marco y eje de las secciones"),
    "textos": Capa("SECCION_TEXTOS", 7, descripcion="Estación y áreas de las secciones"),
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
              plantilla: Plantilla | PlantillaCarretera | None = None, tin_proyecto=None) -> list[Seccion]:
    """Secciones del terreno en las estaciones. El proyecto sale de la rasante con su plantilla o, con `tin_proyecto`, de la superficie
    de proyecto (su triangulación): la sección de proyecto es esa superficie cortada en la misma línea, y las áreas, entre las dos."""
    if rasante is not None and tin_proyecto is not None:
        raise ValueError("El proyecto sale de la rasante o de la triangulación de proyecto, no de las dos.")
    m = Muestreador(tin)
    mp = Muestreador(tin_proyecto) if tin_proyecto is not None else None
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
        if mp is not None:
            s.proyecto = seccion_terreno(mp, eje, e, izq, der).terreno
            if len(s.proyecto) < 2:
                s.proyecto = []
                s.avisos.append(f"{formato_estacion(s.estacion)}: fuera de la triangulación de proyecto.")
            s.corte, s.terraplen = areas_entre(s.terreno, s.proyecto)
        elif rasante is not None:
            if isinstance(plantilla, PlantillaCarretera):
                aplicar_plantilla_carretera(s, rasante.elevacion(e), plantilla, peraltes.en(e) if peraltes else None)
            else:
                aplicar_plantilla(s, rasante.elevacion(e), plantilla or Plantilla())
        salida.append(s)
    return salida


def dibujar_secciones(
    lista: Sequence[Seccion],
    origen: tuple[float, float],
    escala: Escala | None = None,
    exageracion: float = 1.0,
    columnas: int = 4,
    estilo: str = "CIVIL",
    decimales: int = 2,
) -> Dibujo:
    """Rejilla de secciones (de arriba abajo y de izquierda a derecha) desde la esquina superior izquierda `origen`."""
    esc = escala or Escala()
    d = Dibujo()
    for c in CAPAS_SECCION.values():
        d.capa(c)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    h = esc.altura("cuadro_dato")
    if not lista:
        return d
    ancho = max(s.terreno[-1][0] - s.terreno[0][0] for s in lista if s.terreno) if any(s.terreno for s in lista) else 10.0
    rango = max((max(z for _, z in s.terreno + s.proyecto) - min(z for _, z in s.terreno + s.proyecto)) for s in lista if s.terreno) if any(s.terreno for s in lista) else 1.0
    caja_w = ancho + 8 * h
    caja_h = rango * exageracion + 8 * h
    filas = math.ceil(len(lista) / columnas)
    for k, s in enumerate(lista):
        col, fila = divmod(k, filas)
        cx = origen[0] + col * caja_w + caja_w / 2
        top = origen[1] - fila * caja_h
        pts = s.terreno + s.proyecto
        if not s.terreno:
            d.agregar(Texto(CAPAS_SECCION["textos"].nombre, valor=f"{formato_estacion(s.estacion)}  sin terreno", posicion=(cx, top - caja_h / 2), altura=h, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
            continue
        zmin = min(z for _, z in pts)
        base = top - caja_h + 4 * h
        X = lambda o: cx + o
        Y = lambda z: base + (z - zmin) * exageracion
        ret, txt = CAPAS_SECCION["reticula"].nombre, CAPAS_SECCION["textos"].nombre
        d.agregar(Linea(ret, inicio=(cx, base - h, 0.0), fin=(cx, top - 2.5 * h, 0.0)))
        d.agregar(Polilinea(CAPAS_SECCION["terreno"].nombre, vertices=[(X(o), Y(z)) for o, z in s.terreno], datos={"TVCIVIL": [(1000, "seccion_terreno"), (1040, float(s.estacion))]}))
        if s.proyecto:
            d.agregar(Polilinea(CAPAS_SECCION["proyecto"].nombre, vertices=[(X(o), Y(z)) for o, z in s.proyecto], datos={"TVCIVIL": [(1000, "seccion_proyecto"), (1040, float(s.estacion))]}))
        if s.rasante:
            d.agregar(Polilinea(CAPAS_SECCION["rasante"].nombre, vertices=[(X(o), Y(z)) for o, z in s.rasante]))
        d.agregar(Texto(txt, valor=formato_estacion(s.estacion), posicion=(cx, top - 1.5 * h), altura=h * 1.2, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
        if s.proyecto:
            d.agregar(Texto(txt, valor=f"Corte {s.corte:.{decimales}f} m²   Terraplén {s.terraplen:.{decimales}f} m²" + (f"   Despalme {s.despalme:.{decimales}f} m²" if s.despalme else "")
                            + (f"   S {s.peralte['izq'] * 100:+.1f} % / {s.peralte['der'] * 100:+.1f} %" if s.peralte else "")
                            + (f"   Amp. {max(s.peralte['amp_izq'], s.peralte['amp_der']):.2f} m" if s.peralte and max(s.peralte['amp_izq'], s.peralte['amp_der']) > 0 else ""), posicion=(cx, base - 2.5 * h), altura=h * 0.9, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
            ze = _interp(s.proyecto, 0.0)
            if ze is not None:
                d.agregar(Texto(txt, valor=f"{ze:.{decimales}f}", posicion=(cx + h * 0.5, Y(ze) + h), altura=h * 0.8, estilo=estilo, alineacion_h="izquierda", alineacion_v="abajo"))
    return d
