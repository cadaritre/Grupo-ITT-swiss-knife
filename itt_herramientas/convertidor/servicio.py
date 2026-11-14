"""Convertidor geoespacial DXF ↔ KML/KMZ (herramientas-web.md §4.4): lectura, vista previa y conversión.

Toda la geometría la procesa `dominio.geospatial_converter`, sin cambios de lógica; aquí solo se administran los archivos, los
límites y la vista previa para el mapa del navegador.
"""
from __future__ import annotations

import json
import math
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path

from django.utils import timezone
from tvplataforma import ajustes
from tvplataforma.tareas import cancelada, progreso

from ..comun import errores
from ..dominio import geometria
from ..dominio import geospatial_converter as gc
from ..models import Conversion

FORMATOS_ENTRADA = ("dxf", "kml", "kmz")
#: Precisión de las curvas (error máximo de cuerda): nombre -> etiqueta. Los valores están en `geometria.TOLERANCIAS`.
PRECISIONES = {"fina": "Fina · 1 mm", "topografica": "Topográfica · 1 cm", "dibujo": "Dibujo · 5 cm", "ligera": "Ligera · 25 cm (archivos más pequeños)"}
FORMATOS_SALIDA = {"dxf": ("kmz", "kml"), "kml": ("dxf",), "kmz": ("dxf",)}
VIDA_DIAS = 7
MAX_PUNTOS_VISTA = 40_000
MAX_DESCOMPRIMIDO = 400 * 1024 * 1024        # un KMZ que descomprima a más que esto es una bomba, no un mapa


class ErrorLimite(errores.ErrorUsuario):
    """El archivo supera lo que la web procesa; el mensaje manda a la persona al módulo local."""


def raiz() -> Path:
    return Path(ajustes.valor("TV_HERRAMIENTAS_ARCHIVOS"))


def carpeta(c: Conversion) -> Path:
    return raiz() / "convertidor" / str(c.cuenta_id) / str(c.id)


def ruta_origen(c: Conversion) -> Path:
    return raiz() / c.ruta_origen


def limites() -> dict:
    return {"bytes": ajustes.limite_herramienta("convertidor", "bytes"), "entidades": ajustes.limite_herramienta("convertidor", "entidades")}


def mensaje_limite(que: str, valor: int | float, maximo: int | float) -> str:
    return (f"{que} ({valor:,.0f}) supera lo que se procesa en la web ({maximo:,.0f}). Ábrelo en Herramientas Grupo ITT "
            "(módulo local), que no tiene este límite.")


def revisar_kmz(ruta: Path) -> None:
    """Rechaza un KMZ corrupto o que descomprime a un tamaño absurdo, sin extraerlo."""
    try:
        with zipfile.ZipFile(ruta) as zf:
            total = sum(i.file_size for i in zf.infolist())
    except zipfile.BadZipFile as exc:
        raise errores.ErrorUsuario("El KMZ no es un ZIP válido o está dañado. Guárdalo de nuevo desde Google Earth y vuelve a subirlo.") from exc
    if total > MAX_DESCOMPRIMIDO:
        raise errores.ErrorUsuario("El KMZ descomprime a un tamaño excesivo y no se procesa.")


def leer(c: Conversion, tolerancia: float | None = None) -> "gc.GeoDataset":
    """Lee el origen con el avance de la tarea. Aplica el límite de entidades. `tolerancia`: error máximo de cuerda en curvas (m)."""
    origen = ruta_origen(c)
    maximo = limites()["entidades"]
    if c.formato == "kmz":
        revisar_kmz(origen)
    if c.formato == "dxf":
        import ezdxf

        progreso(0.02, "Contando entidades…")
        documento = ezdxf.readfile(origen)
        total = len(documento.modelspace())
        del documento
        if total > maximo:
            raise ErrorLimite(mensaje_limite("El DXF tiene demasiadas entidades", total, maximo))
    dataset = gc.read_geospatial(origen, zone=c.zona, hemisphere=c.hemisferio, curve_tolerance=geometria.tolerancia_valida(tolerancia), progress=lambda v, m: progreso(0.05 + 0.65 * v, m))
    if len(dataset.features) > maximo:
        raise ErrorLimite(mensaje_limite("El archivo tiene demasiados elementos", len(dataset.features), maximo))
    return dataset


def geojson(dataset: "gc.GeoDataset") -> dict:
    """Vista previa en WGS84: una `Feature` por elemento, con su capa y color. Se simplifica si es enorme (la conversión no)."""
    from shapely.geometry import LineString, Polygon

    def puntos(f):
        return sum(len(p) for p in f.parts)

    total = sum(puntos(f) for f in dataset.features)
    tolerancia = 0.0
    limites_ = dataset.bounds()
    if total > MAX_PUNTOS_VISTA and limites_:
        diagonal = math.hypot(limites_[2] - limites_[0], limites_[3] - limites_[1])
        tolerancia = diagonal / 20000
    salida = []
    pasos = max(1, len([f for f in dataset.features if f.geometry_type == "Point"]) // 15000 + 1)
    indice_punto = 0
    for f in dataset.features:
        capa = f.layer or "0"
        props = {"capa": capa, "nombre": f.name or "", "color": gc.layer_color(capa), "tipo": f.geometry_type}
        if f.geometry_type == "Point":
            indice_punto += 1
            if indice_punto % pasos:
                continue
            x, y = f.parts[0][0][:2]
            salida.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}})
        elif f.geometry_type == "LineString":
            for parte in f.parts:
                pts = [(p[0], p[1]) for p in parte]
                if tolerancia and len(pts) > 3:
                    pts = list(LineString(pts).simplify(tolerancia).coords)
                if len(pts) >= 2:
                    salida.append({"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": [list(p) for p in pts]}})
        elif f.geometry_type == "Polygon":
            anillos = []
            for parte in f.parts:
                pts = [(p[0], p[1]) for p in parte]
                if tolerancia and len(pts) > 5:
                    simple = Polygon(pts).simplify(tolerancia)
                    pts = list(simple.exterior.coords) if not simple.is_empty else pts
                if pts and pts[0] != pts[-1]:
                    pts.append(pts[0])
                if len(pts) >= 4:
                    anillos.append([list(p) for p in pts])
            if anillos:
                salida.append({"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": anillos}})
    return {"type": "FeatureCollection", "features": salida}


def resumen(dataset: "gc.GeoDataset", c: Conversion) -> dict:
    cuentas = dataset.geometry_counts()
    limites_ = dataset.bounds()
    zona, hem = gc.suggested_utm(dataset)
    return {
        "elementos": len(dataset.features),
        "capas": [{"nombre": nombre, "elementos": n, "color": gc.layer_color(nombre)} for nombre, n in sorted(dataset.layer_counts().items())],
        "geometrias": {"puntos": cuentas.get("Point", 0), "lineas": cuentas.get("LineString", 0), "poligonos": cuentas.get("Polygon", 0)},
        "limites": list(limites_) if limites_ else None,
        "avisos": list(dataset.warnings),
        "zona_sugerida": [zona, hem],
        "formato": dataset.source_format,
    }


def analizar(c: Conversion) -> None:
    """Lee el origen y guarda el resumen y la vista previa. Se llama desde la tarea; cualquier error queda en la conversión."""
    c.estado, c.error = Conversion.Estado.ANALIZANDO, ""
    c.save(update_fields=["estado", "error"])
    try:
        dataset = leer(c)
        progreso(0.75, "Preparando la vista previa…")
        destino = carpeta(c)
        destino.mkdir(parents=True, exist_ok=True)
        vista = destino / "vista.geojson"
        vista.write_text(json.dumps(geojson(dataset), separators=(",", ":")), encoding="utf-8")
        c.vista_ruta = str(vista.relative_to(raiz()))
        c.resumen = resumen(dataset, c)
        c.estado = Conversion.Estado.LISTO
        progreso(1.0, "Listo")
    except Exception as exc:                               # el error se muestra a la persona; la tarea no debe "fallar" a medias
        c.estado = Conversion.Estado.ERROR
        if isinstance(exc, InterruptedError):
            raise
        c.error = errores.texto(exc, errores.ARCHIVO_ILEGIBLE)
        c.resumen = {}
    c.save()


def nombre_salida(c: Conversion, formato: str) -> str:
    return f"{Path(c.nombre).stem}_convertido.{formato}"


def convertir(c: Conversion) -> None:
    """Genera la salida pedida en `c.opciones` con las capas elegidas."""
    op = c.opciones
    formato = op["formato"]
    if formato not in FORMATOS_SALIDA[c.formato]:
        raise errores.ErrorUsuario(f"Un archivo {c.formato.upper()} se convierte a {' o '.join(f.upper() for f in FORMATOS_SALIDA[c.formato])}.")
    capas = set(op.get("capas") or [])
    if not capas:
        raise errores.ErrorUsuario("Activa al menos una capa para exportar.")
    dataset = leer(c, geometria.TOLERANCIAS.get(op.get("precision", ""), geometria.TOLERANCIA_POR_OMISION))
    if cancelada():
        return
    destino = carpeta(c) / "salida"
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    objetivo = destino / nombre_salida(c, formato)
    progreso(0.72, "Escribiendo el archivo…")
    gc.convert_file(
        dataset, objetivo, zone=int(op["zona"]), hemisphere=op["hemisferio"], selected_layers=capas,
        add_labels=bool(op.get("etiquetas", True)), add_hatches=bool(op.get("rellenos", True)), clamp_to_ground=bool(op.get("relieve", True)),
        progress=lambda v, m: progreso(0.72 + 0.27 * v, m),
    )
    c.salida_ruta, c.salida_formato = str(objetivo.relative_to(raiz())), formato
    c.save(update_fields=["salida_ruta", "salida_formato"])
    progreso(1.0, "Listo")
    if formato in ("kml", "kmz"):                                              # a Drive (Poligonos KML), como el escritorio; el DXF no se envía
        from ..drive import servicio as drive

        drive.encolar(c.cuenta_id, "poligonos", objetivo.name, origen=objetivo, clave=drive.clave_de("conversion", c.pk, sorted(op.items(), key=str)))


def borrar_archivos(c: Conversion) -> None:
    shutil.rmtree(carpeta(c), ignore_errors=True)
    ruta = ruta_origen(c)
    ruta.unlink(missing_ok=True)


def limpiar() -> int:
    """`tv_limpiar`: borra las conversiones de más de 7 días (archivos y filas). Devuelve cuántas."""
    viejas = list(Conversion.objects.filter(creada__lt=timezone.now() - timedelta(days=VIDA_DIAS)))
    for c in viejas:
        borrar_archivos(c)
        c.delete()
    return len(viejas)
