from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable


Point3D = tuple[float, float, float]
Triangle = tuple[Point3D, Point3D, Point3D]
ProgressCallback = Callable[[float, str], None]


def _progress(callback: ProgressCallback | None, value: float, message: str):
    if callback:
        callback(max(0.0, min(1.0, value)), message)


@dataclass
class TinStats:
    point_entities: int = 0
    insert_entities: int = 0
    polyline_vertices: int = 0
    invalid: int = 0
    duplicates: int = 0
    triangles_total: int = 0
    filtered_area: int = 0
    filtered_edge: int = 0


@dataclass
class TinModel:
    source_name: str
    points: list[Point3D]
    triangles: list[Triangle]
    stats: TinStats

    def bounds(self) -> tuple[float, float, float, float]:
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class SlopeRange:
    name: str
    min_pct: float
    max_pct: float | None
    color_hex: str
    count: int = 0
    area_2d: float = 0.0
    area_3d: float = 0.0
    layer_name: str = ""

    def label(self) -> str:
        return f"> {self.min_pct:g}%" if self.max_pct is None else f"{self.min_pct:g}% - {self.max_pct:g}%"

    def contains(self, value: float) -> bool:
        return value >= self.min_pct if self.max_pct is None else self.min_pct <= value < self.max_pct


DEFAULT_SLOPE_RANGES = (
    SlopeRange("Plano", 0.0, 2.0, "#2ECC71"),
    SlopeRange("Ligero", 2.0, 5.0, "#A3E635"),
    SlopeRange("Medio", 5.0, 10.0, "#FACC15"),
    SlopeRange("Fuerte", 10.0, 20.0, "#FB923C"),
    SlopeRange("Muy fuerte", 20.0, 35.0, "#EF4444"),
    SlopeRange("Extremo", 35.0, None, "#7F1D1D"),
)


@dataclass
class SlopeAnalysis:
    source_name: str
    triangles: list[Triangle]
    slopes: list[float]
    assignments: list[int]
    ranges: list[SlopeRange]

    @property
    def total_area(self) -> float:
        return sum(item.area_2d for item in self.ranges)

    def bounds(self) -> tuple[float, float, float, float]:
        return triangle_bounds(self.triangles)


@dataclass
class FlowRange:
    name: str
    min_pct: float
    max_pct: float | None
    color_hex: str
    length_factor: float = 1.0
    lineweight_mm: float = 0.25
    count: int = 0
    layer_name: str = ""

    def label(self) -> str:
        return f"> {self.min_pct:g}%" if self.max_pct is None else f"{self.min_pct:g}% - {self.max_pct:g}%"

    def contains(self, value: float) -> bool:
        return value >= self.min_pct if self.max_pct is None else self.min_pct <= value < self.max_pct


DEFAULT_FLOW_RANGES = (
    FlowRange("Escurrimiento suave", 0.0, 2.0, "#66D9E8", 0.75, 0.18),
    FlowRange("Escurrimiento moderado", 2.0, 10.0, "#22A6D5", 1.00, 0.25),
    FlowRange("Escurrimiento rápido", 10.0, 20.0, "#1769AA", 1.20, 0.35),
    FlowRange("Escurrimiento fuerte", 20.0, None, "#5036A6", 1.45, 0.50),
)


@dataclass
class FlowArrow:
    start: tuple[float, float]
    tip: tuple[float, float]
    head_left: tuple[float, float]
    head_right: tuple[float, float]
    center: Point3D
    slope_pct: float
    range_index: int


@dataclass
class FlowAnalysis:
    source_name: str
    triangles: list[Triangle]
    arrows: list[FlowArrow]
    ranges: list[FlowRange]
    base_length: float
    head_ratio: float
    density: int
    minimum_slope: float

    def bounds(self) -> tuple[float, float, float, float]:
        return triangle_bounds(self.triangles)


def clone_default_ranges() -> list[SlopeRange]:
    return [replace(item) for item in DEFAULT_SLOPE_RANGES]


def clone_default_flow_ranges() -> list[FlowRange]:
    return [replace(item) for item in DEFAULT_FLOW_RANGES]


def _finite_xyz(x, y, z) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in (x, y, z))
    except Exception:
        return False


def read_dxf_points(
    path: str | Path,
    include_points: bool = True,
    include_inserts: bool = True,
    include_polyline_vertices: bool = False,
) -> tuple[list[Point3D], TinStats]:
    try:
        import ezdxf
    except ImportError as exc:
        raise RuntimeError("Falta ezdxf para leer la triangulación.") from exc
    document = ezdxf.readfile(path)
    records: list[Point3D] = []
    stats = TinStats()
    for entity in document.modelspace():
        kind = entity.dxftype()
        candidates: list[Point3D] = []
        if include_points and kind == "POINT":
            location = entity.dxf.location
            candidates = [(float(location.x), float(location.y), float(location.z))]
            stats.point_entities += 1
        elif include_inserts and kind == "INSERT":
            insertion = entity.dxf.insert
            candidates = [(float(insertion.x), float(insertion.y), float(insertion.z))]
            stats.insert_entities += 1
        elif include_polyline_vertices and kind == "POLYLINE":
            candidates = [
                (float(vertex.dxf.location.x), float(vertex.dxf.location.y), float(vertex.dxf.location.z))
                for vertex in entity.vertices
            ]
            stats.polyline_vertices += len(candidates)
        elif include_polyline_vertices and kind == "LWPOLYLINE":
            elevation = float(getattr(entity.dxf, "elevation", 0.0) or 0.0)
            candidates = [(float(point[0]), float(point[1]), elevation) for point in entity.get_points("xy")]
            stats.polyline_vertices += len(candidates)
        for point in candidates:
            if _finite_xyz(*point):
                records.append(point)
            else:
                stats.invalid += 1
    return records, stats


def deduplicate_xy(points: Iterable[Point3D], decimals: int = 6) -> tuple[list[Point3D], int]:
    unique: list[Point3D] = []
    seen: set[tuple[float, float]] = set()
    duplicates = 0
    for point in points:
        key = round(point[0], decimals), round(point[1], decimals)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(point)
    return unique, duplicates


def triangle_area_2d(triangle: Triangle) -> float:
    a, b, c = triangle
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2.0


def triangle_area_3d(triangle: Triangle) -> float:
    a, b, c = triangle
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cross = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    return math.sqrt(sum(value * value for value in cross)) / 2.0


def maximum_edge_2d(triangle: Triangle) -> float:
    a, b, c = triangle
    return max(
        math.hypot(a[0] - b[0], a[1] - b[1]),
        math.hypot(b[0] - c[0], b[1] - c[1]),
        math.hypot(c[0] - a[0], c[1] - a[1]),
    )


def create_tin_model(
    path: str | Path,
    include_points: bool = True,
    include_inserts: bool = True,
    include_polyline_vertices: bool = False,
    dedup_decimals: int = 6,
    max_edge: float | None = None,
    min_area: float = 1e-9,
    progress: ProgressCallback | None = None,
) -> TinModel:
    try:
        from shapely import delaunay_triangles
        from shapely.geometry import MultiPoint
    except ImportError as exc:
        raise RuntimeError("Falta Shapely para generar la triangulación Delaunay.") from exc
    _progress(progress, 0.03, "Leyendo entidades POINT, INSERT y polilíneas…")
    raw_points, stats = read_dxf_points(path, include_points, include_inserts, include_polyline_vertices)
    _progress(progress, 0.26, f"{len(raw_points):,} puntos leídos · eliminando duplicados XY…")
    points, stats.duplicates = deduplicate_xy(raw_points, dedup_decimals)
    if len(points) < 3:
        raise ValueError("No hay al menos tres puntos XY únicos. Activa POINT, INSERT o vértices de polilínea según corresponda.")
    _progress(progress, 0.38, f"{len(points):,} puntos únicos · calculando Delaunay…")
    xy_lookup = {(round(point[0], 9), round(point[1], 9)): point for point in points}
    result = delaunay_triangles(MultiPoint([(point[0], point[1]) for point in points]))
    triangles: list[Triangle] = []
    polygons = list(getattr(result, "geoms", []))
    stats.triangles_total = len(polygons)
    for polygon_index, polygon in enumerate(polygons):
        if polygon_index % 250 == 0:
            _progress(progress, 0.48 + 0.49 * polygon_index / max(len(polygons), 1), f"Filtrando triángulo {polygon_index:,} de {len(polygons):,}…")
        coordinates = list(polygon.exterior.coords)[:3]
        vertices: list[Point3D] = []
        for x, y in coordinates:
            point = xy_lookup.get((round(x, 9), round(y, 9)))
            if point is None:
                point = min(points, key=lambda candidate: (candidate[0] - x) ** 2 + (candidate[1] - y) ** 2)
            vertices.append(point)
        triangle = vertices[0], vertices[1], vertices[2]
        if triangle_area_2d(triangle) <= min_area:
            stats.filtered_area += 1
            continue
        if max_edge is not None and maximum_edge_2d(triangle) > max_edge:
            stats.filtered_edge += 1
            continue
        triangles.append(triangle)
    if not triangles:
        raise ValueError("Todos los triángulos quedaron fuera por los filtros de área o longitud máxima de arista.")
    _progress(progress, 1.0, f"TIN listo · {len(triangles):,} triángulos")
    return TinModel(Path(path).name, points, triangles, stats)


def write_tin_dxf(model: TinModel, path: str | Path, include_source_points: bool = True, progress: ProgressCallback | None = None) -> Path:
    try:
        import ezdxf
        from ezdxf import units
    except ImportError as exc:
        raise RuntimeError("Falta ezdxf para exportar el TIN.") from exc
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = ezdxf.new("R2013", setup=True)
    document.units = units.M
    document.header["$INSUNITS"] = 6
    tin_layer = document.layers.add("TIN_3DFACE", color=3)
    tin_layer.rgb = (31, 143, 173)
    point_layer = document.layers.add("PUNTOS_ORIGEN", color=1)
    point_layer.rgb = (220, 60, 45)
    modelspace = document.modelspace()
    _progress(progress, 0.05, "Creando capas y superficie 3DFACE…")
    for index, triangle in enumerate(model.triangles):
        if index % 300 == 0:
            _progress(progress, 0.08 + 0.68 * index / max(len(model.triangles), 1), f"Escribiendo 3DFACE {index:,} de {len(model.triangles):,}…")
        a, b, c = triangle
        modelspace.add_3dface([a, b, c, c], dxfattribs={"layer": "TIN_3DFACE"})
    if include_source_points:
        for index, point in enumerate(model.points):
            if index % 500 == 0:
                _progress(progress, 0.78 + 0.14 * index / max(len(model.points), 1), f"Escribiendo punto {index:,} de {len(model.points):,}…")
            modelspace.add_point(point, dxfattribs={"layer": "PUNTOS_ORIGEN"})
    _progress(progress, 0.94, "Guardando y cerrando el DXF…")
    document.saveas(target)
    _progress(progress, 1.0, "DXF TIN terminado")
    return target


def _distance_3d(a: Point3D, b: Point3D) -> float:
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def _entity_vertices(entity) -> list[Point3D]:
    kind = entity.dxftype()
    if kind == "3DFACE":
        values = [getattr(entity.dxf, name) for name in ("vtx0", "vtx1", "vtx2", "vtx3")]
        result: list[Point3D] = []
        for value in values:
            point = float(value[0]), float(value[1]), float(value[2])
            if not any(_distance_3d(point, existing) < 1e-9 for existing in result):
                result.append(point)
        return result
    if kind == "POLYLINE":
        return [
            (float(vertex.dxf.location.x), float(vertex.dxf.location.y), float(vertex.dxf.location.z))
            for vertex in entity.vertices
        ]
    if kind == "LWPOLYLINE":
        elevation = float(getattr(entity.dxf, "elevation", 0.0) or 0.0)
        return [(float(point[0]), float(point[1]), elevation) for point in entity.get_points("xy")]
    return []


def _fan_triangles(points: list[Point3D]) -> list[Triangle]:
    if len(points) > 3 and _distance_3d(points[0], points[-1]) < 1e-9:
        points = points[:-1]
    if len(points) < 3:
        return []
    return [(points[0], points[index], points[index + 1]) for index in range(1, len(points) - 1)]


def triangle_layers(path: str | Path) -> list[str]:
    try:
        import ezdxf
    except ImportError as exc:
        raise RuntimeError("Falta ezdxf para leer el TIN.") from exc
    document = ezdxf.readfile(path)
    return sorted({
        str(entity.dxf.layer) for entity in document.modelspace()
        if entity.dxftype() in {"3DFACE", "POLYLINE", "LWPOLYLINE"}
    }, key=str.casefold)


def extract_triangles(path: str | Path, selected_layers: Iterable[str] | None = None, progress: ProgressCallback | None = None) -> list[Triangle]:
    try:
        import ezdxf
    except ImportError as exc:
        raise RuntimeError("Falta ezdxf para leer el TIN.") from exc
    document = ezdxf.readfile(path)
    modelspace = document.modelspace()
    selected = set(selected_layers or [])
    available = {
        str(entity.dxf.layer) for entity in modelspace
        if entity.dxftype() in {"3DFACE", "POLYLINE", "LWPOLYLINE"}
    }
    if not selected and "TIN_3DFACE" in available:
        selected = {"TIN_3DFACE"}
    triangles: list[Triangle] = []
    entities = list(modelspace)
    for entity_index, entity in enumerate(entities):
        if entity_index % 500 == 0:
            _progress(progress, entity_index / max(len(entities), 1), f"Leyendo entidad {entity_index:,} de {len(entities):,}…")
        if entity.dxftype() not in {"3DFACE", "POLYLINE", "LWPOLYLINE"}:
            continue
        if selected and str(entity.dxf.layer) not in selected:
            continue
        for triangle in _fan_triangles(_entity_vertices(entity)):
            if triangle_area_2d(triangle) > 1e-9:
                triangles.append(triangle)
    if not triangles:
        raise ValueError("No se encontraron triángulos válidos en las capas seleccionadas.")
    _progress(progress, 1.0, f"{len(triangles):,} triángulos leídos")
    return triangles


def triangle_slope_percent(triangle: Triangle) -> float:
    a, b, c = triangle
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    if abs(nz) < 1e-12:
        return float("inf")
    return math.hypot(-nx / nz, -ny / nz) * 100.0


def _triangle_downhill(triangle: Triangle) -> tuple[float, float, float]:
    a, b, c = triangle
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    if abs(nz) < 1e-12:
        return 0.0, 0.0, float("inf")
    gradient_x, gradient_y = -nx / nz, -ny / nz
    downhill_x, downhill_y = -gradient_x, -gradient_y
    magnitude = math.hypot(downhill_x, downhill_y)
    if magnitude < 1e-12:
        return 0.0, 0.0, 0.0
    return downhill_x / magnitude, downhill_y / magnitude, magnitude * 100.0


def validate_ranges(ranges: Iterable[SlopeRange]) -> list[SlopeRange]:
    clean = sorted((replace(item, count=0, area_2d=0.0, area_3d=0.0, layer_name="") for item in ranges), key=lambda item: item.min_pct)
    if not clean:
        raise ValueError("Agrega al menos un rango de pendiente.")
    if clean[0].min_pct > 0:
        raise ValueError("El primer rango debe comenzar en 0%.")
    for index, item in enumerate(clean):
        if item.min_pct < 0 or (item.max_pct is not None and item.max_pct <= item.min_pct):
            raise ValueError(f"Rango inválido: {item.name}.")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", item.color_hex):
            raise ValueError(f"Color inválido en {item.name}: usa #RRGGBB.")
        if index and clean[index - 1].max_pct != item.min_pct:
            raise ValueError("Los rangos deben ser continuos, sin huecos ni traslapes.")
    if clean[-1].max_pct is not None:
        raise ValueError("El último rango debe dejar 'Hasta %' vacío.")
    return clean


def validate_flow_ranges(ranges: Iterable[FlowRange]) -> list[FlowRange]:
    clean = sorted((replace(item, count=0, layer_name="") for item in ranges), key=lambda item: item.min_pct)
    if not clean:
        raise ValueError("Agrega al menos un rango para las flechas.")
    if not math.isclose(clean[0].min_pct, 0.0, abs_tol=1e-9):
        raise ValueError("El primer rango de escurrimiento debe comenzar en 0%.")
    for index, item in enumerate(clean):
        if item.min_pct < 0 or (item.max_pct is not None and item.max_pct <= item.min_pct):
            raise ValueError(f"Rango inválido: {item.name}.")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", item.color_hex):
            raise ValueError(f"Color inválido en {item.name}: usa #RRGGBB.")
        if item.length_factor <= 0 or item.lineweight_mm <= 0:
            raise ValueError(f"Tamaño o grosor inválido en {item.name}.")
        if index and not math.isclose(clean[index - 1].max_pct or -1, item.min_pct, abs_tol=1e-9):
            raise ValueError("Los rangos de escurrimiento deben ser continuos.")
    if clean[-1].max_pct is not None:
        raise ValueError("El último rango debe dejar 'Hasta %' vacío.")
    return clean


def analyze_slopes(triangles: Iterable[Triangle], ranges: Iterable[SlopeRange], source_name: str = "TIN", progress: ProgressCallback | None = None) -> SlopeAnalysis:
    triangle_list = list(triangles)
    if not triangle_list:
        raise ValueError("No hay triángulos para calcular pendientes.")
    clean_ranges = validate_ranges(ranges)
    slopes: list[float] = []
    assignments: list[int] = []
    for triangle_index, triangle in enumerate(triangle_list):
        if triangle_index % 500 == 0:
            _progress(progress, triangle_index / max(len(triangle_list), 1), f"Calculando pendiente {triangle_index:,} de {len(triangle_list):,}…")
        slope = triangle_slope_percent(triangle)
        range_index = next((index for index, item in enumerate(clean_ranges) if item.contains(slope)), len(clean_ranges) - 1)
        item = clean_ranges[range_index]
        item.count += 1
        item.area_2d += triangle_area_2d(triangle)
        item.area_3d += triangle_area_3d(triangle)
        slopes.append(slope)
        assignments.append(range_index)
    _progress(progress, 1.0, "Zonificación calculada")
    return SlopeAnalysis(source_name, triangle_list, slopes, assignments, clean_ranges)


def analyze_flow(
    triangles: Iterable[Triangle],
    ranges: Iterable[FlowRange],
    source_name: str = "TIN",
    base_length: float | None = None,
    head_ratio: float = 0.28,
    density: int = 1,
    minimum_slope: float = 0.10,
    progress: ProgressCallback | None = None,
) -> FlowAnalysis:
    triangle_list = list(triangles)
    if not triangle_list:
        raise ValueError("No hay triángulos para calcular escurrimientos.")
    clean_ranges = validate_flow_ranges(ranges)
    density = max(1, int(density))
    if not 0.10 <= head_ratio <= 0.60:
        raise ValueError("El tamaño de punta debe estar entre 10% y 60%.")
    if minimum_slope < 0:
        raise ValueError("La pendiente mínima no puede ser negativa.")
    if base_length is None:
        characteristic = [math.sqrt(max(triangle_area_2d(triangle), 1e-12)) for triangle in triangle_list]
        base_length = statistics.median(characteristic) * 0.85
    if base_length <= 0:
        raise ValueError("La longitud base de las flechas debe ser mayor que cero.")
    arrows: list[FlowArrow] = []
    for triangle_index in range(0, len(triangle_list), density):
        if triangle_index % max(500, density) == 0:
            _progress(progress, triangle_index / max(len(triangle_list), 1), f"Calculando flujo {triangle_index:,} de {len(triangle_list):,}…")
        triangle = triangle_list[triangle_index]
        direction_x, direction_y, slope = _triangle_downhill(triangle)
        if not math.isfinite(slope) or slope < minimum_slope or (direction_x == 0 and direction_y == 0):
            continue
        range_index = next((index for index, item in enumerate(clean_ranges) if item.contains(slope)), len(clean_ranges) - 1)
        item = clean_ranges[range_index]
        item.count += 1
        length = base_length * item.length_factor
        center = tuple(sum(point[axis] for point in triangle) / 3 for axis in range(3))
        start = center[0] - direction_x * length * 0.45, center[1] - direction_y * length * 0.45
        tip = center[0] + direction_x * length * 0.55, center[1] + direction_y * length * 0.55
        head_length = length * head_ratio
        head_half_width = head_length * 0.52
        base_x = tip[0] - direction_x * head_length
        base_y = tip[1] - direction_y * head_length
        perpendicular_x, perpendicular_y = -direction_y, direction_x
        head_left = base_x + perpendicular_x * head_half_width, base_y + perpendicular_y * head_half_width
        head_right = base_x - perpendicular_x * head_half_width, base_y - perpendicular_y * head_half_width
        arrows.append(FlowArrow(start, tip, head_left, head_right, center, slope, range_index))
    if not arrows:
        raise ValueError("No se generaron flechas. Reduce la pendiente mínima o revisa las elevaciones Z del TIN.")
    _progress(progress, 1.0, f"{len(arrows):,} flechas calculadas")
    return FlowAnalysis(source_name, triangle_list, arrows, clean_ranges, base_length, head_ratio, density, minimum_slope)


def triangle_bounds(triangles: Iterable[Triangle]) -> tuple[float, float, float, float]:
    points = [point for triangle in triangles for point in triangle]
    return min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    clean = value.lstrip("#")
    return tuple(int(clean[index:index + 2], 16) for index in (0, 2, 4))


def _clean_layer(value: str) -> str:
    clean = re.sub(r'[<>/\\":;?*|=`,\[\]]', "_", value.strip()).replace("%", "pct").replace(" ", "_")
    return clean[:240] or "SIN_NOMBRE"


def _add_text(modelspace, text: str, insert, height: float, layer: str = "TABLA_PENDIENTES"):
    return modelspace.add_text(text, dxfattribs={"height": height, "layer": layer, "insert": insert})


def _add_slope_table(modelspace, analysis: SlopeAnalysis, decimals: int = 2):
    min_x, min_y, max_x, max_y = analysis.bounds()
    size = max(max_x - min_x, max_y - min_y, 1.0)
    text_height = max(size * 0.012, 1.5)
    row_height = text_height * 1.9
    x = max_x + size * 0.04
    y = max_y
    _add_text(modelspace, "TABLA DE ZONIFICACION POR PENDIENTES", (x, y), text_height * 1.15)
    _add_text(modelspace, f"Area total: {analysis.total_area:.{decimals}f} m2", (x, y - row_height), text_height)
    y -= row_height * 2.4
    _add_text(modelspace, "RANGO | DESCRIPCION | AREA m2 | % | TRIANGULOS", (x, y), text_height * 0.82)
    for item in analysis.ranges:
        y -= row_height
        percentage = item.area_2d / analysis.total_area * 100 if analysis.total_area else 0
        rgb = _hex_rgb(item.color_hex)
        hatch = modelspace.add_hatch(dxfattribs={"layer": "TABLA_PENDIENTES"})
        hatch.set_solid_fill(color=256, rgb=rgb)
        hatch.paths.add_polyline_path([(x, y), (x + text_height * 2, y), (x + text_height * 2, y + text_height), (x, y + text_height)], is_closed=True, flags=1)
        _add_text(
            modelspace,
            f"{item.label()} | {item.name} | {item.area_2d:.{decimals}f} | {percentage:.2f}% | {item.count}",
            (x + text_height * 2.8, y), text_height * 0.82,
        )


def write_slope_dxf(
    analysis: SlopeAnalysis,
    path: str | Path,
    include_slope_text: bool = False,
    include_3d_faces: bool = True,
    decimals: int = 2,
    progress: ProgressCallback | None = None,
) -> Path:
    try:
        import ezdxf
        from ezdxf import units
    except ImportError as exc:
        raise RuntimeError("Falta ezdxf para exportar la zonificación.") from exc
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = ezdxf.new("R2010", setup=True)
    document.units = units.M
    document.header["$INSUNITS"] = 6
    modelspace = document.modelspace()
    for layer_name, color in (("TRIANGULACION_BORDE", 8), ("TABLA_PENDIENTES", 7), ("TEXTOS_PENDIENTE", 7)):
        document.layers.add(layer_name, color=color)
    by_range: dict[int, list[int]] = defaultdict(list)
    for triangle_index, range_index in enumerate(analysis.assignments):
        by_range[range_index].append(triangle_index)
    bounds = analysis.bounds()
    size = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 1.0)
    text_height = max(size * 0.006, 0.75)
    unique_edges: dict[tuple, tuple[Point3D, Point3D]] = {}
    max_paths_per_hatch = 200
    processed = 0
    for range_index, item in enumerate(analysis.ranges):
        item.layer_name = _clean_layer(f"PEND_{range_index + 1:02d}_{item.label()}_{item.name}")
        layer = document.layers.add(item.layer_name, color=7)
        layer.rgb = _hex_rgb(item.color_hex)
        rgb = _hex_rgb(item.color_hex)
        indices = by_range.get(range_index, [])
        for start in range(0, len(indices), max_paths_per_hatch):
            hatch = modelspace.add_hatch(dxfattribs={"layer": item.layer_name})
            hatch.set_solid_fill(color=256, rgb=rgb)
            hatch.dxf.associative = 0
            for triangle_index in indices[start:start + max_paths_per_hatch]:
                triangle = analysis.triangles[triangle_index]
                hatch.paths.add_polyline_path([(point[0], point[1]) for point in triangle], is_closed=True, flags=1)
        for triangle_index in indices:
            processed += 1
            if processed % 400 == 0:
                _progress(progress, 0.08 + 0.78 * processed / max(len(analysis.triangles), 1), f"Exportando triángulo {processed:,} de {len(analysis.triangles):,}…")
            triangle = analysis.triangles[triangle_index]
            if include_3d_faces:
                a, b, c = triangle
                face = modelspace.add_3dface([a, b, c, c], dxfattribs={"layer": item.layer_name})
                face.rgb = rgb
            for a, b in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
                key = tuple(sorted(((round(a[0], 6), round(a[1], 6)), (round(b[0], 6), round(b[1], 6)))))
                unique_edges.setdefault(key, (a, b))
            if include_slope_text and math.isfinite(analysis.slopes[triangle_index]):
                center = tuple(sum(point[axis] for point in triangle) / 3 for axis in range(3))
                _add_text(modelspace, f"{analysis.slopes[triangle_index]:.1f}%", center, text_height, "TEXTOS_PENDIENTE")
    for a, b in unique_edges.values():
        modelspace.add_line((a[0], a[1]), (b[0], b[1]), dxfattribs={"layer": "TRIANGULACION_BORDE"})
    _progress(progress, 0.90, "Creando tabla de áreas y porcentajes…")
    _add_slope_table(modelspace, analysis, decimals)
    _progress(progress, 0.96, "Guardando DXF de pendientes…")
    document.saveas(target)
    _progress(progress, 1.0, "DXF de pendientes terminado")
    return target


def _lineweight_value(value_mm: float) -> int:
    allowed = (13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)
    requested = round(value_mm * 100)
    return min(allowed, key=lambda candidate: abs(candidate - requested))


def _add_flow_legend(modelspace, analysis: FlowAnalysis):
    min_x, min_y, max_x, max_y = analysis.bounds()
    size = max(max_x - min_x, max_y - min_y, 1.0)
    text_height = max(size * 0.012, 1.5)
    row_height = text_height * 1.9
    x = max_x + size * 0.04
    y = max_y
    _add_text(modelspace, "DIRECCIONES DE ESCURRIMIENTO PLUVIAL", (x, y), text_height * 1.15, "TABLA_ESCURRIMIENTOS")
    _add_text(
        modelspace,
        f"Flechas: {len(analysis.arrows)} | Longitud base: {analysis.base_length:.2f} m | Cada {analysis.density} triangulo(s)",
        (x, y - row_height), text_height * 0.82, "TABLA_ESCURRIMIENTOS",
    )
    y -= row_height * 2.4
    for item in analysis.ranges:
        rgb = _hex_rgb(item.color_hex)
        sample_start = (x, y + text_height * 0.4)
        sample_tip = (x + text_height * 3.0, y + text_height * 0.4)
        modelspace.add_line(sample_start, sample_tip, dxfattribs={
            "layer": "TABLA_ESCURRIMIENTOS", "true_color": (rgb[0] << 16) + (rgb[1] << 8) + rgb[2],
            "lineweight": _lineweight_value(item.lineweight_mm),
        })
        modelspace.add_solid([
            sample_tip,
            (sample_tip[0] - text_height * 0.8, sample_tip[1] + text_height * 0.45),
            (sample_tip[0] - text_height * 0.8, sample_tip[1] - text_height * 0.45),
            (sample_tip[0] - text_height * 0.8, sample_tip[1] - text_height * 0.45),
        ], dxfattribs={"layer": "TABLA_ESCURRIMIENTOS", "true_color": (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]})
        _add_text(
            modelspace,
            f"{item.label()} | {item.name} | x{item.length_factor:g} | {item.lineweight_mm:g} mm | {item.count} flechas",
            (x + text_height * 3.8, y), text_height * 0.82, "TABLA_ESCURRIMIENTOS",
        )
        y -= row_height


def write_flow_dxf(
    analysis: FlowAnalysis,
    path: str | Path,
    include_tin_reference: bool = True,
    include_slope_text: bool = False,
    progress: ProgressCallback | None = None,
) -> Path:
    try:
        import ezdxf
        from ezdxf import units
    except ImportError as exc:
        raise RuntimeError("Falta ezdxf para exportar los escurrimientos.") from exc
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = ezdxf.new("R2010", setup=True)
    document.units = units.M
    document.header["$INSUNITS"] = 6
    modelspace = document.modelspace()
    document.layers.add("TIN_REFERENCIA", color=8)
    document.layers.add("TEXTOS_ESCURRIMIENTO", color=7)
    document.layers.add("TABLA_ESCURRIMIENTOS", color=7)
    for range_index, item in enumerate(analysis.ranges, 1):
        item.layer_name = _clean_layer(f"ESC_{range_index:02d}_{item.label()}_{item.name}")
        layer = document.layers.add(item.layer_name, color=7)
        layer.rgb = _hex_rgb(item.color_hex)
    if include_tin_reference:
        unique_edges: dict[tuple, tuple[Point3D, Point3D]] = {}
        for triangle in analysis.triangles:
            for a, b in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
                key = tuple(sorted(((round(a[0], 6), round(a[1], 6)), (round(b[0], 6), round(b[1], 6)))))
                unique_edges.setdefault(key, (a, b))
        for index, (a, b) in enumerate(unique_edges.values()):
            if index % 600 == 0:
                _progress(progress, 0.05 + 0.27 * index / max(len(unique_edges), 1), f"Dibujando malla {index:,} de {len(unique_edges):,} aristas…")
            modelspace.add_line((a[0], a[1]), (b[0], b[1]), dxfattribs={"layer": "TIN_REFERENCIA"})
    text_height = max(analysis.base_length * 0.22, 0.25)
    for arrow_index, arrow in enumerate(analysis.arrows):
        if arrow_index % 400 == 0:
            _progress(progress, 0.34 + 0.55 * arrow_index / max(len(analysis.arrows), 1), f"Exportando flecha {arrow_index:,} de {len(analysis.arrows):,}…")
        item = analysis.ranges[arrow.range_index]
        rgb = _hex_rgb(item.color_hex)
        true_color = (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]
        attributes = {"layer": item.layer_name, "true_color": true_color, "lineweight": _lineweight_value(item.lineweight_mm)}
        modelspace.add_line(arrow.start, arrow.tip, dxfattribs=attributes)
        modelspace.add_solid(
            [arrow.tip, arrow.head_left, arrow.head_right, arrow.head_right],
            dxfattribs={"layer": item.layer_name, "true_color": true_color},
        )
        if include_slope_text:
            _add_text(modelspace, f"{arrow.slope_pct:.1f}%", (arrow.center[0], arrow.center[1]), text_height, "TEXTOS_ESCURRIMIENTO")
    _progress(progress, 0.91, "Creando leyenda de escurrimientos…")
    _add_flow_legend(modelspace, analysis)
    _progress(progress, 0.96, "Guardando DXF de escurrimientos…")
    document.saveas(target)
    _progress(progress, 1.0, "DXF de escurrimientos terminado")
    return target
