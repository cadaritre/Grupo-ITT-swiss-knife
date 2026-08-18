from __future__ import annotations

import colorsys
import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from .branding import active_profile


Coord = tuple[float, float, float]
ProgressCallback = Callable[[float, str], None]


def _notify_progress(callback: ProgressCallback | None, value: float, message: str):
    if callback:
        callback(max(0.0, min(1.0, value)), message)


@dataclass
class GeoFeature:
    geometry_type: str
    parts: list[list[Coord]]
    layer: str = "0"
    name: str = ""
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def point_count(self) -> int:
        return sum(len(part) for part in self.parts)


@dataclass
class GeoDataset:
    features: list[GeoFeature] = field(default_factory=list)
    source_format: str = ""
    source_name: str = ""
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    def layer_counts(self) -> dict[str, int]:
        return dict(Counter(feature.layer or "0" for feature in self.features))

    def filtered(self, layers: set[str] | None = None) -> "GeoDataset":
        if layers is None:
            return self
        return GeoDataset(
            features=[feature for feature in self.features if (feature.layer or "0") in layers],
            source_format=self.source_format,
            source_name=self.source_name,
            warnings=list(self.warnings),
            metadata=dict(self.metadata),
        )

    def all_coordinates(self):
        for feature in self.features:
            for part in feature.parts:
                yield from part

    def bounds(self) -> tuple[float, float, float, float] | None:
        coordinates = list(self.all_coordinates())
        if not coordinates:
            return None
        longitudes = [coordinate[0] for coordinate in coordinates]
        latitudes = [coordinate[1] for coordinate in coordinates]
        return min(longitudes), min(latitudes), max(longitudes), max(latitudes)

    def geometry_counts(self) -> dict[str, int]:
        return dict(Counter(feature.geometry_type for feature in self.features))


def _dxf_coordinate_samples(document, limit: int = 4000) -> list[tuple[float, float]]:
    samples: list[tuple[float, float]] = []

    def add(value):
        try:
            x, y = float(value[0]), float(value[1])
            if math.isfinite(x) and math.isfinite(y) and abs(x) < 1e15 and abs(y) < 1e15:
                samples.append((x, y))
        except Exception:
            pass

    add(document.header.get("$EXTMIN"))
    add(document.header.get("$EXTMAX"))
    for entity in document.modelspace():
        try:
            kind = entity.dxftype()
            if kind == "LINE":
                add(entity.dxf.start)
                add(entity.dxf.end)
            elif kind == "LWPOLYLINE":
                for point in entity.get_points("xy"):
                    add(point)
            elif kind == "POLYLINE":
                for vertex in entity.vertices:
                    add(vertex.dxf.location)
            elif kind in {"POINT", "TEXT", "MTEXT", "INSERT"}:
                add(getattr(entity.dxf, "location", None) or getattr(entity.dxf, "insert", None))
            elif kind in {"CIRCLE", "ARC", "ELLIPSE"}:
                add(entity.dxf.center)
            elif kind in {"3DFACE", "SOLID", "TRACE"}:
                for index in range(4):
                    add(getattr(entity.dxf, f"vtx{index}"))
        except Exception:
            continue
        if len(samples) >= limit:
            break
    return samples


def _transformers(zone: int, hemisphere: str):
    try:
        from pyproj import CRS, Transformer
    except ImportError as exc:
        raise RuntimeError("Falta pyproj, necesario para transformar coordenadas UTM.") from exc
    zone = int(zone)
    if not 1 <= zone <= 60:
        raise ValueError("La zona UTM debe estar entre 1 y 60.")
    south = str(hemisphere).upper() == "S"
    utm = CRS.from_epsg((32700 if south else 32600) + zone)
    geographic = CRS.from_epsg(4326)
    return (
        Transformer.from_crs(utm, geographic, always_xy=True),
        Transformer.from_crs(geographic, utm, always_xy=True),
    )


def _coord(value) -> Coord:
    return float(value[0]), float(value[1]), float(value[2] if len(value) > 2 else 0.0)


def _clean_layer(value: str) -> str:
    clean = re.sub(r'[<>/\\":;?*|=,]', "_", (value or "0").strip())
    return clean[:255] or "0"


def _flatten_entity(entity, tolerance: float) -> tuple[str, list[list[Coord]]] | None:
    kind = entity.dxftype()
    if kind == "POINT":
        return "Point", [[_coord(entity.dxf.location)]]
    if kind == "LINE":
        return "LineString", [[_coord(entity.dxf.start), _coord(entity.dxf.end)]]
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        try:
            from ezdxf.path import make_path

            points = [_coord(vertex) for vertex in make_path(entity).flattening(distance=tolerance, segments=8)]
        except Exception:
            if kind == "LWPOLYLINE":
                elevation = float(getattr(entity.dxf, "elevation", 0.0) or 0.0)
                points = [(float(x), float(y), elevation) for x, y, *_ in entity.get_points("xy")]
            else:
                points = [_coord(vertex.dxf.location) for vertex in entity.vertices]
        closed = bool(getattr(entity, "closed", False) or getattr(entity, "is_closed", False))
        if closed and points and points[0] != points[-1]:
            points.append(points[0])
        return ("Polygon" if closed and len(points) >= 4 else "LineString"), [points]
    if kind in {"CIRCLE", "ARC", "ELLIPSE", "SPLINE"}:
        try:
            from ezdxf.path import make_path

            points = [_coord(vertex) for vertex in make_path(entity).flattening(distance=tolerance, segments=12)]
        except Exception:
            return None
        closed = kind in {"CIRCLE"} or bool(getattr(entity, "closed", False))
        if closed and points and points[0] != points[-1]:
            points.append(points[0])
        return ("Polygon" if closed else "LineString"), [points]
    if kind in {"3DFACE", "SOLID", "TRACE"}:
        points = [_coord(getattr(entity.dxf, f"vtx{index}")) for index in range(4)]
        while len(points) > 3 and points[-1] == points[-2]:
            points.pop()
        if points and points[0] != points[-1]:
            points.append(points[0])
        return "Polygon", [points]
    if kind in {"TEXT", "MTEXT"}:
        location = getattr(entity.dxf, "insert", None)
        if location is None:
            return None
        return "Point", [[_coord(location)]]
    return None


def _entity_name(entity) -> str:
    kind = entity.dxftype()
    if kind == "TEXT":
        return str(entity.dxf.text or "")
    if kind == "MTEXT":
        try:
            return entity.plain_text()
        except Exception:
            return str(entity.text or "")
    return ""


def read_dxf(
    path: str | Path,
    zone: int = 13,
    hemisphere: str = "N",
    curve_tolerance: float = 0.5,
    progress: ProgressCallback | None = None,
) -> GeoDataset:
    try:
        import ezdxf
    except ImportError as exc:
        raise RuntimeError("Falta ezdxf, necesario para leer archivos DXF.") from exc

    source = Path(path)
    _notify_progress(progress, 0.03, "Abriendo estructura del DXF…")
    document = ezdxf.readfile(source)
    coordinate_samples = _dxf_coordinate_samples(document)
    to_wgs84, _ = _transformers(zone, hemisphere)
    features: list[GeoFeature] = []
    projected_xy: list[tuple[float, float]] = []
    skipped = Counter()

    def append_entity(entity, inherited_layer: str | None = None, depth: int = 0):
        kind = entity.dxftype()
        if kind == "INSERT" and depth < 8:
            try:
                for virtual in entity.virtual_entities():
                    append_entity(virtual, inherited_layer or str(entity.dxf.layer), depth + 1)
            except Exception:
                skipped[kind] += 1
            return
        flattened = _flatten_entity(entity, max(0.01, curve_tolerance))
        if not flattened:
            if kind not in {"HATCH", "IMAGE", "WIPEOUT", "VIEWPORT", "DIMENSION", "LEADER", "MLEADER"}:
                skipped[kind] += 1
            return
        geometry_type, raw_parts = flattened
        parts: list[list[Coord]] = []
        for raw_part in raw_parts:
            part: list[Coord] = []
            for x, y, z in raw_part:
                easting, northing = x, y
                longitude, latitude = to_wgs84.transform(easting, northing)
                if not (math.isfinite(longitude) and math.isfinite(latitude)):
                    continue
                projected_xy.append((easting, northing))
                part.append((longitude, latitude, z))
            if part:
                parts.append(part)
        if not parts:
            return
        layer = inherited_layer or str(getattr(entity.dxf, "layer", "0") or "0")
        features.append(GeoFeature(
            geometry_type=geometry_type,
            parts=parts,
            layer=layer,
            name=_entity_name(entity),
            properties={"dxf_type": kind},
        ))

    entities = list(document.modelspace())
    for entity_index, entity in enumerate(entities):
        if entity_index % 400 == 0:
            _notify_progress(progress, 0.10 + 0.84 * entity_index / max(len(entities), 1), f"Transformando entidad {entity_index:,} de {len(entities):,}…")
        append_entity(entity)

    warnings: list[str] = []
    if projected_xy:
        eastings = [point[0] for point in projected_xy]
        northings = [point[1] for point in projected_xy]
        typical = sum(100_000 <= x <= 900_000 and 0 <= y <= 10_000_000 for x, y in projected_xy)
        if typical < len(projected_xy) * 0.8:
            warnings.append("Las coordenadas no parecen UTM. El archivo podría usar un origen local.")
    if skipped:
        description = ", ".join(f"{kind}: {count}" for kind, count in skipped.most_common(6))
        warnings.append(f"Entidades no compatibles omitidas: {description}.")
    metadata = {
        "utm_zone": f"{int(zone)}{str(hemisphere).upper()}",
    }
    if coordinate_samples:
        raw_x = [point[0] for point in coordinate_samples]
        raw_y = [point[1] for point in coordinate_samples]
        metadata.update({
            "raw_bounds": f"X {min(raw_x):.3f}–{max(raw_x):.3f} · Y {min(raw_y):.3f}–{max(raw_y):.3f}",
            "utm_bounds": (
                f"E {min(raw_x):.3f}–{max(raw_x):.3f} · "
                f"N {min(raw_y):.3f}–{max(raw_y):.3f}"
            ),
        })
    _notify_progress(progress, 1.0, f"DXF leído · {len(features):,} elementos compatibles")
    return GeoDataset(
        features=features,
        source_format="DXF",
        source_name=source.name,
        warnings=warnings,
        metadata=metadata,
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_coordinates(text: str | None) -> list[Coord]:
    result: list[Coord] = []
    for token in (text or "").replace("\n", " ").replace("\t", " ").split():
        values = token.split(",")
        if len(values) < 2:
            continue
        try:
            result.append((float(values[0]), float(values[1]), float(values[2]) if len(values) > 2 and values[2] else 0.0))
        except ValueError:
            continue
    return result


def _child_text(node: ET.Element, name: str) -> str:
    for child in node.iter():
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _parse_kml_geometry(node: ET.Element) -> list[tuple[str, list[list[Coord]]]]:
    kind = _local_name(node.tag)
    if kind == "Point":
        coordinates = _parse_coordinates(_child_text(node, "coordinates"))
        return [("Point", [[coordinates[0]]])] if coordinates else []
    if kind == "LineString":
        coordinates = _parse_coordinates(_child_text(node, "coordinates"))
        return [("LineString", [coordinates])] if coordinates else []
    if kind == "Polygon":
        rings: list[list[Coord]] = []
        for boundary in node:
            boundary_kind = _local_name(boundary.tag)
            if boundary_kind not in {"outerBoundaryIs", "innerBoundaryIs"}:
                continue
            coordinates = _parse_coordinates(_child_text(boundary, "coordinates"))
            if coordinates:
                rings.append(coordinates)
        return [("Polygon", rings)] if rings else []
    if kind in {"MultiGeometry", "GeometryCollection"}:
        result = []
        for child in node:
            result.extend(_parse_kml_geometry(child))
        return result
    return []


def read_kml(path: str | Path, progress: ProgressCallback | None = None) -> GeoDataset:
    source = Path(path)
    _notify_progress(progress, 0.05, "Abriendo estructura KML/KMZ…")
    if source.suffix.lower() == ".kmz":
        with zipfile.ZipFile(source) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".kml")]
            if not names:
                raise ValueError("El KMZ no contiene un archivo KML.")
            preferred = next((name for name in names if Path(name).name.lower() == "doc.kml"), names[0])
            payload = archive.read(preferred)
    else:
        payload = source.read_bytes()
    root = ET.fromstring(payload)
    features: list[GeoFeature] = []

    def parse_placemark(placemark: ET.Element, folder_layer: str):
        name = ""
        layer = folder_layer or "KML"
        properties: dict[str, str] = {}
        for child in placemark:
            child_kind = _local_name(child.tag)
            if child_kind == "name":
                name = (child.text or "").strip()
            elif child_kind == "ExtendedData":
                for data_node in child.iter():
                    if _local_name(data_node.tag) == "Data":
                        key = data_node.attrib.get("name", "")
                        value = _child_text(data_node, "value")
                        if key:
                            properties[key] = value
        layer = properties.get("layer") or properties.get("Layer") or layer
        geometries = []
        for child in placemark:
            if _local_name(child.tag) in {"Point", "LineString", "Polygon", "MultiGeometry", "GeometryCollection"}:
                geometries.extend(_parse_kml_geometry(child))
        for geometry_type, parts in geometries:
            features.append(GeoFeature(geometry_type, parts, layer, name, dict(properties)))

    def walk(node: ET.Element, folder_layer: str = ""):
        kind = _local_name(node.tag)
        current_layer = folder_layer
        if kind == "Folder":
            direct_name = next(((child.text or "").strip() for child in node if _local_name(child.tag) == "name"), "")
            current_layer = direct_name or folder_layer
        for child in node:
            child_kind = _local_name(child.tag)
            if child_kind == "Placemark":
                parse_placemark(child, current_layer)
            elif child_kind in {"kml", "Document", "Folder"}:
                walk(child, current_layer)

    _notify_progress(progress, 0.35, "Interpretando carpetas y geometrías…")
    walk(root)
    if not features:
        raise ValueError("No se encontraron puntos, líneas o polígonos en el archivo.")
    _notify_progress(progress, 1.0, f"{len(features):,} elementos KML leídos")
    return GeoDataset(features, source.suffix[1:].upper(), source.name, [])


def read_geospatial(
    path: str | Path,
    zone: int = 13,
    hemisphere: str = "N",
    curve_tolerance: float = 0.5,
    progress: ProgressCallback | None = None,
) -> GeoDataset:
    suffix = Path(path).suffix.lower()
    if suffix == ".dxf":
        return read_dxf(path, zone, hemisphere, curve_tolerance, progress)
    if suffix in {".kml", ".kmz"}:
        return read_kml(path, progress)
    raise ValueError("Selecciona un archivo DXF, KML o KMZ.")


def suggested_utm(dataset: GeoDataset) -> tuple[int, str]:
    bounds = dataset.bounds()
    if not bounds:
        return 13, "N"
    longitude = (bounds[0] + bounds[2]) / 2
    latitude = (bounds[1] + bounds[3]) / 2
    zone = max(1, min(60, int((longitude + 180) / 6) + 1))
    return zone, "N" if latitude >= 0 else "S"


def _layer_color(layer: str) -> tuple[int, int, int]:
    seed = sum((index + 1) * ord(character) for index, character in enumerate(layer or "0")) % 360
    red, green, blue = colorsys.hsv_to_rgb(seed / 360, 0.67, 0.78)
    return round(red * 255), round(green * 255), round(blue * 255)


def layer_color(layer: str) -> str:
    return "#%02X%02X%02X" % _layer_color(layer)


def _kml_color(layer: str, alpha: int = 255) -> str:
    red, green, blue = _layer_color(layer)
    return f"{alpha:02x}{blue:02x}{green:02x}{red:02x}"


def _coordinates_text(points: list[Coord], ground_clamped: bool = False) -> str:
    return " ".join(
        f"{longitude:.9f},{latitude:.9f},{0.0 if ground_clamped else altitude:.3f}"
        for longitude, latitude, altitude in points
    )


def write_kml(
    dataset: GeoDataset,
    path: str | Path,
    selected_layers: set[str] | None = None,
    include_names: bool = True,
    clamp_to_ground: bool = True,
    progress: ProgressCallback | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    selected = dataset.filtered(selected_layers)
    namespace = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", namespace)
    kml = ET.Element(f"{{{namespace}}}kml")
    document = ET.SubElement(kml, f"{{{namespace}}}Document")
    ET.SubElement(document, f"{{{namespace}}}name").text = target.stem
    layers = sorted(selected.layer_counts(), key=str.casefold)
    style_ids: dict[str, str] = {}
    for index, layer in enumerate(layers):
        style_id = f"layer_{index + 1}"
        style_ids[layer] = style_id
        style = ET.SubElement(document, f"{{{namespace}}}Style", {"id": style_id})
        line_style = ET.SubElement(style, f"{{{namespace}}}LineStyle")
        ET.SubElement(line_style, f"{{{namespace}}}color").text = _kml_color(layer)
        ET.SubElement(line_style, f"{{{namespace}}}width").text = "2"
        poly_style = ET.SubElement(style, f"{{{namespace}}}PolyStyle")
        ET.SubElement(poly_style, f"{{{namespace}}}color").text = _kml_color(layer, 92)
        ET.SubElement(poly_style, f"{{{namespace}}}fill").text = "1"
        ET.SubElement(poly_style, f"{{{namespace}}}outline").text = "1"
        icon_style = ET.SubElement(style, f"{{{namespace}}}IconStyle")
        ET.SubElement(icon_style, f"{{{namespace}}}color").text = _kml_color(layer)

    by_layer: dict[str, list[GeoFeature]] = {layer: [] for layer in layers}
    for feature in selected.features:
        by_layer.setdefault(feature.layer or "0", []).append(feature)
    processed = 0
    total_features = len(selected.features)
    for layer, features in by_layer.items():
        folder = ET.SubElement(document, f"{{{namespace}}}Folder")
        ET.SubElement(folder, f"{{{namespace}}}name").text = layer
        for feature_index, feature in enumerate(features, 1):
            if processed % 300 == 0:
                _notify_progress(progress, 0.05 + 0.86 * processed / max(total_features, 1), f"Escribiendo elemento {processed:,} de {total_features:,}…")
            processed += 1
            placemark = ET.SubElement(folder, f"{{{namespace}}}Placemark")
            if include_names and feature.name:
                ET.SubElement(placemark, f"{{{namespace}}}name").text = feature.name
            ET.SubElement(placemark, f"{{{namespace}}}styleUrl").text = f"#{style_ids[layer]}"
            extended = ET.SubElement(placemark, f"{{{namespace}}}ExtendedData")
            data_node = ET.SubElement(extended, f"{{{namespace}}}Data", {"name": "layer"})
            ET.SubElement(data_node, f"{{{namespace}}}value").text = layer
            for key, value in feature.properties.items():
                if key.lower() == "layer" or not value:
                    continue
                data_node = ET.SubElement(extended, f"{{{namespace}}}Data", {"name": str(key)})
                ET.SubElement(data_node, f"{{{namespace}}}value").text = str(value)
            if feature.geometry_type == "Point":
                geometry = ET.SubElement(placemark, f"{{{namespace}}}Point")
                ET.SubElement(geometry, f"{{{namespace}}}altitudeMode").text = "clampToGround" if clamp_to_ground else "absolute"
                ET.SubElement(geometry, f"{{{namespace}}}coordinates").text = _coordinates_text(feature.parts[0][:1], clamp_to_ground)
            elif feature.geometry_type == "LineString":
                geometry = ET.SubElement(placemark, f"{{{namespace}}}LineString")
                ET.SubElement(geometry, f"{{{namespace}}}tessellate").text = "1"
                ET.SubElement(geometry, f"{{{namespace}}}altitudeMode").text = "clampToGround" if clamp_to_ground else "absolute"
                ET.SubElement(geometry, f"{{{namespace}}}coordinates").text = _coordinates_text(feature.parts[0], clamp_to_ground)
            elif feature.geometry_type == "Polygon" and feature.parts:
                geometry = ET.SubElement(placemark, f"{{{namespace}}}Polygon")
                ET.SubElement(geometry, f"{{{namespace}}}tessellate").text = "1"
                ET.SubElement(geometry, f"{{{namespace}}}altitudeMode").text = "clampToGround" if clamp_to_ground else "absolute"
                for ring_index, ring in enumerate(feature.parts):
                    boundary_name = "outerBoundaryIs" if ring_index == 0 else "innerBoundaryIs"
                    boundary = ET.SubElement(geometry, f"{{{namespace}}}{boundary_name}")
                    linear = ET.SubElement(boundary, f"{{{namespace}}}LinearRing")
                    ET.SubElement(linear, f"{{{namespace}}}coordinates").text = _coordinates_text(ring, clamp_to_ground)
    _notify_progress(progress, 0.93, "Comprimiendo KML/KMZ…")
    payload = ET.tostring(kml, encoding="utf-8", xml_declaration=True)
    if target.suffix.lower() == ".kmz":
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("doc.kml", payload)
    else:
        target.write_bytes(payload)
    _notify_progress(progress, 1.0, "Archivo Google Earth terminado")
    return target


def write_dxf(
    dataset: GeoDataset,
    path: str | Path,
    zone: int = 13,
    hemisphere: str = "N",
    selected_layers: set[str] | None = None,
    add_labels: bool = True,
    add_hatches: bool = True,
    progress: ProgressCallback | None = None,
) -> Path:
    try:
        import ezdxf
        from ezdxf import units
    except ImportError as exc:
        raise RuntimeError("Falta ezdxf, necesario para escribir archivos DXF.") from exc
    _, to_utm = _transformers(zone, hemisphere)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    selected = dataset.filtered(selected_layers)
    document = ezdxf.new("R2010", setup=True)
    document.units = units.M
    document.header["$INSUNITS"] = 6
    document.header["$MEASUREMENT"] = 1
    modelspace = document.modelspace()
    layer_names: dict[str, str] = {}
    used: set[str] = set()
    for original in sorted(selected.layer_counts(), key=str.casefold):
        candidate = _clean_layer(original)
        base = candidate
        counter = 2
        while candidate.casefold() in used:
            candidate = f"{base[:245]}_{counter}"
            counter += 1
        used.add(candidate.casefold())
        layer_names[original] = candidate
        if candidate not in document.layers:
            layer = document.layers.new(candidate)
            layer.rgb = _layer_color(original)
    if add_labels and "ETIQUETAS_KML" not in document.layers:
        document.layers.new("ETIQUETAS_KML", dxfattribs={"color": 7})

    def projected(points: list[Coord]) -> list[Coord]:
        return [(float(to_utm.transform(lon, lat)[0]), float(to_utm.transform(lon, lat)[1]), float(alt)) for lon, lat, alt in points]

    total_features = len(selected.features)
    for feature_index, feature in enumerate(selected.features):
        if feature_index % 300 == 0:
            _notify_progress(progress, 0.05 + 0.88 * feature_index / max(total_features, 1), f"Escribiendo elemento CAD {feature_index:,} de {total_features:,}…")
        layer = layer_names[feature.layer or "0"]
        parts = [projected(part) for part in feature.parts if part]
        if not parts:
            continue
        if feature.geometry_type == "Point":
            x, y, z = parts[0][0]
            modelspace.add_point((x, y, z), dxfattribs={"layer": layer})
            label_at = (x, y, z)
        elif feature.geometry_type == "LineString":
            points = parts[0]
            if any(abs(point[2]) > 1e-8 for point in points):
                modelspace.add_polyline3d(points, dxfattribs={"layer": layer})
            else:
                modelspace.add_lwpolyline([(x, y) for x, y, _ in points], dxfattribs={"layer": layer})
            label_at = points[len(points) // 2]
        elif feature.geometry_type == "Polygon":
            outer = parts[0]
            clean_outer = outer[:-1] if len(outer) > 2 and outer[0] == outer[-1] else outer
            if any(abs(point[2]) > 1e-8 for point in clean_outer):
                modelspace.add_polyline3d(clean_outer + clean_outer[:1], dxfattribs={"layer": layer})
            else:
                modelspace.add_lwpolyline([(x, y) for x, y, _ in clean_outer], close=True, dxfattribs={"layer": layer})
            for inner in parts[1:]:
                clean_inner = inner[:-1] if len(inner) > 2 and inner[0] == inner[-1] else inner
                modelspace.add_lwpolyline([(x, y) for x, y, _ in clean_inner], close=True, dxfattribs={"layer": layer})
            if add_hatches and len(clean_outer) >= 3 and not any(abs(point[2]) > 1e-8 for part in parts for point in part):
                hatch = modelspace.add_hatch(dxfattribs={"layer": layer})
                hatch.set_solid_fill(color=256, rgb=_layer_color(feature.layer or "0"))
                hatch.transparency = 0.65
                hatch.paths.add_polyline_path([(x, y) for x, y, _ in clean_outer], is_closed=True, flags=1)
                for inner in parts[1:]:
                    clean_inner = inner[:-1] if len(inner) > 2 and inner[0] == inner[-1] else inner
                    hatch.paths.add_polyline_path([(x, y) for x, y, _ in clean_inner], is_closed=True, flags=0)
            label_at = (
                sum(point[0] for point in clean_outer) / len(clean_outer),
                sum(point[1] for point in clean_outer) / len(clean_outer),
                sum(point[2] for point in clean_outer) / len(clean_outer),
            )
        else:
            continue
        if add_labels and feature.name:
            modelspace.add_text(feature.name[:240], dxfattribs={
                "layer": "ETIQUETAS_KML", "insert": label_at, "height": 1.8,
            })
    metadata = (
        f"Conversión {active_profile().name} | WGS84 / UTM zona {int(zone)}{str(hemisphere).upper()}",
        f"Fuente: {dataset.source_name} | {len(selected.features)} elementos",
    )
    bounds = selected.bounds()
    if bounds:
        x, y = to_utm.transform(bounds[0], bounds[3])
        for row, value in enumerate(metadata):
            modelspace.add_text(value, dxfattribs={"layer": "ETIQUETAS_KML", "insert": (x, y + 6 - row * 2.5), "height": 1.5})
    _notify_progress(progress, 0.96, "Guardando archivo DXF…")
    document.saveas(target)
    _notify_progress(progress, 1.0, "DXF terminado")
    return target


def convert_file(
    dataset: GeoDataset,
    target: str | Path,
    zone: int = 13,
    hemisphere: str = "N",
    selected_layers: set[str] | None = None,
    add_labels: bool = True,
    add_hatches: bool = True,
    clamp_to_ground: bool = True,
    progress: ProgressCallback | None = None,
) -> Path:
    suffix = Path(target).suffix.lower()
    if suffix in {".kml", ".kmz"}:
        return write_kml(dataset, target, selected_layers, add_labels, clamp_to_ground, progress)
    if suffix == ".dxf":
        return write_dxf(dataset, target, zone, hemisphere, selected_layers, add_labels, add_hatches, progress)
    raise ValueError("El formato de salida debe ser DXF, KML o KMZ.")
