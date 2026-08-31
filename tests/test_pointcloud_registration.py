from __future__ import annotations

import queue
import multiprocessing
import tempfile
import unittest
from pathlib import Path

import laspy
import numpy as np
import pye57

from photo_report_app.pointcloud_registration import inspect_cloud, iter_cloud_chunks, run_merge_worker, solve_rigid_registration


class PointCloudRegistrationTests(unittest.TestCase):
    def test_rigid_transform_does_not_change_scale(self):
        source = np.array(((0, 0, 0), (10, 0, 0), (0, 10, 0), (3, 4, 5)), dtype=float)
        angle = np.deg2rad(27)
        rotation = np.array(
            ((np.cos(angle), -np.sin(angle), 0), (np.sin(angle), np.cos(angle), 0), (0, 0, 1)),
            dtype=float,
        )
        target = source @ rotation.T + (390_000, 3_168_000, 1200)
        result = solve_rigid_registration(source, target)
        np.testing.assert_allclose(result.transform(source), target, atol=1e-7)
        self.assertLess(result.rmse, 1e-7)
        self.assertTrue(result.independent_check)
        self.assertAlmostEqual(np.linalg.det(np.asarray(result.rotation)), 1.0, places=8)

    def test_chunked_laz_merge_marks_cloud_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            scanner_path = folder / "scanner.laz"
            drone_path = folder / "drone.laz"
            output_path = folder / "merged.laz"
            source = np.array(((0, 0, 0), (10, 0, 0), (0, 10, 0), (5, 5, 2)), dtype=float)
            translation = np.array((390_000, 3_168_000, 1200), dtype=float)
            target = source + translation
            self._write_laz(scanner_path, source)
            self._write_laz(drone_path, target)
            pairs = [list(a) + list(b) for a, b in zip(source, target)]
            messages = queue.Queue()
            run_merge_worker(
                {
                    "scanner_path": str(scanner_path),
                    "drone_path": str(drone_path),
                    "scanner_unit": "Metros",
                    "drone_unit": "Metros",
                    "scanner_factor": 1.0,
                    "drone_factor": 1.0,
                    "pairs": pairs,
                    "output_path": str(output_path),
                    "chunk_size": 2,
                },
                messages,
            )
            final = None
            while not messages.empty():
                final = messages.get()
            self.assertEqual(final["kind"], "done", final)
            merged = laspy.read(output_path)
            self.assertEqual(len(merged.points), 8)
            self.assertEqual(np.count_nonzero(merged["source_id"] == 0), 4)
            self.assertEqual(np.count_nonzero(merged["source_id"] == 1), 4)
            self.assertIsNone(merged.header.parse_crs())
            np.testing.assert_allclose(
                np.column_stack((merged.x[4:], merged.y[4:], merged.z[4:])), source, atol=0.0011
            )
            self.assertTrue(output_path.with_name("merged_registro.json").exists())

    def test_e57_scan_is_exposed_as_bounded_chunks(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scanner.e57"
            writer = pye57.E57(str(path), mode="w")
            writer.write_scan_raw(
                {
                    "cartesianX": np.arange(5, dtype=np.float64),
                    "cartesianY": np.arange(5, dtype=np.float64) + 10,
                    "cartesianZ": np.arange(5, dtype=np.float64) + 20,
                }
            )
            writer.close()
            info = inspect_cloud(path)
            self.assertEqual(info.point_count, 5)
            chunks = list(iter_cloud_chunks(path, chunk_size=2))
            self.assertEqual([len(chunk.xyz) for chunk in chunks], [2, 2, 1])

    def test_worker_can_run_in_spawned_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            scanner_path = folder / "scanner.laz"
            drone_path = folder / "drone.laz"
            output_path = folder / "spawned.laz"
            source = np.array(((0, 0, 0), (2, 0, 0), (0, 2, 0)), dtype=float)
            target = source + (100, 200, 10)
            self._write_laz(scanner_path, source)
            self._write_laz(drone_path, target)
            request = {
                "scanner_path": str(scanner_path), "drone_path": str(drone_path),
                "scanner_unit": "Metros", "drone_unit": "Metros",
                "scanner_factor": 1.0, "drone_factor": 1.0,
                "pairs": [list(a) + list(b) for a, b in zip(source, target)],
                "output_path": str(output_path), "chunk_size": 2,
            }
            context = multiprocessing.get_context("spawn")
            messages, cancelled = context.Queue(), context.Event()
            process = context.Process(target=run_merge_worker, args=(request, messages, cancelled))
            process.start()
            process.join(timeout=20)
            self.assertEqual(process.exitcode, 0)
            received = []
            while not messages.empty():
                received.append(messages.get())
            self.assertTrue(any(message["kind"] == "done" for message in received), received)
            self.assertTrue(output_path.exists())

    def test_explicit_millimetres_are_normalized_without_estimating_scale(self):
        source_mm = np.array(((0, 0, 0), (1000, 0, 0), (0, 1000, 0)), dtype=float)
        target_m = np.array(((50, 60, 7), (51, 60, 7), (50, 61, 7)), dtype=float)
        result = solve_rigid_registration(source_mm, target_m, source_unit_factor=0.001)
        np.testing.assert_allclose(result.transform(source_mm), target_m, atol=1e-9)
        self.assertAlmostEqual(np.linalg.det(np.asarray(result.rotation)), 1.0, places=8)

    @staticmethod
    def _write_laz(path: Path, xyz: np.ndarray):
        header = laspy.LasHeader(point_format=3, version="1.2")
        header.scales = np.array((0.001, 0.001, 0.001))
        header.offsets = np.floor(xyz.mean(axis=0) / 1000) * 1000
        cloud = laspy.LasData(header)
        cloud.x, cloud.y, cloud.z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
        cloud.write(path)


if __name__ == "__main__":
    unittest.main()
