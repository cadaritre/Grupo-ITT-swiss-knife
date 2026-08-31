from __future__ import annotations

import json
import math
import re
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator

import numpy as np


SUPPORTED_EXTENSIONS = {".las", ".laz", ".e57", ".xyz", ".pts", ".txt", ".csv"}
UNIT_FACTORS = {
    "Metros": 1.0,
    "Milímetros": 0.001,
    "Centímetros": 0.01,
    "Pies internacionales": 0.3048,
    "Pies US survey": 1200.0 / 3937.0,
}


class RegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class CloudInfo:
    path: str
    format: str
    point_count: int | None
    scan_count: int | None = None
    has_rgb: bool = False
    has_intensity: bool = False


@dataclass(frozen=True)
class RegistrationResult:
    rotation: list[list[float]]
    translation: list[float]
    matrix: list[list[float]]
    residuals: list[float]
    rmse: float
    max_error: float
    pair_count: int
    source_unit_factor: float
    target_unit_factor: float
    independent_check: bool
    source_spread: float
    target_spread: float
    planar_residuals: list[float]
    vertical_differences: list[float]
    planar_rmse: float
    planar_max_error: float
    vertical_rmse: float
    vertical_max_difference: float
    vertical_bias: float
    yaw_degrees: float
    preserve_vertical: bool

    def transform(self, xyz: np.ndarray) -> np.ndarray:
        points = np.asarray(xyz, dtype=np.float64)
        matrix = np.asarray(self.matrix, dtype=np.float64)
        return points @ matrix[:3, :3].T + matrix[:3, 3]


@dataclass
class CloudChunk:
    xyz: np.ndarray
    rgb: np.ndarray | None = None
    intensity: np.ndarray | None = None
    classification: np.ndarray | None = None


@dataclass
class CloudVisualSample:
    """Bounded geometry and optional source colors for interactive viewers."""

    xyz: np.ndarray
    rgb: np.ndarray | None = None


def _validate_control_geometry(points: np.ndarray, label: str) -> float:
    centered = points - points.mean(axis=0)
    singular = np.linalg.svd(centered, compute_uv=False)
    spread = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    if spread <= 1e-9:
        raise RegistrationError(f"Los puntos de {label} coinciden o están demasiado juntos.")
    # Three valid 3D control points define a plane, so rank 2 is expected.
    if len(singular) < 2 or singular[1] <= max(singular[0], 1.0) * 1e-8:
        raise RegistrationError(
            f"Los puntos de {label} están alineados. Usa tres puntos separados que formen un triángulo."
        )
    return spread


def solve_rigid_registration(
    source_xyz,
    target_xyz,
    source_unit_factor: float = 1.0,
    target_unit_factor: float = 1.0,
) -> RegistrationResult:
    """Solve source -> target while preserving the absolute vertical axis.

    Unit factors only normalize explicitly selected input units to metres. No scale
    is estimated from the controls. The fit is deliberately constrained to a yaw
    rotation and XY translation: Z values are converted to metres but are never
    tilted or translated. Vertical discrepancies remain visible as quality-control
    residuals instead of silently deforming either survey.
    """
    source = np.asarray(source_xyz, dtype=np.float64)
    target = np.asarray(target_xyz, dtype=np.float64)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise RegistrationError("Las coordenadas de la nube móvil y la nube base deben tener la misma cantidad de filas XYZ.")
    if source.shape[0] < 3:
        raise RegistrationError("Se necesitan al menos tres pares de puntos de control.")
    if not np.isfinite(source).all() or not np.isfinite(target).all():
        raise RegistrationError("Todas las coordenadas deben ser números finitos.")
    if source_unit_factor <= 0 or target_unit_factor <= 0:
        raise RegistrationError("Las unidades seleccionadas no son válidas.")

    source_m = source * float(source_unit_factor)
    target_m = target * float(target_unit_factor)
    source_spread = _validate_control_geometry(source_m, "la nube móvil")
    target_spread = _validate_control_geometry(target_m, "la nube base")

    source_xy = source_m[:, :2]
    target_xy = target_m[:, :2]
    source_xy_spread = float(np.linalg.norm(np.ptp(source_xy, axis=0)))
    target_xy_spread = float(np.linalg.norm(np.ptp(target_xy, axis=0)))
    if source_xy_spread <= 1e-9 or target_xy_spread <= 1e-9:
        raise RegistrationError(
            "Los controles deben ocupar posiciones XY diferentes para calcular la orientación horizontal."
        )

    source_center_xy = source_xy.mean(axis=0)
    target_center_xy = target_xy.mean(axis=0)
    covariance = (source_xy - source_center_xy).T @ (target_xy - target_center_xy)
    u, _singular, vt = np.linalg.svd(covariance)
    rotation_xy = vt.T @ u.T
    if np.linalg.det(rotation_xy) < 0:
        vt[-1, :] *= -1
        rotation_xy = vt.T @ u.T

    rotation = np.eye(3, dtype=np.float64)
    rotation[:2, :2] = rotation_xy
    translation = np.zeros(3, dtype=np.float64)
    translation[:2] = target_center_xy - rotation_xy @ source_center_xy

    predicted = source_m @ rotation.T + translation
    differences = predicted - target_m
    planar_residuals = np.linalg.norm(differences[:, :2], axis=1)
    vertical_differences = differences[:, 2]
    residuals = np.linalg.norm(differences, axis=1)
    rmse = float(math.sqrt(float(np.mean(residuals**2))))
    max_error = float(np.max(residuals))
    planar_rmse = float(math.sqrt(float(np.mean(planar_residuals**2))))
    planar_max_error = float(np.max(planar_residuals))
    vertical_rmse = float(math.sqrt(float(np.mean(vertical_differences**2))))
    vertical_max_difference = float(np.max(np.abs(vertical_differences)))
    vertical_bias = float(np.mean(vertical_differences))
    yaw_degrees = float(math.degrees(math.atan2(rotation_xy[1, 0], rotation_xy[0, 0])))

    # This homogeneous matrix consumes raw source coordinates and emits metres.
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rotation * float(source_unit_factor)
    matrix[:3, 3] = translation
    return RegistrationResult(
        rotation=rotation.tolist(),
        translation=translation.tolist(),
        matrix=matrix.tolist(),
        residuals=residuals.tolist(),
        rmse=rmse,
        max_error=max_error,
        pair_count=int(source.shape[0]),
        source_unit_factor=float(source_unit_factor),
        target_unit_factor=float(target_unit_factor),
        independent_check=source.shape[0] > 3,
        source_spread=source_spread,
        target_spread=target_spread,
        planar_residuals=planar_residuals.tolist(),
        vertical_differences=vertical_differences.tolist(),
        planar_rmse=planar_rmse,
        planar_max_error=planar_max_error,
        vertical_rmse=vertical_rmse,
        vertical_max_difference=vertical_max_difference,
        vertical_bias=vertical_bias,
        yaw_degrees=yaw_degrees,
        preserve_vertical=True,
    )


def inspect_cloud(path: str | Path) -> CloudInfo:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    extension = source.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise RegistrationError(f"Formato no compatible: {extension or 'sin extensión'}")
    if extension in {".las", ".laz"}:
        import laspy

        with laspy.open(source) as reader:
            names = {name.casefold() for name in reader.header.point_format.dimension_names}
            return CloudInfo(
                str(source), extension[1:].upper(), int(reader.header.point_count),
                has_rgb={"red", "green", "blue"}.issubset(names),
                has_intensity="intensity" in names,
            )
    if extension == ".e57":
        import pye57

        cloud = pye57.E57(str(source))
        try:
            scan_count = int(cloud.scan_count)
            counts = []
            for index in range(scan_count):
                try:
                    header = cloud.get_header(index)
                    counts.append(int(header.point_count))
                except Exception:
                    counts = []
                    break
            return CloudInfo(
                str(source), "E57", sum(counts) if counts else None, scan_count=scan_count,
                has_rgb=True, has_intensity=True,
            )
        finally:
            close = getattr(cloud, "close", None)
            if callable(close):
                close()
    return CloudInfo(str(source), extension[1:].upper(), _ascii_declared_count(source))


def _ascii_declared_count(path: Path) -> int | None:
    if path.suffix.lower() != ".pts":
        return None
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
            first = handle.readline().strip()
        return int(first) if re.fullmatch(r"\d+", first) else None
    except (OSError, ValueError):
        return None


def iter_cloud_chunks(path: str | Path, chunk_size: int = 500_000) -> Iterator[CloudChunk]:
    source = Path(path)
    extension = source.suffix.lower()
    if extension in {".las", ".laz"}:
        yield from _iter_las(source, chunk_size)
    elif extension == ".e57":
        yield from _iter_e57(source, chunk_size)
    elif extension in {".xyz", ".pts", ".txt", ".csv"}:
        yield from _iter_ascii(source, chunk_size)
    else:
        raise RegistrationError(f"Formato no compatible: {extension or 'sin extensión'}")


def _iter_las(path: Path, chunk_size: int) -> Iterator[CloudChunk]:
    import laspy

    with laspy.open(path) as reader:
        names = {name.casefold() for name in reader.header.point_format.dimension_names}
        for points in reader.chunk_iterator(chunk_size):
            xyz = np.column_stack((np.asarray(points.x), np.asarray(points.y), np.asarray(points.z)))
            rgb = None
            if {"red", "green", "blue"}.issubset(names):
                rgb = np.column_stack((points.red, points.green, points.blue)).astype(np.uint16, copy=False)
            intensity = np.asarray(points.intensity, dtype=np.uint16) if "intensity" in names else None
            classification = (
                np.asarray(points.classification, dtype=np.uint8) if "classification" in names else None
            )
            yield CloudChunk(xyz.astype(np.float64, copy=False), rgb, intensity, classification)


def _iter_e57(path: Path, chunk_size: int) -> Iterator[CloudChunk]:
    import pye57

    cloud = pye57.E57(str(path))
    try:
        for scan_index in range(int(cloud.scan_count)):
            # pye57 materializes one scan. We immediately slice it so all later
            # processing and LAZ writing remain bounded. The UI warns about this
            # E57-specific limitation before processing very large files.
            try:
                data = cloud.read_scan(
                    scan_index, intensity=True, colors=True, transform=True, ignore_missing_fields=True
                )
            except TypeError:
                data = cloud.read_scan(scan_index, intensity=True, colors=True, transform=True)
            x = np.asarray(data["cartesianX"], dtype=np.float64)
            y = np.asarray(data["cartesianY"], dtype=np.float64)
            z = np.asarray(data["cartesianZ"], dtype=np.float64)
            intensity_all = data.get("intensity")
            red = data.get("colorRed")
            green = data.get("colorGreen")
            blue = data.get("colorBlue")
            for start in range(0, len(x), chunk_size):
                stop = min(start + chunk_size, len(x))
                rgb = None
                if red is not None and green is not None and blue is not None:
                    channels = np.column_stack((red[start:stop], green[start:stop], blue[start:stop]))
                    if np.issubdtype(channels.dtype, np.floating) and channels.size and channels.max() <= 1.0:
                        channels = channels * 65535.0
                    elif channels.size and channels.max() <= 255:
                        channels = channels * 257.0
                    rgb = np.clip(channels, 0, 65535).astype(np.uint16)
                intensity = None
                if intensity_all is not None:
                    values = np.asarray(intensity_all[start:stop])
                    if np.issubdtype(values.dtype, np.floating) and values.size:
                        maximum = float(np.nanmax(values))
                        if maximum <= 1.0:
                            values = values * 65535.0
                    intensity = np.nan_to_num(values).clip(0, 65535).astype(np.uint16)
                xyz = np.column_stack((x[start:stop], y[start:stop], z[start:stop]))
                yield CloudChunk(xyz, rgb, intensity, None)
    finally:
        close = getattr(cloud, "close", None)
        if callable(close):
            close()


def _iter_ascii(path: Path, chunk_size: int) -> Iterator[CloudChunk]:
    xyz_rows: list[tuple[float, float, float]] = []
    rgb_rows: list[tuple[int, int, int] | None] = []
    intensity_rows: list[int | None] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            if line_number == 1 and path.suffix.lower() == ".pts" and re.fullmatch(r"\d+", text):
                continue
            parts = [item for item in re.split(r"[,;\s]+", text) if item]
            if len(parts) < 3:
                continue
            try:
                values = [float(parts[index]) for index in range(min(len(parts), 7))]
            except ValueError:
                continue
            if not all(math.isfinite(value) for value in values[:3]):
                continue
            xyz_rows.append((values[0], values[1], values[2]))
            if len(values) >= 7:  # PTS: X Y Z intensity R G B
                intensity_rows.append(int(max(0, min(65535, values[3]))))
                colors = values[4:7]
                if max(colors) <= 255:
                    colors = [value * 257 for value in colors]
                rgb_rows.append(tuple(int(max(0, min(65535, value))) for value in colors))
            elif len(values) >= 6:  # XYZRGB
                intensity_rows.append(None)
                colors = values[3:6]
                if max(colors) <= 255:
                    colors = [value * 257 for value in colors]
                rgb_rows.append(tuple(int(max(0, min(65535, value))) for value in colors))
            else:
                rgb_rows.append(None)
                intensity_rows.append(
                    int(max(0, min(65535, values[3]))) if path.suffix.lower() == ".pts" and len(values) >= 4 else None
                )
            if len(xyz_rows) >= chunk_size:
                yield _ascii_chunk(xyz_rows, rgb_rows, intensity_rows)
                xyz_rows, rgb_rows, intensity_rows = [], [], []
    if xyz_rows:
        yield _ascii_chunk(xyz_rows, rgb_rows, intensity_rows)


def _ascii_chunk(xyz_rows, rgb_rows, intensity_rows) -> CloudChunk:
    rgb = None
    if rgb_rows and all(row is not None for row in rgb_rows):
        rgb = np.asarray(rgb_rows, dtype=np.uint16)
    intensity = None
    if intensity_rows and all(value is not None for value in intensity_rows):
        intensity = np.asarray(intensity_rows, dtype=np.uint16)
    return CloudChunk(np.asarray(xyz_rows, dtype=np.float64), rgb, intensity)


def sample_cloud(
    path: str | Path,
    unit_factor: float,
    max_points: int = 120_000,
    cancel_check: Callable[[], bool] | None = None,
) -> np.ndarray:
    """Read the cloud once and keep a bounded, approximately uniform sample."""
    info = inspect_cloud(path)
    total = info.point_count
    stride = max(1, math.ceil(total / max_points)) if total else None
    samples: list[np.ndarray] = []
    seen = 0
    rng = np.random.default_rng(20260831)
    priorities = np.empty(0, dtype=np.float64)
    reservoir = np.empty((0, 3), dtype=np.float64)
    for chunk in iter_cloud_chunks(path, 350_000):
        if cancel_check and cancel_check():
            raise InterruptedError("Muestreo cancelado")
        xyz = chunk.xyz
        if stride:
            start = (-seen) % stride
            selected = xyz[start::stride]
            if len(selected):
                samples.append(selected)
            seen += len(xyz)
        else:
            keys = rng.random(len(xyz))
            combined_points = np.vstack((reservoir, xyz))
            combined_keys = np.concatenate((priorities, keys))
            if len(combined_points) > max_points:
                indices = np.argpartition(combined_keys, max_points - 1)[:max_points]
                reservoir = combined_points[indices]
                priorities = combined_keys[indices]
            else:
                reservoir = combined_points
                priorities = combined_keys
    if stride:
        if not samples:
            raise RegistrationError("No se encontraron puntos válidos en el archivo.")
        result = np.vstack(samples)
        if len(result) > max_points:
            result = result[:max_points]
    else:
        result = reservoir
    if not len(result):
        raise RegistrationError("No se encontraron puntos válidos en el archivo.")
    return result.astype(np.float64, copy=False) * float(unit_factor)


def sample_cloud_visual(
    path: str | Path,
    unit_factor: float,
    max_points: int = 250_000,
    cancel_check: Callable[[], bool] | None = None,
) -> CloudVisualSample:
    """Return a deterministic bounded sample while keeping RGB aligned.

    The full-resolution merge never uses this sample. Known-size sources use a
    global stride so the entire file is represented; unknown-size text sources
    use a bounded priority reservoir.
    """
    info = inspect_cloud(path)
    total = info.point_count
    stride = max(1, math.ceil(total / max_points)) if total else None
    xyz_parts: list[np.ndarray] = []
    rgb_parts: list[np.ndarray] = []
    rgb_complete = True
    seen = 0
    rng = np.random.default_rng(20260831)
    reservoir_xyz = np.empty((0, 3), dtype=np.float64)
    reservoir_rgb = np.empty((0, 3), dtype=np.uint16)
    priorities = np.empty(0, dtype=np.float64)

    for chunk in iter_cloud_chunks(path, 350_000):
        if cancel_check and cancel_check():
            raise InterruptedError("Muestreo cancelado")
        if stride:
            start = (-seen) % stride
            indices = np.arange(start, len(chunk.xyz), stride, dtype=np.int64)
            if len(indices):
                xyz_parts.append(chunk.xyz[indices])
                if rgb_complete and chunk.rgb is not None:
                    rgb_parts.append(chunk.rgb[indices])
                else:
                    rgb_complete = False
            seen += len(chunk.xyz)
            continue

        keys = rng.random(len(chunk.xyz))
        combined_xyz = np.vstack((reservoir_xyz, chunk.xyz))
        combined_keys = np.concatenate((priorities, keys))
        if rgb_complete and chunk.rgb is not None:
            combined_rgb = np.vstack((reservoir_rgb, chunk.rgb.astype(np.uint16, copy=False)))
        else:
            rgb_complete = False
            combined_rgb = np.empty((0, 3), dtype=np.uint16)
        if len(combined_xyz) > max_points:
            indices = np.argpartition(combined_keys, max_points - 1)[:max_points]
            reservoir_xyz = combined_xyz[indices]
            priorities = combined_keys[indices]
            if rgb_complete:
                reservoir_rgb = combined_rgb[indices]
        else:
            reservoir_xyz = combined_xyz
            priorities = combined_keys
            if rgb_complete:
                reservoir_rgb = combined_rgb

    if stride:
        if not xyz_parts:
            raise RegistrationError("No se encontraron puntos válidos en el archivo.")
        xyz = np.vstack(xyz_parts)
        rgb = np.vstack(rgb_parts) if rgb_complete and rgb_parts else None
        if len(xyz) > max_points:
            xyz = xyz[:max_points]
            if rgb is not None:
                rgb = rgb[:max_points]
    else:
        xyz = reservoir_xyz
        rgb = reservoir_rgb if rgb_complete and len(reservoir_rgb) == len(reservoir_xyz) else None
    if not len(xyz):
        raise RegistrationError("No se encontraron puntos válidos en el archivo.")
    return CloudVisualSample(xyz.astype(np.float64, copy=False) * float(unit_factor), rgb)


def write_registration_report(
    path: str | Path,
    result: RegistrationResult,
    request: dict,
    output_path: str | Path,
    audit: dict | None = None,
) -> Path:
    target = Path(path)
    payload = {
        "schema": "grupo-itt.pointcloud-registration.v3",
        "base_scanner": request["scanner_path"],
        "moving_drone": request["drone_path"],
        "output_laz": str(output_path),
        "scanner_unit": request.get("scanner_unit", "Metros"),
        "drone_unit": request.get("drone_unit", "Metros"),
        "control_pairs": request.get("pairs", []),
        "registration": asdict(result),
        "point_audit": audit or {},
        "note": (
            "La matriz transforma XY de la nube de dron al sistema local del escáner mediante giro exclusivo "
            "sobre Z y traslación horizontal. Las elevaciones se conservan sin inclinación ni traslación vertical."
        ),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _queue_message(queue, kind: str, **values):
    if queue is not None:
        queue.put({"kind": kind, **values})


def run_merge_worker(request: dict, queue=None, cancel_event=None) -> None:
    """Multiprocessing entry point. All values in request are serializable."""
    partial_output: Path | None = None
    try:
        scanner_factor = float(request["scanner_factor"])
        drone_factor = float(request["drone_factor"])
        scanner_controls = np.asarray([row[:3] for row in request["pairs"]], dtype=np.float64)
        drone_controls = np.asarray([row[3:] for row in request["pairs"]], dtype=np.float64)
        # The scanner is always the fixed local base. The drone is the moving
        # cloud, even when its original coordinates are UTM.
        result = solve_rigid_registration(drone_controls, scanner_controls, drone_factor, scanner_factor)
        scanner_info = inspect_cloud(request["scanner_path"])
        drone_info = inspect_cloud(request["drone_path"])
        total = (scanner_info.point_count or 0) + (drone_info.point_count or 0)
        completed = 0

        import laspy

        output = Path(request["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        partial_output = output.with_name(output.name + ".partial")
        partial_output.unlink(missing_ok=True)
        header = laspy.LasHeader(point_format=7, version="1.4")
        header.scales = np.array([0.001, 0.001, 0.001])
        scanner_center = np.mean(scanner_controls * scanner_factor, axis=0)
        header.offsets = np.floor(scanner_center / 1000.0) * 1000.0
        header.add_extra_dim(
            laspy.ExtraBytesParams(name="source_id", type=np.uint8, description="0=escaner_base, 1=dron_ajustado")
        )

        with laspy.open(partial_output, mode="w", header=header, do_compress=True) as writer:
            clouds = (
                (request["scanner_path"], scanner_factor, 0, None),
                (request["drone_path"], drone_factor, 1, result),
            )
            for cloud_path, factor, source_id, registration in clouds:
                for chunk in iter_cloud_chunks(cloud_path, int(request.get("chunk_size", 500_000))):
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("Proceso cancelado por el usuario")
                    xyz = chunk.xyz * factor
                    if registration is not None:
                        rotation = np.asarray(registration.rotation)
                        translation = np.asarray(registration.translation)
                        xyz = xyz @ rotation.T + translation
                    points = laspy.ScaleAwarePointRecord.zeros(len(xyz), header=header)
                    points.x, points.y, points.z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
                    if chunk.rgb is not None:
                        points.red, points.green, points.blue = chunk.rgb[:, 0], chunk.rgb[:, 1], chunk.rgb[:, 2]
                    if chunk.intensity is not None:
                        points.intensity = chunk.intensity
                    if chunk.classification is not None:
                        points.classification = chunk.classification
                    points["source_id"] = np.full(len(xyz), source_id, dtype=np.uint8)
                    writer.write_points(points)
                    completed += len(xyz)
                    progress = completed / total if total else None
                    _queue_message(
                        queue, "progress", progress=progress, completed=completed, total=total,
                        message=f"Escribiendo {'escáner base' if source_id == 0 else 'dron ajustado'}…",
                    )

        partial_output.replace(output)
        partial_output = None
        output_count = inspect_cloud(output).point_count
        expected_count = total if scanner_info.point_count is not None and drone_info.point_count is not None else None
        audit = {
            "scanner_input_points": scanner_info.point_count,
            "drone_input_points": drone_info.point_count,
            "expected_points": expected_count,
            "written_points": completed,
            "output_header_points": output_count,
            "difference": (output_count - expected_count) if expected_count is not None and output_count is not None else None,
            "complete": (output_count == expected_count) if expected_count is not None and output_count is not None else None,
        }
        report_path = output.with_name(output.stem + "_registro.json")
        write_registration_report(report_path, result, request, output, audit)
        _queue_message(
            queue, "done", output_path=str(output), report_path=str(report_path),
            result=asdict(result), point_count=completed, audit=audit,
        )
    except InterruptedError as exc:
        try:
            if partial_output is not None:
                partial_output.unlink(missing_ok=True)
        except OSError:
            pass
        _queue_message(queue, "cancelled", message=str(exc))
    except Exception as exc:
        try:
            if partial_output is not None:
                partial_output.unlink(missing_ok=True)
        except OSError:
            pass
        _queue_message(queue, "error", message=str(exc), traceback=traceback.format_exc())
