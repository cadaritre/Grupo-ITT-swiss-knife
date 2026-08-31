from __future__ import annotations

import traceback

import numpy as np


SCANNER_COLOR = np.array((0.18, 0.82, 0.96), dtype=np.float64)
DRONE_COLOR = np.array((1.0, 0.48, 0.12), dtype=np.float64)


def _normalise_rgb(rgb: np.ndarray | None, count: int, fallback: np.ndarray) -> np.ndarray:
    if rgb is None or len(rgb) != count:
        return np.tile(fallback, (count, 1))
    colors = np.asarray(rgb, dtype=np.float64)
    maximum = float(np.nanmax(colors)) if colors.size else 0.0
    colors /= 65535.0 if maximum > 255.0 else 255.0
    return np.clip(colors, 0.0, 1.0) ** 0.72


def _elevation_colors(points: np.ndarray, low: float, high: float) -> np.ndarray:
    span = max(high - low, 1e-9)
    value = np.clip((points[:, 2] - low) / span, 0.0, 1.0)
    stops = np.array(
        (
            (0.04, 0.12, 0.46),
            (0.00, 0.62, 0.92),
            (0.08, 0.82, 0.48),
            (0.98, 0.82, 0.16),
            (0.91, 0.18, 0.10),
        ),
        dtype=np.float64,
    )
    scaled = value * (len(stops) - 1)
    index = np.minimum(scaled.astype(np.int32), len(stops) - 2)
    fraction = (scaled - index)[:, None]
    return stops[index] * (1.0 - fraction) + stops[index + 1] * fraction


class AdvancedPointCloudViewer:
    def __init__(self, payload: dict, status_queue=None):
        import open3d as o3d
        from open3d.visualization import gui, rendering

        self.o3d = o3d
        self.gui = gui
        self.rendering = rendering
        self.payload = payload
        self.status_queue = status_queue
        self.points = {
            "scanner": np.asarray(payload["scanner_xyz"], dtype=np.float64),
            "adjusted": np.asarray(payload["drone_adjusted_xyz"], dtype=np.float64),
            "raw": np.asarray(payload["drone_raw_xyz"], dtype=np.float64),
        }
        self.rgb = {
            "scanner": payload.get("scanner_rgb"),
            "adjusted": payload.get("drone_rgb"),
            "raw": payload.get("drone_rgb"),
        }
        self.clouds = {}
        self.material = rendering.MaterialRecord()
        self.material.shader = "defaultUnlit"
        self.material.point_size = 2.0
        self.view_index = 0
        self.color_index = 0

        app = gui.Application.instance
        app.initialize()
        self.window = app.create_window("Grupo ITT · Visor avanzado de nubes", 1460, 900)
        self.scene_widget = gui.SceneWidget()
        self.scene_widget.scene = rendering.Open3DScene(self.window.renderer)
        self.scene_widget.scene.set_background(np.array((0.025, 0.04, 0.065, 1.0)))
        self.scene_widget.scene.show_axes(True)
        self.scene_widget.set_view_controls(gui.SceneWidget.Controls.ROTATE_CAMERA)
        self.panel = gui.Vert(0.45, gui.Margins(14, 14, 14, 14))
        self._build_panel()
        self.window.add_child(self.scene_widget)
        self.window.add_child(self.panel)
        self.window.set_on_layout(self._on_layout)
        self._create_clouds()
        self._apply_colors()
        self._apply_view(0)
        if status_queue is not None:
            status_queue.put({"kind": "ready"})

    def _build_panel(self):
        gui = self.gui
        title = gui.Label("VISOR 3D AVANZADO")
        title.text_color = gui.Color(0.20, 0.72, 0.96)
        self.panel.add_child(title)
        self.panel.add_fixed(8)

        names = self.payload.get("names", {})
        scanner_name = names.get("scanner", "Escáner")
        drone_name = names.get("drone", "Dron")
        self.panel.add_child(gui.Label(f"Base: {scanner_name}"))
        self.panel.add_child(gui.Label(f"Móvil: {drone_name}"))
        self.panel.add_fixed(12)

        self.panel.add_child(gui.Label("Geometría visible"))
        self.view_combo = gui.Combobox()
        for label in ("Registro superpuesto", "Sólo escáner", "Sólo dron ajustado", "Dron original"):
            self.view_combo.add_item(label)
        self.view_combo.set_on_selection_changed(lambda _text, index: self._apply_view(index))
        self.panel.add_child(self.view_combo)
        self.panel.add_fixed(10)

        self.panel.add_child(gui.Label("Coloración"))
        self.color_combo = gui.Combobox()
        for label in ("Colores originales / por nube", "Identificar cada nube", "Elevación"):
            self.color_combo.add_item(label)
        self.color_combo.set_on_selection_changed(lambda _text, index: self._set_color_mode(index))
        self.panel.add_child(self.color_combo)
        self.panel.add_fixed(10)

        self.point_label = gui.Label("Tamaño de punto: 2.0 px")
        self.panel.add_child(self.point_label)
        point_slider = gui.Slider(gui.Slider.DOUBLE)
        point_slider.set_limits(1.0, 9.0)
        point_slider.double_value = 2.0
        point_slider.set_on_value_changed(self._set_point_size)
        self.panel.add_child(point_slider)
        self.panel.add_fixed(10)

        self.panel.add_child(gui.Label("Fondo"))
        background = gui.Combobox()
        for label in ("Oscuro", "Gris técnico", "Blanco"):
            background.add_item(label)
        background.set_on_selection_changed(lambda _text, index: self._set_background(index))
        self.panel.add_child(background)

        axes = gui.Checkbox("Mostrar ejes XYZ")
        axes.checked = True
        axes.set_on_checked(lambda checked: self.scene_widget.scene.show_axes(checked))
        self.panel.add_child(axes)
        self.panel.add_fixed(14)

        self.panel.add_child(gui.Label("Vistas rápidas"))
        row_a = gui.Horiz(0.3)
        for label, name in (("Isométrica", "iso"), ("Superior", "top")):
            button = gui.Button(label)
            button.set_on_clicked(lambda view=name: self._quick_view(view))
            row_a.add_child(button)
        self.panel.add_child(row_a)
        row_b = gui.Horiz(0.3)
        for label, name in (("Frontal", "front"), ("Derecha", "right")):
            button = gui.Button(label)
            button.set_on_clicked(lambda view=name: self._quick_view(view))
            row_b.add_child(button)
        self.panel.add_child(row_b)
        fit = gui.Button("Encuadrar geometría")
        fit.set_on_clicked(self._fit_active)
        self.panel.add_child(fit)
        self.panel.add_stretch()

        scanner_count = len(self.points["scanner"])
        drone_count = len(self.points["adjusted"])
        stats = gui.Label(f"Muestra GPU\nEscáner: {scanner_count:,}\nDron: {drone_count:,}")
        stats.text_color = gui.Color(0.68, 0.76, 0.82)
        self.panel.add_child(stats)
        help_text = gui.Label("Mouse izquierdo: girar\nRueda: acercar/alejar\nShift + mouse: desplazar")
        help_text.text_color = gui.Color(0.55, 0.64, 0.70)
        self.panel.add_child(help_text)

    def _on_layout(self, _context):
        rect = self.window.content_rect
        panel_width = min(330, max(260, int(rect.width * 0.24)))
        self.scene_widget.frame = self.gui.Rect(rect.x, rect.y, rect.width - panel_width, rect.height)
        self.panel.frame = self.gui.Rect(rect.x + rect.width - panel_width, rect.y, panel_width, rect.height)

    def _create_clouds(self):
        for name, points in self.points.items():
            cloud = self.o3d.geometry.PointCloud()
            cloud.points = self.o3d.utility.Vector3dVector(points)
            self.clouds[name] = cloud

    def _color_for(self, name: str) -> np.ndarray:
        points = self.points[name]
        if self.color_index == 1:
            fallback = SCANNER_COLOR if name == "scanner" else DRONE_COLOR
            return np.tile(fallback, (len(points), 1))
        if self.color_index == 2:
            registered = np.vstack((self.points["scanner"], self.points["adjusted"]))
            low, high = np.percentile(registered[:, 2], (1, 99))
            if name == "raw":
                low, high = np.percentile(points[:, 2], (1, 99))
            return _elevation_colors(points, float(low), float(high))
        fallback = SCANNER_COLOR if name == "scanner" else DRONE_COLOR
        return _normalise_rgb(self.rgb[name], len(points), fallback)

    def _apply_colors(self):
        for name, cloud in self.clouds.items():
            cloud.colors = self.o3d.utility.Vector3dVector(self._color_for(name))
            if self.scene_widget.scene.has_geometry(name):
                self.scene_widget.scene.remove_geometry(name)
            self.scene_widget.scene.add_geometry(name, cloud, self.material)
        self._apply_visibility()

    def _set_color_mode(self, index: int):
        self.color_index = int(index)
        self._apply_colors()
        self.scene_widget.force_redraw()

    def _set_point_size(self, value: float):
        self.material.point_size = float(value)
        self.point_label.text = f"Tamaño de punto: {value:.1f} px"
        for name in self.clouds:
            self.scene_widget.scene.modify_geometry_material(name, self.material)
        self.scene_widget.force_redraw()

    def _set_background(self, index: int):
        backgrounds = (
            (0.025, 0.04, 0.065, 1.0),
            (0.17, 0.20, 0.23, 1.0),
            (0.96, 0.97, 0.98, 1.0),
        )
        self.scene_widget.scene.set_background(np.asarray(backgrounds[int(index)], dtype=np.float32))
        self.scene_widget.force_redraw()

    def _apply_view(self, index: int):
        self.view_index = int(index)
        self._apply_visibility()
        self._fit_active()

    def _apply_visibility(self):
        visible = {
            0: {"scanner", "adjusted"},
            1: {"scanner"},
            2: {"adjusted"},
            3: {"raw"},
        }[self.view_index]
        for name in self.clouds:
            self.scene_widget.scene.show_geometry(name, name in visible)

    def _active_points(self) -> np.ndarray:
        if self.view_index == 0:
            return np.vstack((self.points["scanner"], self.points["adjusted"]))
        return self.points[{1: "scanner", 2: "adjusted", 3: "raw"}[self.view_index]]

    def _bounds(self):
        points = self._active_points()
        low, high = np.percentile(points, (0.5, 99.5), axis=0)
        center = (low + high) / 2.0
        extent = max(float(np.max(high - low)), 1.0)
        return center, extent

    def _fit_active(self):
        points = self._active_points()
        cloud = self.o3d.geometry.PointCloud()
        cloud.points = self.o3d.utility.Vector3dVector(points)
        bounds = cloud.get_axis_aligned_bounding_box()
        self.scene_widget.setup_camera(60.0, bounds, bounds.get_center())
        self.scene_widget.force_redraw()

    def _quick_view(self, view: str):
        center, extent = self._bounds()
        distance = extent * 1.8
        if view == "top":
            eye, up = center + (0, 0, distance), (0, 1, 0)
        elif view == "front":
            eye, up = center + (0, -distance, 0), (0, 0, 1)
        elif view == "right":
            eye, up = center + (distance, 0, 0), (0, 0, 1)
        else:
            eye, up = center + (distance, -distance, distance * 0.75), (0, 0, 1)
        self.scene_widget.look_at(center, eye, up)
        self.scene_widget.force_redraw()


def run_advanced_viewer(payload: dict, status_queue=None) -> None:
    """Multiprocessing entry point; Open3D never shares Tkinter's event loop."""
    try:
        viewer = AdvancedPointCloudViewer(payload, status_queue)
        viewer.gui.Application.instance.run()
        if status_queue is not None:
            status_queue.put({"kind": "closed"})
    except Exception as exc:
        if status_queue is not None:
            status_queue.put({"kind": "error", "message": str(exc), "traceback": traceback.format_exc()})

