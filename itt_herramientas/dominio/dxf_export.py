from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

from .osm_vector import VectorPath, road_width_m


def _clean_text(value: str) -> str:
    return " ".join((value or "").replace("\r", " ").replace("\n", " ").split())


def generate_detailed_dxf(data, output: str | Path) -> Path:
    if len(data.points) < 3:
        raise ValueError("Dibuja el área de selección antes de exportar el DXF.")
    if not data.features:
        raise ValueError("Vectoriza la geometría de OpenStreetMap antes de exportar el DXF.")
    try:
        import ezdxf
        from ezdxf import units
        from shapely.geometry import LineString, Polygon
        from shapely.ops import unary_union
    except ImportError as exc:
        raise RuntimeError("Falta ezdxf o Shapely. Instala las dependencias antes de exportar CAD.") from exc

    from .location_sketch import _utm_zone, latlon_to_utm, layer_is_visible, visible_features

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    zone = _utm_zone(data.points)
    boundary = [latlon_to_utm(point.latitude, point.longitude, zone) for point in data.points]
    eastings = [value[0] for value in boundary]
    northings = [value[1] for value in boundary]
    diagonal = math.hypot(max(eastings) - min(eastings), max(northings) - min(northings))
    text_height = max(0.75, min(5.0, diagonal / 80 if diagonal else 2.5))
    features = visible_features(data)
    show_labels = layer_is_visible(data, "labels")

    doc = ezdxf.new("R2010", setup=True)
    doc.units = units.M
    doc.header["$INSUNITS"] = 6
    doc.header["$MEASUREMENT"] = 1
    msp = doc.modelspace()
    layer_specs = {
        "LIMITE_AREA": (4, (0, 190, 230)),
        "EDIFICIOS": (8, (88, 96, 104)), "EDIFICIOS_HATCH": (254, (205, 210, 214)),
        "VIALIDADES_EJE": (30, (220, 115, 25)), "VIALIDADES_BORDE": (8, (95, 95, 95)),
        "BANQUETAS_SENDEROS": (9, (150, 110, 70)),
        "AGUA": (5, (35, 120, 185)), "AGUA_HATCH": (151, (170, 220, 245)),
        "VEGETACION": (3, (55, 135, 65)), "VEGETACION_HATCH": (91, (195, 225, 190)),
        "RECREACION": (2, (184, 139, 39)), "RECREACION_HATCH": (52, (238, 220, 160)),
        "USO_SUELO": (33, (145, 117, 78)), "USO_SUELO_HATCH": (43, (232, 220, 200)),
        "ESTACIONAMIENTOS": (9, (105, 115, 125)), "ESTACIONAMIENTOS_HATCH": (253, (220, 223, 226)),
        "FERROCARRIL": (6, (125, 75, 150)), "BARRERAS": (33, (105, 75, 55)),
        "ELECTRICO": (1, (220, 55, 40)), "ESTRUCTURAS": (32, (120, 95, 70)),
        "EQUIPAMIENTO": (2, (210, 160, 20)), "OTROS": (9, (100, 120, 135)),
        "CURVAS_MENORES": (32, (155, 112, 78)), "CURVAS_MAESTRAS": (34, (112, 73, 48)),
        "COTAS_CURVAS": (34, None),
        "PUNTOS_OSM": (1, (220, 45, 35)), "TEXTOS": (7, None), "DATOS": (2, None),
    }
    for name, (aci, rgb) in layer_specs.items():
        layer = doc.layers.add(name, color=aci)
        if rgb is not None:
            layer.rgb = rgb

    def xy_points(vector_path: VectorPath) -> list[tuple[float, float]]:
        return [latlon_to_utm(latitude, longitude, zone)[:2] for latitude, longitude in vector_path.points]

    def add_polyline(layer: str, points, closed=False, width=0.0):
        clean = [(float(x), float(y)) for x, y, *_ in points]
        if closed and len(clean) > 2 and clean[0] == clean[-1]:
            clean = clean[:-1]
        if len(clean) < 2:
            return None
        attribs = {"layer": layer}
        if width:
            attribs["const_width"] = width
        return msp.add_lwpolyline(clean, close=closed and len(clean) >= 3, dxfattribs=attribs)

    def add_contour(points, elevation: float, major: bool):
        clean = [(float(x), float(y), elevation) for x, y, *_ in points]
        if len(clean) >= 2:
            return msp.add_polyline3d(clean, dxfattribs={"layer": "CURVAS_MAESTRAS" if major else "CURVAS_MENORES"})
        return None

    def add_hatch(layer: str, rings: list[tuple[list[tuple[float, float]], str]], rgb, pattern: str | None = None):
        valid = [(points[:-1] if len(points) > 2 and points[0] == points[-1] else points, role) for points, role in rings if len(points) >= 4]
        if not valid:
            return
        hatch = msp.add_hatch(dxfattribs={"layer": layer})
        if pattern:
            hatch.set_pattern_fill(pattern, color=256, scale=max(0.6, text_height * 0.65), angle=0)
        else:
            hatch.set_solid_fill(color=256, rgb=rgb)
        hatch.dxf.associative = 0
        hatch.dxf.hatch_style = 0
        for points, role in valid:
            hatch.paths.add_polyline_path(points, is_closed=True, flags=1 if role != "inner" else 0)
        return hatch

    def polygon_parts(geometry):
        if geometry.is_empty:
            return []
        if geometry.geom_type == "Polygon":
            return [geometry]
        if hasattr(geometry, "geoms"):
            return [part for item in geometry.geoms for part in polygon_parts(item)]
        return []

    boundary_xy = [(value[0], value[1]) for value in boundary]
    selection_polygon = Polygon(boundary_xy)
    if not selection_polygon.is_valid:
        selection_polygon = selection_polygon.buffer(0)
    if layer_is_visible(data, "selection"):
        add_polyline("LIMITE_AREA", boundary_xy, True, max(0.12, text_height * 0.06))

    category_layers = {
        "building": "EDIFICIOS", "parking": "ESTACIONAMIENTOS", "water": "AGUA",
        "vegetation": "VEGETACION", "recreation": "RECREACION", "railway": "FERROCARRIL", "barrier": "BARRERAS",
        "landuse": "USO_SUELO",
        "power": "ELECTRICO", "structure": "ESTRUCTURAS", "amenity": "EQUIPAMIENTO",
        "other": "OTROS",
    }
    area_hatches = {
        "building": ("EDIFICIOS_HATCH", (205, 210, 214), "ANSI31"),
        "parking": ("ESTACIONAMIENTOS_HATCH", (220, 223, 226), "ANSI37"),
        "water": ("AGUA_HATCH", (170, 220, 245), "ANSI37"),
        "vegetation": ("VEGETACION_HATCH", (195, 225, 190), "ANSI31"),
        "recreation": ("RECREACION_HATCH", (238, 220, 160), "ANSI37"),
        "landuse": ("USO_SUELO_HATCH", (232, 220, 200), "ANSI37"),
        "amenity": ("EQUIPAMIENTO", (235, 210, 130), None),
    }
    hatch_geometries = defaultdict(list)
    hatch_styles = {layer: (rgb, pattern) for layer, rgb, pattern in area_hatches.values()}
    hatch_simplify = max(0.08, min(0.75, diagonal / 4000 if diagonal else 0.10))

    def queue_hatch_geometry(layer: str, geometry):
        for polygon in polygon_parts(geometry):
            if polygon.is_empty or polygon.area <= 0.002:
                continue
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            if not polygon.is_empty:
                hatch_geometries[layer].append(polygon)

    def queue_hatch_rings(layer: str, rings):
        outers = [points for points, role in rings if role != "inner" and len(points) >= 4]
        inners = [points for points, role in rings if role == "inner" and len(points) >= 4]
        for index, outer in enumerate(outers):
            try:
                holes = inners if len(outers) == 1 and index == 0 else []
                queue_hatch_geometry(layer, Polygon(outer, holes))
            except Exception:
                continue

    def flush_optimized_hatches():
        max_paths_per_hatch = 120
        for layer, geometries in hatch_geometries.items():
            if not geometries:
                continue
            try:
                merged = unary_union(geometries)
            except Exception:
                merged = geometries
            polygons = polygon_parts(merged) if not isinstance(merged, list) else [part for geometry in merged for part in polygon_parts(geometry)]
            optimized = []
            for polygon in polygons:
                simplified = polygon.simplify(hatch_simplify, preserve_topology=True)
                optimized.extend(polygon_parts(simplified))
            rgb, pattern = hatch_styles[layer]
            ring_batch = []
            path_count = 0
            for polygon in optimized:
                rings = [(list(polygon.exterior.coords), "outer")]
                rings.extend((list(interior.coords), "inner") for interior in polygon.interiors)
                if ring_batch and path_count + len(rings) > max_paths_per_hatch:
                    add_hatch(layer, ring_batch, rgb, pattern)
                    ring_batch = []
                    path_count = 0
                ring_batch.extend(rings)
                path_count += len(rings)
            if ring_batch:
                add_hatch(layer, ring_batch, rgb, pattern)
    labelled: set[str] = set()
    for feature in features:
        if feature.category == "contour":
            try:
                elevation = float(feature.tags.get("ele", "0"))
            except ValueError:
                elevation = 0.0
            major = feature.tags.get("major") == "yes"
            longest = []
            for vector_path in feature.paths:
                coordinates = xy_points(vector_path)
                add_contour(coordinates, elevation, major)
                if len(coordinates) > len(longest):
                    longest = coordinates
            if show_labels and major and longest:
                x, y = longest[len(longest) // 2]
                msp.add_text(f"{elevation:g} m", dxfattribs={
                    "layer": "COTAS_CURVAS", "insert": (x, y, elevation), "height": text_height * 0.52,
                })
            continue
        if feature.category == "road":
            road_width = road_width_m(feature.tags)
            highway = feature.tags.get("highway", "")
            center_layer = "BANQUETAS_SENDEROS" if highway in {"footway", "path", "cycleway", "steps"} else "VIALIDADES_EJE"
            label_coordinates = []
            for vector_path in feature.paths:
                coordinates = xy_points(vector_path)
                if len(coordinates) < 2:
                    continue
                if len(coordinates) > len(label_coordinates):
                    label_coordinates = coordinates
                add_polyline(center_layer, coordinates, False, max(0.08, min(0.25, road_width / 35)))
                corridor = LineString(coordinates).buffer(road_width / 2, cap_style=2, join_style=2).intersection(selection_polygon)
                for polygon in polygon_parts(corridor):
                    outer = list(polygon.exterior.coords)
                    rings = [(outer, "outer")] + [(list(inner.coords), "inner") for inner in polygon.interiors]
                    add_polyline("VIALIDADES_BORDE", outer, True, max(0.08, text_height * 0.035))
                    for inner in polygon.interiors:
                        add_polyline("VIALIDADES_BORDE", list(inner.coords), True, max(0.08, text_height * 0.035))
            if show_labels and feature.name and feature.name not in labelled and len(label_coordinates) >= 2:
                middle = len(label_coordinates) // 2
                x, y = label_coordinates[middle]
                x0, y0 = label_coordinates[max(0, middle - 1)]
                angle = math.degrees(math.atan2(y - y0, x - x0))
                if angle > 90:
                    angle -= 180
                elif angle < -90:
                    angle += 180
                msp.add_text(_clean_text(feature.name), dxfattribs={
                    "layer": "TEXTOS", "insert": (x, y), "height": text_height * 0.58, "rotation": angle,
                })
                labelled.add(feature.name)
            continue

        layer_name = category_layers.get(feature.category, "OTROS")
        rings = []
        feature_coordinates = []
        for vector_path in feature.paths:
            coordinates = xy_points(vector_path)
            if not coordinates:
                continue
            if len(coordinates) == 1:
                x, y = coordinates[0]
                radius = max(0.22, text_height * 0.14)
                msp.add_circle((x, y), radius, dxfattribs={"layer": "PUNTOS_OSM"})
                msp.add_point((x, y), dxfattribs={"layer": layer_name})
                feature_coordinates = coordinates
                continue
            add_polyline(layer_name, coordinates, vector_path.closed, max(0.08, text_height * 0.035))
            if vector_path.closed:
                rings.append((coordinates, vector_path.role))
            if not feature_coordinates:
                feature_coordinates = coordinates
        hatch_spec = area_hatches.get(feature.category)
        if hatch_spec and rings:
            queue_hatch_rings(hatch_spec[0], rings)

        label = feature.name
        if feature.category == "building" and not label:
            levels = feature.tags.get("building:levels")
            if levels:
                label = f"{levels} niveles"
        if show_labels and label and label not in labelled and feature_coordinates:
            x = sum(point[0] for point in feature_coordinates) / len(feature_coordinates)
            y = sum(point[1] for point in feature_coordinates) / len(feature_coordinates)
            msp.add_text(_clean_text(label), dxfattribs={"layer": "TEXTOS", "insert": (x, y), "height": text_height * 0.62})
            labelled.add(label)

    flush_optimized_hatches()

    min_e, max_n = min(eastings), max(northings)
    title_y = max_n + text_height * 8
    metadata = (
        data.title or "Croquis de ubicación",
        f"{data.project} | {data.location}".strip(" |"),
        f"{len(features)} elementos visibles | WGS84 / UTM zona {zone}{boundary[0][3]}",
        "Anchos viales: etiqueta width/lanes OSM o estimación por clasificación funcional",
        f"Hatches de áreas optimizados | calles sin hatch | tolerancia {hatch_simplify:.2f} m",
        "Datos cartográficos © OpenStreetMap contributors | ODbL",
    )
    contour_features = [feature for feature in features if feature.category == "contour"]
    if contour_features:
        interval = contour_features[0].tags.get("interval", data.contour_interval)
        metadata += (f"Curvas de nivel 3D cada {interval} m | Elevación: Mapzen Terrain Tiles / AWS Open Data",)
    for row, text in enumerate(metadata):
        if text:
            msp.add_text(_clean_text(text), dxfattribs={
                "layer": "DATOS", "insert": (min_e, title_y - row * text_height * 1.5),
                "height": text_height * (1.25 if row == 0 else 0.78),
            })

    doc.saveas(target)
    return target
