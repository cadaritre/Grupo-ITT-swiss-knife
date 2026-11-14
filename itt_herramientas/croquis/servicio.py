"""Croquis de ubicación (herramientas-web.md §4.3): validación del proyecto, sincronización, vectorización, PDF y DXF.

La lógica de mapa, vectorización, PDF y DXF es la del escritorio (`dominio.location_sketch`, `osm_vector`, `terrain_contours`,
`dxf_export`); aquí solo se decide de quién es cada croquis, cuándo valen los vectores y cómo se corre en segundo plano.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import tempfile
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path

from django.utils import timezone
from tvplataforma.tareas import cancelada, progreso

from ..comun import capturas, errores, perfiles, sincronizacion
from ..dominio import branding, geospatial_converter as gc, location_sketch as ls, osm_vector, terrain_contours
from ..models import Croquis, Perfil

LIMITE_KM2 = 50.0                      # web; arriba: «Exportar proyecto para el módulo local» (el escritorio llega a 200 km²)
MAX_VERTICES = 500
CONTORNOS = ("Automática", "1 m", "2 m", "5 m", "10 m", "20 m", "50 m")
GEOMETRIAS = ("Polígono", "Rectángulo")
_FECHA = re.compile(r"^\d{2}/\d{2}/\d{4}$")


class CroquisInvalido(ValueError):
    """Los datos o la operación no son válidos (el mensaje se muestra a la persona)."""


class SinVectores(CroquisInvalido):
    """Faltan los vectores o ya no valen: la solución es vectorizar (409)."""


# -- Validación -----------------------------------------------------------------------------------------------------------

def _texto(valor, nombre: str, maximo: int) -> str:
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise CroquisInvalido(f"«{nombre}» debe ser texto.")
    if len(valor) > maximo:
        raise CroquisInvalido(f"«{nombre}» es demasiado largo (máximo {maximo} caracteres).")
    return valor.replace("\x00", "")


def _coordenada(valor, nombre: str, limite: float) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) or abs(valor) > limite:
        raise CroquisInvalido(f"«{nombre}» no es una coordenada válida.")
    return round(float(valor), 7)


def validar(crudo) -> dict:
    """JSON del navegador -> dict normalizado (el `SketchData` del escritorio sin `features`)."""
    if not isinstance(crudo, dict):
        raise CroquisInvalido("El croquis debe ser un objeto JSON.")
    base = ls.SketchData()
    puntos = crudo.get("points", [])
    if not isinstance(puntos, list) or len(puntos) > MAX_VERTICES:
        raise CroquisInvalido(f"Un croquis admite hasta {MAX_VERTICES} vértices.")
    salida_puntos = []
    for n, p in enumerate(puntos, 1):
        if not isinstance(p, dict):
            raise CroquisInvalido(f"El vértice {n} no es válido.")
        salida_puntos.append({"name": _texto(p.get("name", f"V{n}"), f"nombre del vértice {n}", 60), "latitude": _coordenada(p.get("latitude"), f"latitud del vértice {n}", 85.0511),
                              "longitude": _coordenada(p.get("longitude"), f"longitud del vértice {n}", 180), "description": _texto(p.get("description", ""), f"descripción del vértice {n}", 200)})
    capa = crudo.get("map_layer", base.map_layer)
    if capa not in ls.MAP_LAYERS:
        raise CroquisInvalido("La capa de mapa no es válida.")
    contornos = crudo.get("contour_interval", base.contour_interval)
    if contornos not in CONTORNOS:
        raise CroquisInvalido("La equidistancia de curvas no es válida.")
    geometria = crudo.get("geometry", base.geometry)
    if geometria not in GEOMETRIAS:
        raise CroquisInvalido("La geometría debe ser Polígono o Rectángulo.")
    fecha = _texto(crudo.get("sketch_date", base.sketch_date), "fecha", 10)
    if fecha and not _FECHA.match(fecha):
        raise CroquisInvalido("La fecha debe ser dd/mm/aaaa.")
    visibilidad = ls.default_layer_visibility()
    recibida = crudo.get("layer_visibility", {})
    if not isinstance(recibida, dict):
        raise CroquisInvalido("La visibilidad de capas no es válida.")
    visibilidad.update({str(k): bool(v) for k, v in recibida.items() if k in visibilidad})
    return {"title": _texto(crudo.get("title", base.title), "título", 200), "client": _texto(crudo.get("client", ""), "cliente", 200), "project": _texto(crudo.get("project", ""), "proyecto", 200),
            "location": _texto(crudo.get("location", ""), "localización", 200), "sketch_date": fecha, "geometry": geometria, "map_layer": capa, "contour_interval": contornos,
            "layer_visibility": visibilidad, "notes": _texto(crudo.get("notes", ""), "notas", 3000), "points": salida_puntos, "version": base.version}


def area_km2(datos: dict) -> float:
    return osm_vector.selection_area_km2([ls.SketchPoint(p["name"], p["latitude"], p["longitude"]) for p in datos["points"]])


def fuente(datos: dict) -> str:
    """Huella de lo que determina los vectores (área, capa y curvas): si cambia, los vectores ya no valen."""
    estado = [[[round(p["latitude"], 7), round(p["longitude"], 7)] for p in datos["points"]], datos["map_layer"], datos["contour_interval"]]
    return hashlib.sha256(json.dumps(estado, separators=(",", ":")).encode()).hexdigest()


# -- Sincronización --------------------------------------------------------------------------------------------------------

def _aplicar(c: Croquis, datos: dict, perfil: str) -> None:
    c.datos, c.perfil, c.titulo, c.actualizada = datos, perfil, datos["title"][:200], timezone.now()


def sincronizar(usuario, id_cliente: uuid.UUID, servidor_id: uuid.UUID | None, revision_base: int | None, datos: dict, perfil: str) -> dict:
    nuevos = validar(datos)

    def copia(obj):
        _aplicar(obj, dict(nuevos, title=(nuevos["title"].rstrip() + " (desde el teléfono)").strip()), perfil)

    return sincronizacion.sincronizar(Croquis, usuario, id_cliente, servidor_id, revision_base, nuevos,
                                      aplicar=lambda obj: _aplicar(obj, nuevos, perfil), aplicar_copia=copia, error=CroquisInvalido)


def estado_vectores(c: Croquis) -> str:
    if not c.vectores_ruta:
        return "ninguno"
    return "vigentes" if c.vectores_fuente == fuente(c.datos) else "obsoletos"


def resumen_fila(c: Croquis) -> dict:
    return {"id": str(c.id), "id_cliente": str(c.id_cliente) if c.id_cliente else None, "revision": c.revision, "titulo": c.titulo, "actualizada": c.actualizada.isoformat(),
            "copia": c.copia_de_id is not None, "perfil": c.perfil, "vectores": estado_vectores(c), "resumen": c.vectores_resumen,
            "vectorizado": c.vectorizado.isoformat() if c.vectorizado else None, "area_km2": round(area_km2(c.datos), 3),
            "pdf_al_dia": bool(c.salida_ruta) and c.salida_fuente == _huella_salida(c)}


def _huella_salida(c: Croquis) -> str:
    return hashlib.sha256((json.dumps(c.datos, sort_keys=True) + c.vectores_fuente + (c.vectorizado.isoformat() if c.vectorizado else "")).encode()).hexdigest()


# -- Vectores -------------------------------------------------------------------------------------------------------------------

def _carpeta(c: Croquis) -> Path:
    return capturas.raiz() / "croquis" / str(c.cuenta_id) / str(c.id)


def _puntos(datos: dict) -> list[ls.SketchPoint]:
    return [ls.SketchPoint(p["name"], p["latitude"], p["longitude"], p["description"]) for p in datos["points"]]


def verificar_area(datos: dict) -> float:
    if len(datos["points"]) < 3:
        raise CroquisInvalido("Elige Polígono o Rectángulo y delimita el área sobre el mapa.")
    km2 = area_km2(datos)
    if km2 <= 0:
        raise CroquisInvalido("El polígono no tiene una superficie válida.")
    if km2 > LIMITE_KM2:
        raise CroquisInvalido(f"El polígono mide {km2:.1f} km². La web procesa hasta {LIMITE_KM2:g} km²: exporta el proyecto y ábrelo en el módulo local, o divídelo en varios croquis.")
    return km2


def vectorizar(c: Croquis) -> None:
    """Consulta OpenStreetMap (y el relieve si la capa es topográfica), recorta al polígono y guarda los vectores (tarea)."""
    datos = c.datos
    km2 = verificar_area(datos)
    selection = _puntos(datos)
    topo = datos["map_layer"].startswith("Topográfico")
    huella = fuente(datos)

    def avance(mensaje: str) -> None:
        if cancelada():
            raise InterruptedError("Cancelado")
        m = re.search(r"bloque\s+(\d+)\s+de\s+(\d+)", mensaje.lower())
        fraccion = 0.05 + 0.6 * ((int(m.group(1)) - 1) / int(m.group(2))) if m else None
        progreso(fraccion if fraccion is not None else 0.1, mensaje)

    progreso(0.02, "Consultando OpenStreetMap…")
    try:
        features = osm_vector.fetch_osm_features(selection, progress=avance)
        if topo:
            progreso(0.7, "Calculando curvas de nivel…")
            features.extend(terrain_contours.fetch_elevation_contours(selection, datos["contour_interval"], progress=avance))
    except RuntimeError as exc:                                      # los servidores de mapas no respondieron: el detalle (direcciones y respuestas) queda en el registro
        errores.log.warning("Fallo al consultar el mapa del croquis %s: %s", c.pk, exc)
        raise errores.ErrorUsuario("No se pudo consultar el mapa en este momento (los servidores de OpenStreetMap no respondieron). Espera unos minutos e inténtalo de nuevo.") from exc
    progreso(0.92, "Guardando…")
    carpeta = _carpeta(c)
    carpeta.mkdir(parents=True, exist_ok=True)
    temporal = carpeta / "vectores.tmp"
    temporal.write_text(json.dumps([asdict(f) for f in features], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporal.replace(carpeta / "vectores.json")
    conteos = osm_vector.feature_counts(features)
    Croquis.objects.filter(pk=c.pk).update(vectores_ruta=(carpeta / "vectores.json").relative_to(capturas.raiz()).as_posix(), vectores_fuente=huella, vectorizado=timezone.now(),
                                           vectores_resumen={"total": len(features), "conteos": conteos, "area_km2": round(km2, 3)})
    progreso(1.0, "Listo")


def cargar_features(c: Croquis) -> list:
    if estado_vectores(c) != "vigentes":
        raise SinVectores("El área, la capa o las curvas cambiaron: vectoriza de nuevo para exportar la cartografía de esta área.")
    crudo = json.loads((capturas.raiz() / c.vectores_ruta).read_text(encoding="utf-8"))
    return ls.SketchData.from_dict({"features": crudo}).features


def a_geojson(c: Croquis) -> dict:
    """Vectores como GeoJSON (WGS84) para dibujarlos en el mapa; se omiten los vectores si ya no valen."""
    salida = []
    for f in cargar_features(c):
        lineas, poligonos = [], []
        for ruta in f.paths:
            anillo = [[round(lon, 7), round(lat, 7)] for lat, lon, *_ in ruta.points]
            if ruta.closed and ruta.role == "outer" and len(anillo) >= 3:
                poligonos.append([anillo + [anillo[0]]] if anillo[0] != anillo[-1] else [anillo])
            elif len(anillo) >= 2:
                lineas.append(anillo)
        props = {"categoria": f.category, "nombre": f.name}
        if poligonos:
            salida.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiPolygon", "coordinates": poligonos}})
        if lineas:
            salida.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiLineString", "coordinates": lineas}})
    return {"type": "FeatureCollection", "features": salida}


# -- Exportación ----------------------------------------------------------------------------------------------------------------------

def datos_de_dominio(c: Croquis) -> ls.SketchData:
    d = ls.SketchData.from_dict({**c.datos, "vectorized_at": c.vectorizado.isoformat(timespec="seconds") if c.vectorizado else ""})
    d.features = cargar_features(c)
    return d


def verificar_exportable(c: Croquis) -> ls.SketchData:
    """Las mismas condiciones del escritorio antes de exportar (`_validate_export`)."""
    if len(c.datos["points"]) < 3:
        raise CroquisInvalido("Selecciona un área con al menos tres vértices antes de exportar.")
    if not c.vectores_ruta:
        raise SinVectores("Pulsa «Vectorizar mapa» para obtener la cartografía del área antes de exportar.")
    d = datos_de_dominio(c)
    if not d.features:
        raise SinVectores("Pulsa «Vectorizar mapa» para obtener la cartografía del área antes de exportar.")
    if d.map_layer.startswith("Topográfico") and not any(f.category == "contour" for f in d.features):
        raise SinVectores("La capa topográfica requiere vectorizar de nuevo para generar las curvas de nivel del área.")
    return d


def nombre_salida(c: Croquis, formato: str) -> str:
    proyecto = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", c.datos.get("project", "")).strip(" .")
    return "CROQUIS DE UBICACION" + (f" - {' '.join(proyecto.split())[:80]}" if proyecto else "") + f".{formato}"


def generar(c: Croquis, formato: str) -> Path:
    """PDF o DXF del croquis (tarea). Mismo `generate_sketch_pdf` / `generate_sketch_dxf` del escritorio."""
    if formato not in ("pdf", "dxf"):
        raise CroquisInvalido("El formato debe ser pdf o dxf.")
    datos = verificar_exportable(c)
    perfil = Perfil.objects.get(clave=c.perfil)
    carpeta = _carpeta(c)
    carpeta.mkdir(parents=True, exist_ok=True)
    temporal = carpeta / f"salida.tmp.{formato}"
    progreso(0.05, "Dibujando el mapa…" if formato == "pdf" else "Escribiendo el DXF…")
    with perfiles.activar(perfil):
        if formato == "pdf":
            ls.generate_sketch_pdf(datos, temporal, logo_path=branding.active_profile().logo_path)
        else:
            ls.generate_sketch_dxf(datos, temporal)
    destino = carpeta / f"salida.{formato}"
    temporal.replace(destino)
    Croquis.objects.filter(pk=c.pk).update(salida_ruta=destino.relative_to(capturas.raiz()).as_posix(), salida_nombre=nombre_salida(c, formato), salida_formato=formato,
                                           salida_fuente=_huella_salida(c))
    progreso(1.0, "Listo")
    return destino


def editable(c: Croquis) -> dict:
    """Proyecto para el módulo local: el `SketchData` completo (con vectores si valen)."""
    base = {**c.datos, "vectorized_at": c.vectorizado.isoformat(timespec="seconds") if c.vectorizado and estado_vectores(c) == "vigentes" else ""}
    d = ls.SketchData.from_dict(base)
    if estado_vectores(c) == "vigentes":
        d.features = cargar_features(c)
    return d.to_dict()


# -- Importación -----------------------------------------------------------------------------------------------------------------------

def _flotante(valor: str) -> float:
    return float(valor.replace(",", ".")) if valor else float("nan")


def puntos_de_csv(contenido: bytes, partida: int = 0) -> list[dict]:
    """CSV con columnas Punto/Latitud/Longitud/Descripción (como el escritorio). Devuelve vértices ya validados."""
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = contenido.decode("latin-1")
    try:
        dialecto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel
    puntos = []
    for fila in csv.DictReader(io.StringIO(texto, newline=""), dialect=dialecto):
        n = {str(k).strip().lower(): (v or "").strip() for k, v in fila.items() if k}
        lat = n.get("latitud") or n.get("latitude") or n.get("lat")
        lon = n.get("longitud") or n.get("longitude") or n.get("lon") or n.get("lng")
        if not lat or not lon:
            continue
        try:
            la, lo = _flotante(lat), _flotante(lon)
        except ValueError:
            raise CroquisInvalido(f"No se pudo leer la coordenada «{lat}, {lon}».") from None
        nombre = n.get("punto") or n.get("nombre") or n.get("name") or f"V{partida + len(puntos) + 1}"
        puntos.append({"name": nombre[:60], "latitude": la, "longitude": lo, "description": (n.get("descripcion") or n.get("descripción") or n.get("description") or "")[:200]})
    if not puntos:
        raise CroquisInvalido("No se encontraron columnas de latitud y longitud. Usa encabezados como Punto, Latitud, Longitud y Descripción.")
    for n, p in enumerate(puntos, 1):
        _coordenada(p["latitude"], f"latitud del punto {n}", 85.0511), _coordenada(p["longitude"], f"longitud del punto {n}", 180)
    return puntos


def poligono_de_kml(contenido: bytes, nombre: str) -> tuple[list[dict], int]:
    """El polígono de mayor superficie de un KML/KMZ, como el escritorio."""
    sufijo = ".kmz" if nombre.lower().endswith(".kmz") else ".kml"
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = Path(carpeta) / f"entrada{sufijo}"
        ruta.write_bytes(contenido)
        if sufijo == ".kmz":
            try:
                with zipfile.ZipFile(ruta) as z:
                    if sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
                        raise CroquisInvalido("El KMZ descomprime a un tamaño excesivo.")
            except zipfile.BadZipFile:
                raise CroquisInvalido("El KMZ no es un ZIP válido.") from None
        try:
            conjunto = gc.read_kml(ruta)
            puntos, cuantos = ls.polygon_from_kml(conjunto)
        except CroquisInvalido:
            raise
        except Exception as exc:
            raise CroquisInvalido(errores.texto(exc, "No se pudo leer el KML o KMZ. Revisa que contenga un polígono con al menos tres vértices.")) from exc
    return [{"name": p.name, "latitude": round(p.latitude, 7), "longitude": round(p.longitude, 7), "description": ""} for p in puntos][:MAX_VERTICES], cuantos
