"""KML y KMZ de Google Earth: importar al plano y exportar del plano.

**Importar.** Placemarks con Polygon (anillo exterior e interiores), LineString, LinearRing, Point, MultiGeometry y gx:Track, dentro
de Folder y Document a cualquier profundidad. Las coordenadas (longitud, latitud y altitud en WGS84) se convierten a la zona UTM del
plano; si caen en otra zona se avisa. Los polígonos son polilíneas cerradas (también sus huecos), las líneas polilíneas y los puntos
puntos de Civil o puntos simples. La capa es la de su carpeta o la de su estilo. El nombre queda como texto (opcional) y en XDATA. Un
KMZ se abre aquí (es un ZIP con el KML dentro): sin dependencias.

**Exportar.** Polilíneas cerradas (y, si se pide, abiertas y puntos) de UTM a WGS84 con la zona del plano: cada polígono con su nombre
(un texto dentro de él, su capa o un prefijo con número) y su superficie en m² y ha-a-ca; colores y transparencias de línea y relleno;
KML o KMZ, con la vista inicial sobre los datos (Google Earth abre en su lugar).

Seguridad: un KML con DOCTYPE o entidades se rechaza (nunca se resuelven entidades externas); un KMZ se lee con tope de tamaño.
"""

from __future__ import annotations

import html
import io
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .cuadro import _arco, area_con_arcos
from .entidades import Capa, Dibujo, EstiloTexto, Polilinea, Polilinea3D, PuntoCad, Texto
from .escala import Escala
from .superficie import formato_ha_a_ca, formato_miles

__all__ = ["Geometria", "Marca", "LecturaKml", "leer_kml", "dibujar_kml", "escribir_kml", "ElementoExportar", "aci_de_kml", "MAX_KML"]

APP = "TVCIVIL"
#: Tope del KML ya descomprimido (un KMZ puede traer imágenes: solo se lee el KML).
MAX_KML = 64 * 1024 * 1024
MAX_MARCAS = 50_000
MAX_VERTICES = 2_000_000

CAPA_NOMBRES = Capa("KML_NOMBRES", 7, descripcion="Nombres de lo importado de Google Earth")


@dataclass
class Geometria:
    tipo: str  # "poligono" | "linea" | "punto"
    anillos: list[list[tuple[float, float, float | None]]]  # polígono: [exterior, huecos…]; línea: [vértices]; punto: [[el punto]]


@dataclass
class Marca:
    nombre: str
    descripcion: str
    carpeta: tuple[str, ...]
    estilo: str  # clave del estilo resuelto ("" sin estilo)
    geometrias: list[Geometria] = field(default_factory=list)


@dataclass
class Estilo:
    clave: str
    linea: str | None = None  # aabbggrr
    relleno: str | None = None
    ancho: float | None = None


@dataclass
class LecturaKml:
    nombre: str
    marcas: list[Marca]
    estilos: dict[str, Estilo]
    ignorados: dict[str, int] = field(default_factory=dict)  # elementos sin geometría que se pueda dibujar (Model, GroundOverlay…)
    mal_escritas: int = 0  # coordenadas que no se pudieron leer

    def cuentas(self) -> dict[str, int]:
        c = {"poligonos": 0, "huecos": 0, "lineas": 0, "puntos": 0}
        for m in self.marcas:
            for g in m.geometrias:
                if g.tipo == "poligono":
                    c["poligonos"] += 1
                    c["huecos"] += len(g.anillos) - 1
                elif g.tipo == "linea":
                    c["lineas"] += 1
                else:
                    c["puntos"] += 1
        return c


# --- lectura ---------------------------------------------------------------------------------------------------------------------

def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _hijo(e, nombre):
    for h in e:
        if _local(h.tag) == nombre:
            return h
    return None


def _hijos(e, nombre):
    return [h for h in e if _local(h.tag) == nombre]


def _texto(e, nombre) -> str:
    h = _hijo(e, nombre)
    return (h.text or "").strip() if h is not None else ""


def _kml_de(datos: bytes) -> bytes:
    """El KML: tal cual, o el de dentro del KMZ (doc.kml o el primer .kml, el de menor profundidad)."""
    if datos[:4] == b"PK\x03\x04":
        try:
            z = zipfile.ZipFile(io.BytesIO(datos))
        except zipfile.BadZipFile:
            raise ValueError("El KMZ está dañado.") from None
        kmls = [i for i in z.infolist() if i.filename.lower().endswith(".kml") and not i.is_dir() and "__MACOSX" not in i.filename]
        if not kmls:
            raise ValueError("El KMZ no trae ningún KML.")
        info = next((i for i in kmls if i.filename.lower() == "doc.kml"), None) or min(kmls, key=lambda i: (i.filename.count("/"), i.filename))
        if info.file_size > MAX_KML:
            raise ValueError("El KML dentro del KMZ es demasiado grande.")
        try:
            with z.open(info) as f:
                datos = f.read(MAX_KML + 1)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
            raise ValueError("El KMZ está dañado o cifrado.") from None
        if len(datos) > MAX_KML:
            raise ValueError("El KML dentro del KMZ es demasiado grande.")
    elif len(datos) > MAX_KML:
        raise ValueError("El KML es demasiado grande.")
    return datos


def _coordenadas(texto: str) -> tuple[list[tuple[float, float, float | None]], int]:
    pts, malas = [], 0
    for tupla in re.sub(r"\s*,\s*", ",", texto or "").split():
        partes = tupla.split(",")
        try:
            lon, lat = float(partes[0]), float(partes[1])
            alt = float(partes[2]) if len(partes) > 2 and partes[2] != "" else None
        except (ValueError, IndexError):
            malas += 1
            continue
        if not (math.isfinite(lon) and math.isfinite(lat) and -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0) or (alt is not None and not math.isfinite(alt)):
            malas += 1
            continue
        pts.append((lon, lat, alt))
    return pts, malas


def _sin_repetidos(pts, anillo: bool):
    salida = []
    for p in pts:
        if not salida or abs(p[0] - salida[-1][0]) > 1e-12 or abs(p[1] - salida[-1][1]) > 1e-12:
            salida.append(p)
    if anillo and len(salida) > 1 and abs(salida[0][0] - salida[-1][0]) < 1e-12 and abs(salida[0][1] - salida[-1][1]) < 1e-12:
        salida.pop()
    return salida


def leer_kml(datos: bytes) -> LecturaKml:
    """Lee un KML o KMZ."""
    if not datos:
        raise ValueError("El archivo está vacío.")
    kml = _kml_de(datos)
    cabeza = kml[:4096].upper()
    if b"<!DOCTYPE" in cabeza or b"<!ENTITY" in kml.upper():
        raise ValueError("El KML trae declaraciones que no se aceptan (DOCTYPE o ENTITY).")
    try:
        raiz = ET.fromstring(kml)
    except ET.ParseError:
        raise ValueError("El archivo no es un KML válido.") from None
    if _local(raiz.tag) != "kml" and _local(raiz.tag) not in ("Document", "Folder", "Placemark"):
        raise ValueError("El archivo no es un KML.")
    estilos: dict[str, Estilo] = {}
    mapas: dict[str, str] = {}
    for e in raiz.iter():
        t = _local(e.tag)
        if t == "Style" and e.get("id"):
            estilos[e.get("id")] = _estilo(e, e.get("id"))
        elif t == "StyleMap" and e.get("id"):
            for par in _hijos(e, "Pair"):
                if _texto(par, "key") == "normal":
                    url = _texto(par, "styleUrl")
                    if url:
                        mapas[e.get("id")] = url.split("#")[-1]
                    else:
                        interno = _hijo(par, "Style")
                        if interno is not None:
                            clave = f"{e.get('id')}-normal"
                            estilos[clave] = _estilo(interno, clave)
                            mapas[e.get("id")] = clave
    lectura = LecturaKml("", [], estilos)
    tipo_raiz = _local(raiz.tag)
    primero = raiz
    if tipo_raiz == "kml":
        primero = _hijo(raiz, "Document")
        if primero is None:
            primero = _hijo(raiz, "Folder")
    lectura.nombre = _texto(primero, "name") if primero is not None and tipo_raiz != "Placemark" else ""
    contador = {"vertices": 0}
    estilos_en_linea: dict[tuple, str] = {}

    def resolver(pm) -> str:
        interno = _hijo(pm, "Style")
        if interno is not None:
            est = _estilo(interno, "")
            llave = (est.linea, est.relleno)
            if llave not in estilos_en_linea:
                clave = f"Estilo {len(estilos_en_linea) + 1}"
                est.clave = clave
                estilos[clave] = est
                estilos_en_linea[llave] = clave
            return estilos_en_linea[llave]
        url = _texto(pm, "styleUrl").split("#")[-1]
        for _ in range(4):
            if url in mapas:
                url = mapas[url]
        return url if url in estilos else (url or "")

    def recorrer(e, carpeta: tuple[str, ...], superior: bool):
        # `superior`: los hijos de <kml>; su Document es el archivo mismo, no una carpeta
        for h in e:
            t = _local(h.tag)
            if t in ("Folder", "Document"):
                nombre = _texto(h, "name") or ("Carpeta" if t == "Folder" else "Documento")
                recorrer(h, carpeta if (superior and t == "Document") else carpeta + (nombre[:80],), False)
            elif t == "Placemark":
                m = Marca(_texto(h, "name"), _descripcion(_texto(h, "description")), carpeta, resolver(h))
                for g in h:
                    _geometria(g, m, lectura, contador)
                if m.geometrias:
                    lectura.marcas.append(m)
                    if len(lectura.marcas) > MAX_MARCAS:
                        raise ValueError(f"El archivo trae más de {MAX_MARCAS:,} elementos.".replace(",", " "))
                elif not any(_local(g.tag) in ("Point", "LineString", "LinearRing", "Polygon", "MultiGeometry", "Track", "MultiTrack") for g in h):
                    clave = "Model" if any(_local(g.tag) == "Model" for g in h) else "sin geometría"
                    lectura.ignorados[clave] = lectura.ignorados.get(clave, 0) + 1
            elif t in ("GroundOverlay", "ScreenOverlay", "PhotoOverlay", "NetworkLink"):
                lectura.ignorados[t] = lectura.ignorados.get(t, 0) + 1

    if tipo_raiz == "kml":
        recorrer(raiz, (), True)
    elif tipo_raiz == "Placemark":
        recorrer([raiz], (), True)
    else:
        recorrer(raiz, (), False)
    return lectura


def _estilo(e, clave: str) -> Estilo:
    est = Estilo(clave)
    ls, ps = _hijo(e, "LineStyle"), _hijo(e, "PolyStyle")
    if ls is not None:
        est.linea = _color(_texto(ls, "color"))
        try:
            est.ancho = float(_texto(ls, "width")) if _texto(ls, "width") else None
        except ValueError:
            est.ancho = None
    if ps is not None:
        est.relleno = _color(_texto(ps, "color"))
    if est.linea is None and est.relleno is None:
        ic = _hijo(e, "IconStyle")
        if ic is not None:
            est.linea = _color(_texto(ic, "color"))
    return est


def _color(t: str) -> str | None:
    t = (t or "").strip().lstrip("#").lower()
    return t if re.fullmatch(r"[0-9a-f]{8}", t) else None


def _descripcion(t: str) -> str:
    t = re.sub(r"<br\s*/?>", " ", t or "", flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()[:250]


def _geometria(g, m: Marca, lectura: LecturaKml, contador: dict) -> None:
    t = _local(g.tag)

    def coords(e, anillo=False):
        pts, malas = _coordenadas(_texto(e, "coordinates"))
        lectura.mal_escritas += malas
        pts = _sin_repetidos(pts, anillo)
        contador["vertices"] += len(pts)
        if contador["vertices"] > MAX_VERTICES:
            raise ValueError("El archivo trae demasiados vértices.")
        return pts

    if t == "Point":
        p = coords(g)
        if p:
            m.geometrias.append(Geometria("punto", [p[:1]]))
    elif t == "LineString":
        p = coords(g)
        if len(p) >= 2:
            m.geometrias.append(Geometria("linea", [p]))
    elif t == "LinearRing":
        p = coords(g, True)
        if len(p) >= 3:
            m.geometrias.append(Geometria("poligono", [p]))
    elif t == "Polygon":
        anillos = []
        ext = _hijo(g, "outerBoundaryIs")
        anillo = _hijo(ext, "LinearRing") if ext is not None else None
        if anillo is None:
            return
        p = coords(anillo, True)
        if len(p) < 3:
            return
        anillos.append(p)
        for inner in _hijos(g, "innerBoundaryIs"):
            for r in _hijos(inner, "LinearRing"):
                q = coords(r, True)
                if len(q) >= 3:
                    anillos.append(q)
        m.geometrias.append(Geometria("poligono", anillos))
    elif t == "MultiGeometry":
        for h in g:
            _geometria(h, m, lectura, contador)
    elif t == "MultiTrack":
        for h in g:
            _geometria(h, m, lectura, contador)
    elif t == "Track":
        pts = []
        for c in g:
            if _local(c.tag) == "coord":
                partes = (c.text or "").split()
                try:
                    pts.append((float(partes[0]), float(partes[1]), float(partes[2]) if len(partes) > 2 else None))
                except (ValueError, IndexError):
                    lectura.mal_escritas += 1
        pts = _sin_repetidos(pts, False)
        if len(pts) >= 2:
            m.geometrias.append(Geometria("linea", [pts]))


# --- al plano ----------------------------------------------------------------------------------------------------------------------

_PROHIBIDOS = re.compile(r'[<>/\\":;?*|=,`]+')
_RESERVADAS = {"0", "DEFPOINTS"}


def nombre_capa(texto: str, reservadas: Iterable[str] = ()) -> str:
    """Un nombre de capa válido en DWG/DXF a partir de un nombre de carpeta o estilo."""
    t = _PROHIBIDOS.sub("_", re.sub(r"\s+", " ", texto or "")).strip(" ._")[:80]
    if not t:
        return "KML"
    if t.upper() in _RESERVADAS or t.upper() in {r.upper() for r in reservadas}:
        t = f"KML-{t}"
    return t


def aci_de_kml(color: str | None) -> int:
    """Color ACI más cercano a un color KML (aabbggrr). 7 si no hay color."""
    if not color:
        return 7
    r, g, b = int(color[6:8], 16), int(color[4:6], 16), int(color[2:4], 16)
    try:
        from ezdxf.colors import DXF_DEFAULT_COLORS
        paleta = [(i, (DXF_DEFAULT_COLORS[i] >> 16) & 255, (DXF_DEFAULT_COLORS[i] >> 8) & 255, DXF_DEFAULT_COLORS[i] & 255) for i in range(1, 256)]
    except ImportError:  # pragma: no cover - el motor siempre corre con ezdxf en Herramientas
        paleta = [(1, 255, 0, 0), (2, 255, 255, 0), (3, 0, 255, 0), (4, 0, 255, 255), (5, 0, 0, 255), (6, 255, 0, 255), (7, 255, 255, 255), (8, 128, 128, 128)]
    i = min(paleta, key=lambda c: (c[1] - r) ** 2 + (c[2] - g) ** 2 + (c[3] - b) ** 2)[0]
    return 7 if i == 255 or (r, g, b) == (255, 255, 255) or (r, g, b) == (0, 0, 0) else i


@dataclass
class ResultadoKml:
    dibujo: Dibujo
    puntos_civil: list  # tvcivil.puntos.Punto, para dibujar como puntos de Civil
    cuentas: dict[str, int]
    capas: list[str]
    zonas: dict[int, int]
    avisos: list[str]


def dibujar_kml(
    lectura: LecturaKml,
    zona: int,
    hemisferio: str = "N",
    escala: Escala | None = None,
    capas_por: str = "carpeta",
    puntos_como: str = "terreno",
    nombres: bool = True,
    altitudes: bool = True,
    numero_libre: int = 1,
    reservadas: Iterable[str] = (),
) -> ResultadoKml:
    """Lo leído, en la zona UTM del plano.

    `capas_por`: "carpeta" (la ruta de carpetas) o "estilo". `puntos_como`: "terreno" o "proyecto" (puntos de Civil) o "simples"
    (POINT). `altitudes`: la altitud de cada vértice como elevación (0 si no trae); si no, todo en 0.
    """
    from .geodesia import geo_a_utm, zona_de_longitud
    from .puntos import Punto

    if capas_por not in ("carpeta", "estilo"):
        raise ValueError("Elige la capa: por carpeta o por estilo.")
    if puntos_como not in ("terreno", "proyecto", "simples"):
        raise ValueError("Elige cómo van los puntos.")
    esc = escala or Escala()
    d = Dibujo()
    d.estilo(EstiloTexto("CIVIL", "romans.shx", 0.85))
    h = esc.altura("cuadro_dato")
    zonas: dict[int, int] = {}
    reservadas = set(reservadas)
    colores: dict[str, dict[int, int]] = {}
    capas_usadas: dict[str, Capa] = {}
    puntos_civil = []
    siguiente = max(1, int(numero_libre or 1))
    cuentas = {"poligonos": 0, "huecos": 0, "lineas": 0, "puntos": 0, "nombres": 0}
    otro_hemisferio = 0

    def utm(p):
        nonlocal otro_hemisferio
        lon, lat, alt = p
        zl = zona_de_longitud(lon)
        zonas[zl] = zonas.get(zl, 0) + 1
        if (lat < 0) != (hemisferio == "S"):
            otro_hemisferio += 1
        x, y = geo_a_utm(lat, lon, zona, hemisferio)
        return x, y, (float(alt) if (altitudes and alt is not None) else 0.0)

    def capa_de(m: Marca) -> str:
        if capas_por == "carpeta":
            nombre = nombre_capa(" - ".join(m.carpeta) if m.carpeta else (lectura.nombre or "KML"), reservadas)
        else:
            nombre = nombre_capa(m.estilo or "Sin estilo", reservadas)
        est = lectura.estilos.get(m.estilo)
        aci = aci_de_kml((est.linea or est.relleno) if est else None)
        colores.setdefault(nombre, {})
        colores[nombre][aci] = colores[nombre].get(aci, 0) + 1
        return nombre

    def xdata(m: Marca, que: str):
        return {APP: [(1000, "kml"), (1000, que), (1000, (m.nombre or "")[:250]), (1000, " / ".join(m.carpeta)[:250])]}

    def linea(capa, vs, cerrada, datos):
        zs = {round(v[2], 6) for v in vs}
        if len(zs) <= 1:
            return Polilinea(capa, vertices=[(v[0], v[1]) for v in vs], cerrada=cerrada, elevacion=vs[0][2] if vs else 0.0, datos=datos)
        return Polilinea3D(capa, vertices=[tuple(v) for v in vs], cerrada=cerrada, datos=datos)

    for m in lectura.marcas:
        capa = capa_de(m)
        capas_usadas.setdefault(capa, Capa(capa, 7, descripcion="Importado de Google Earth"))
        for g in m.geometrias:
            if g.tipo == "poligono":
                anillos = [[utm(p) for p in r] for r in g.anillos]
                d.agregar(linea(capa, anillos[0], True, xdata(m, "poligono")))
                for r in anillos[1:]:
                    d.agregar(linea(capa, r, True, xdata(m, "hueco")))
                cuentas["poligonos"] += 1
                cuentas["huecos"] += len(anillos) - 1
                if nombres and m.nombre:
                    import shapely

                    try:
                        pol = shapely.Polygon([v[:2] for v in anillos[0]], [[v[:2] for v in r] for r in anillos[1:]])
                        q = pol.representative_point() if pol.is_valid else shapely.Polygon([v[:2] for v in anillos[0]]).centroid
                        pos = (q.x, q.y)
                    except Exception:  # noqa: BLE001 - un anillo raro: el nombre va al centro de sus vértices
                        pos = (sum(v[0] for v in anillos[0]) / len(anillos[0]), sum(v[1] for v in anillos[0]) / len(anillos[0]))
                    d.agregar(Texto(d.capa(CAPA_NOMBRES), valor=m.nombre[:250], posicion=pos, altura=h, estilo="CIVIL", alineacion_h="centro", alineacion_v="medio",
                                    datos=xdata(m, "nombre")))
                    cuentas["nombres"] += 1
            elif g.tipo == "linea":
                vs = [utm(p) for p in g.anillos[0]]
                d.agregar(linea(capa, vs, False, xdata(m, "linea")))
                cuentas["lineas"] += 1
                if nombres and m.nombre:
                    k = len(vs) // 2
                    a, b = vs[max(0, k - 1)], vs[k]
                    ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                    if ang > 90 or ang < -90:
                        ang += 180
                    d.agregar(Texto(d.capa(CAPA_NOMBRES), valor=m.nombre[:250], posicion=((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), altura=h, rotacion=ang % 360,
                                    estilo="CIVIL", alineacion_h="centro", alineacion_v="abajo", datos=xdata(m, "nombre")))
                    cuentas["nombres"] += 1
            else:
                x, y, z = utm(g.anillos[0][0])
                cuentas["puntos"] += 1
                if puntos_como == "simples":
                    d.agregar(PuntoCad(capa, posicion=(x, y, z), datos=xdata(m, "punto")))
                    if nombres and m.nombre:
                        d.agregar(Texto(d.capa(CAPA_NOMBRES), valor=m.nombre[:250], posicion=(x + 0.6 * h, y + 0.3 * h), altura=h, estilo="CIVIL", datos=xdata(m, "nombre")))
                        cuentas["nombres"] += 1
                else:
                    puntos_civil.append(Punto(str(siguiente), x, y, z, (m.nombre or "")[:120], puntos_como))
                    siguiente += 1
    for nombre, capa in capas_usadas.items():
        cuenta = colores.get(nombre) or {}
        capa.color = max(cuenta, key=cuenta.get) if cuenta else 7
        if any(isinstance(e, (Polilinea, Polilinea3D, PuntoCad)) and e.capa == nombre for e in d.entidades):
            d.capas[nombre] = capa
    avisos = []
    otras = {k: v for k, v in zonas.items() if k != zona}
    if otras:
        z, n = max(otras.items(), key=lambda kv: kv[1])
        total = sum(zonas.values())
        avisos.append(f"{'Todos los' if sum(otras.values()) == total else f'{sum(otras.values())} de {total}'} vértices caen en la zona {z}"
                      f"{' (y otras)' if len(otras) > 1 else ''}; se convirtieron a la zona {zona} del plano.")
    if otro_hemisferio:
        avisos.append(f"{otro_hemisferio} vértices están en el otro hemisferio.")
    if lectura.mal_escritas:
        avisos.append(f"{lectura.mal_escritas} coordenadas mal escritas no se leyeron.")
    for k, v in lectura.ignorados.items():
        avisos.append(f"{v} {'elemento' if v == 1 else 'elementos'} sin geometría para dibujar ({k}) no se importaron.")
    return ResultadoKml(d, puntos_civil, cuentas, sorted(capas_usadas), zonas, avisos)


# --- exportar ------------------------------------------------------------------------------------------------------------------------

@dataclass
class ElementoExportar:
    tipo: str  # "poligono" | "linea" | "punto"
    vertices: list[tuple[float, ...]]  # UTM (x, y[, z])
    combas: list[float] | None = None
    nombre: str = ""
    descripcion: str = ""
    capa: str = ""
    superficie: float | None = None


def densificar(vertices: Sequence[Sequence[float]], combas: Sequence[float] | None, cerrada: bool, paso_grados: float = 2.0) -> list[tuple[float, float]]:
    """Vértices con los arcos (combas) partidos en cuerdas de a lo más `paso_grados` de ángulo central."""
    v = [(float(p[0]), float(p[1])) for p in vertices]
    if not combas or not any(combas):
        return v
    n = len(v)
    salida = []
    tramos = n if cerrada else n - 1
    for i in range(n):
        a = v[i]
        salida.append(a)
        if i >= tramos:
            continue
        b = v[(i + 1) % n]
        c = combas[i] if i < len(combas) else 0.0
        if c and (a != b):
            theta, r, centro, _ = _arco(a, b, c)
            k = max(2, math.ceil(abs(math.degrees(theta)) / paso_grados))
            ang0 = math.atan2(a[1] - centro[1], a[0] - centro[0])
            for j in range(1, k):
                t = ang0 + theta * j / k
                salida.append((centro[0] + r * math.cos(t), centro[1] + r * math.sin(t)))
    return salida


def _kml_color(hex_rgb: str, opacidad: float) -> str:
    t = (hex_rgb or "#ff0000").lstrip("#").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", t):
        raise ValueError("Los colores van como #rrggbb.")
    a = max(0, min(255, round(float(opacidad) * 255)))
    return f"{a:02x}{t[4:6]}{t[2:4]}{t[0:2]}"


def _esc(t: str) -> str:
    return html.escape(t or "", quote=True)


def escribir_kml(
    elementos: Sequence[ElementoExportar],
    zona: int,
    hemisferio: str = "N",
    nombre: str = "Plano",
    linea_color: str = "#ffff00",
    linea_ancho: float = 2.0,
    linea_opacidad: float = 1.0,
    relleno_color: str = "#ffff00",
    relleno_opacidad: float = 0.25,
    kmz: bool = False,
) -> bytes:
    """KML (o KMZ) con los elementos en WGS84, cada tipo en su carpeta, y la vista inicial sobre ellos."""
    from .geodesia import datos_geo

    if not elementos:
        raise ValueError("No hay nada que exportar.")
    if not 0 <= linea_opacidad <= 1 or not 0 <= relleno_opacidad <= 1:
        raise ValueError("La opacidad va de 0 a 100 %.")
    if not 0.5 <= linea_ancho <= 20:
        raise ValueError("El ancho de línea va de 0.5 a 20.")
    lc, rc = _kml_color(linea_color, linea_opacidad), _kml_color(relleno_color, relleno_opacidad)

    def geo(x, y):
        g = datos_geo(x, y, zona, hemisferio)
        return g.longitud, g.latitud

    lons, lats = [], []
    carpetas: dict[str, list[str]] = {"poligono": [], "linea": [], "punto": []}
    for e in elementos:
        if e.tipo == "punto":
            lon, lat = geo(*e.vertices[0][:2])
            lons.append(lon)
            lats.append(lat)
            coords = f"{lon:.9f},{lat:.9f},0"
            cuerpo = f"<Point><coordinates>{coords}</coordinates></Point>"
            estilo = "#punto"
        else:
            cerrada = e.tipo == "poligono"
            pts = densificar(e.vertices, e.combas, cerrada)
            if cerrada:
                # el anillo exterior va en sentido antihorario y cerrado (el primer vértice se repite al final)
                area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
                if area < 0:
                    pts = pts[::-1]
                pts = pts + [pts[0]]
            g = [geo(x, y) for x, y in pts]
            lons += [p[0] for p in g]
            lats += [p[1] for p in g]
            coords = " ".join(f"{lon:.9f},{lat:.9f},0" for lon, lat in g)
            if cerrada:
                cuerpo = f"<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon>"
                estilo = "#poligono"
            else:
                cuerpo = f"<LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString>"
                estilo = "#linea"
        datos = [("capa", e.capa)] if e.capa else []
        desc = e.descripcion
        if e.superficie is not None:
            datos += [("superficie_m2", f"{e.superficie:.3f}"), ("superficie_ha_a_ca", formato_ha_a_ca(e.superficie, 2, ""))]
        extendido = "".join(f'<Data name="{_esc(k)}"><value>{_esc(v)}</value></Data>' for k, v in datos)
        carpetas[e.tipo].append(
            f"<Placemark><name>{_esc(e.nombre)}</name>"
            + (f"<description><![CDATA[{desc.replace(']]>', ']] >')}]]></description>" if desc else "")
            + f"<styleUrl>{estilo}</styleUrl>"
            + (f"<ExtendedData>{extendido}</ExtendedData>" if extendido else "")
            + cuerpo + "</Placemark>")
    # vista inicial: el centro de los datos y una distancia que los abarca
    lon0, lat0 = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
    ancho_m = max((max(lons) - min(lons)) * 111_320 * math.cos(math.radians(lat0)), (max(lats) - min(lats)) * 110_574)
    distancia = max(300.0, ancho_m * 1.8)
    nombres = {"poligono": "Polígonos", "linea": "Líneas", "punto": "Puntos"}
    partes = [
        '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>',
        f"<name>{_esc(nombre)}</name><open>1</open>",
        f"<LookAt><longitude>{lon0:.9f}</longitude><latitude>{lat0:.9f}</latitude><altitude>0</altitude><heading>0</heading><tilt>0</tilt>"
        f"<range>{distancia:.1f}</range><altitudeMode>relativeToGround</altitudeMode></LookAt>",
        f'<Style id="poligono"><LineStyle><color>{lc}</color><width>{linea_ancho:g}</width></LineStyle><PolyStyle><color>{rc}</color>'
        f"<fill>{1 if relleno_opacidad > 0 else 0}</fill><outline>1</outline></PolyStyle></Style>",
        f'<Style id="linea"><LineStyle><color>{lc}</color><width>{linea_ancho:g}</width></LineStyle></Style>',
        f'<Style id="punto"><IconStyle><color>{lc}</color><scale>0.8</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle>'
        "<LabelStyle><scale>0.8</scale></LabelStyle></Style>",
    ]
    for tipo, lista in carpetas.items():
        if lista:
            partes.append(f"<Folder><name>{nombres[tipo]}</name>" + "\n".join(lista) + "</Folder>")
    partes.append("</Document>\n</kml>\n")
    datos = "\n".join(partes).encode("utf-8")
    if not kmz:
        return datos
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", datos)
    return salida.getvalue()


def descripcion_superficie(m2: float) -> str:
    return f"Superficie: {formato_miles(m2, 3, ',')} m²<br>{formato_ha_a_ca(m2)}"


def superficie(vertices: Sequence[Sequence[float]], combas: Sequence[float] | None) -> float:
    """Superficie exacta (con los arcos) de una polilínea cerrada, en m²."""
    return abs(area_con_arcos([(float(p[0]), float(p[1])) for p in vertices], list(combas) if combas else None))
