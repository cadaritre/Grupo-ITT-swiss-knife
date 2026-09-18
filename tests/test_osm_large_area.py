from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from shapely.geometry import Polygon
from shapely.ops import unary_union

from photo_report_app.location_sketch import SketchPoint, feature_draw_priority
from photo_report_app.osm_vector import (
    LARGE_QUERY_CHUNK_KM2, MAX_SELECTION_KM2, SINGLE_QUERY_KM2,
    OSMFeature, _geometry_area_km2, _query_chunks, fetch_osm_features,
)
from photo_report_app.sketch_tool import LocationSketchTool


def square(width: float = 0.1) -> list[SketchPoint]:
    return [
        SketchPoint("V1", 28.0, -106.0),
        SketchPoint("V2", 28.0, -106.0 + width),
        SketchPoint("V3", 28.0 + width, -106.0 + width),
        SketchPoint("V4", 28.0 + width, -106.0),
    ]


class LargeAreaVectorizationTests(unittest.TestCase):
    def test_chunks_cover_selection_and_keep_each_query_small(self):
        selection = Polygon([(point.longitude, point.latitude) for point in square()])
        chunks = _query_chunks(selection)
        self.assertGreater(len(chunks), 1)
        self.assertAlmostEqual(selection.difference(unary_union(chunks)).area, 0, places=9)
        for chunk in chunks:
            self.assertLessEqual(_geometry_area_km2(chunk), LARGE_QUERY_CHUNK_KM2 + 0.01)
            self.assertLessEqual(_geometry_area_km2(chunk.minimum_rotated_rectangle), SINGLE_QUERY_KM2 + 0.01)

    def test_large_area_deduplicates_building_returned_by_several_blocks(self):
        building = {
            "type": "way", "id": 123, "tags": {"building": "house"},
            "geometry": [
                {"lat": 28.045, "lon": -105.955}, {"lat": 28.045, "lon": -105.945},
                {"lat": 28.055, "lon": -105.945}, {"lat": 28.055, "lon": -105.955},
                {"lat": 28.045, "lon": -105.955},
            ],
        }
        messages = []
        with patch("photo_report_app.osm_vector._fetch_query_elements", side_effect=lambda _query, _notify: [building]) as fetch:
            features = fetch_osm_features(square(), progress=messages.append)
        self.assertGreater(fetch.call_count, 1)
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0].category, "building")
        self.assertEqual(features[0].osm_id, "way/123")
        self.assertTrue(any("Bloque" in message for message in messages))

    def test_failed_block_never_returns_partial_geometry(self):
        with patch("photo_report_app.osm_vector._fetch_query_elements", side_effect=[[], RuntimeError("servidor saturado")]):
            with self.assertRaisesRegex(RuntimeError, "no se usará un croquis incompleto"):
                fetch_osm_features(square())

    def test_overpass_error_remark_is_not_cached_as_valid_geometry(self):
        payload = json.dumps({"elements": [], "remark": "runtime error: timed out"}).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            with patch("photo_report_app.osm_vector.cache_dir", return_value=Path(directory)):
                with patch("photo_report_app.osm_vector.urllib.request.urlopen", side_effect=lambda *_args, **_kwargs: BytesIO(payload)):
                    with self.assertRaisesRegex(RuntimeError, "respuesta incompleta"):
                        fetch_osm_features(square(0.02))
            self.assertFalse(list(Path(directory).glob("*.json")))

    def test_areas_above_200_square_kilometers_still_rejected(self):
        self.assertEqual(MAX_SELECTION_KM2, 200.0)
        with patch("photo_report_app.osm_vector._fetch_query_elements") as fetch:
            with self.assertRaisesRegex(ValueError, "menor a 200 km²"):
                fetch_osm_features(square(0.2))
        fetch.assert_not_called()

    def test_buildings_draw_above_landuse_and_roads(self):
        building = OSMFeature("way/1", "building")
        landuse = OSMFeature("way/2", "landuse")
        road = OSMFeature("way/3", "road")
        self.assertEqual(sorted([building, road, landuse], key=feature_draw_priority), [landuse, road, building])

    def test_large_area_progress_tracks_blocks(self):
        self.assertEqual(LocationSketchTool._vector_progress_value("Bloque 1 de 4 listo"), 12)
        self.assertEqual(LocationSketchTool._vector_progress_value("Bloque 4 de 4 listo"), 30)


if __name__ == "__main__":
    unittest.main()
