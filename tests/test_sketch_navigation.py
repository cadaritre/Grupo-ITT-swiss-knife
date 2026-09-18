from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from photo_report_app.geospatial_converter import read_kml
from photo_report_app.location_sketch import (
    MapSnapshot, SketchData, _xy, fit_view_to_points, generate_sketch_dxf, polygon_from_kml,
    reproject_snapshot,
)
from photo_report_app.osm_vector import OSMFeature, VectorPath


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>Predio pequeño</name><Polygon><outerBoundaryIs><LinearRing>
<coordinates>-106,28 -105.999,28 -105.999,28.001 -106,28.001 -106,28</coordinates>
</LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>Predio grande</name><Polygon><outerBoundaryIs><LinearRing>
<coordinates>-106.01,28 -106,28 -106,28.01 -106.01,28.01 -106.01,28</coordinates>
</LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""


class SketchNavigationTests(unittest.TestCase):
    def test_inner_ring_before_outer_ring_does_not_replace_the_predio(self):
        kml = """<kml><Placemark><Polygon>
        <innerBoundaryIs><LinearRing><coordinates>-106.002,28.002 -106.001,28.002 -106.001,28.003 -106.002,28.002</coordinates></LinearRing></innerBoundaryIs>
        <outerBoundaryIs><LinearRing><coordinates>-106.01,28 -106,28 -106,28.01 -106.01,28.01 -106.01,28</coordinates></LinearRing></outerBoundaryIs>
        </Polygon></Placemark></kml>"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "hoyo.kml"
            source.write_text(kml, encoding="utf-8")
            points, count = polygon_from_kml(read_kml(source))
        self.assertEqual(count, 1)
        self.assertEqual(len(points), 4)
        self.assertEqual(points[0].longitude, -106.01)

    def test_kml_import_uses_largest_polygon_without_repeated_closing_vertex(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "predios.kml"
            source.write_text(KML, encoding="utf-8")
            points, count = polygon_from_kml(read_kml(source))
        self.assertEqual(count, 2)
        self.assertEqual(len(points), 4)
        self.assertEqual((points[0].latitude, points[0].longitude), (28.0, -106.01))
        self.assertEqual(points[-1].name, "V4")

    def test_imported_polygon_can_be_fitted_to_map_with_margin(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "predios.kml"
            source.write_text(KML, encoding="utf-8")
            points, _ = polygon_from_kml(read_kml(source))
        latitude, longitude, zoom = fit_view_to_points(points, 600, 400, 17)
        x, y = _xy(latitude, longitude, zoom)
        snapshot = MapSnapshot(Image.new("RGB", (600, 400)), zoom, x * 256 - 300, y * 256 - 200, True, "OSM")
        for point in points:
            px, py = snapshot.latlon_to_pixel(point.latitude, point.longitude)
            self.assertGreaterEqual(px, 0)
            self.assertLessEqual(px, 600)
            self.assertGreaterEqual(py, 0)
            self.assertLessEqual(py, 400)

    def test_imported_boundary_is_exported_as_closed_dxf_polyline(self):
        import ezdxf
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "predios.kml"
            source.write_text(KML, encoding="utf-8")
            points, _ = polygon_from_kml(read_kml(source))
            data = SketchData(points=points, features=[OSMFeature(
                "road/1", "road", paths=[VectorPath(
                    [(28.001, -106.009), (28.009, -106.001)], closed=False,
                )], tags={"highway": "residential"},
            )])
            output = generate_sketch_dxf(data, Path(directory) / "croquis.dxf")
            doc = ezdxf.readfile(output)
            boundary = list(doc.modelspace().query('LWPOLYLINE[layer=="LIMITE_AREA"]'))
        self.assertEqual(len(boundary), 1)
        self.assertTrue(boundary[0].closed)
        self.assertEqual(len(boundary[0]), 4)

    def test_interim_zoom_keeps_cursor_geography_and_center(self):
        old_zoom = 13
        latitude, longitude = 28.632996, -106.0691
        world_x, world_y = _xy(latitude, longitude, old_zoom)
        width, height = 600, 400
        snapshot = MapSnapshot(
            Image.new("RGB", (width, height), "white"), old_zoom,
            world_x * 256 - width / 2, world_y * 256 - height / 2,
            True, "Calles - OpenStreetMap",
        )
        result = reproject_snapshot(snapshot, (latitude, longitude), old_zoom + 1)
        self.assertEqual(result.zoom, 14)
        self.assertAlmostEqual(result.center()[0], latitude, places=6)
        self.assertAlmostEqual(result.center()[1], longitude, places=6)


if __name__ == "__main__":
    unittest.main()
