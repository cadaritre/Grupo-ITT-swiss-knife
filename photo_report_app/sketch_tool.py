from __future__ import annotations

import csv
import math
import os
import queue
import re
import threading
from datetime import datetime
from pathlib import Path
from tkinter import END, BooleanVar, Canvas, StringVar, Text, filedialog, messagebox
from tkinter import ttk

from PIL import Image, ImageTk

from .app_storage import SETTINGS, backup_editable, category_dir, preserve_artifact
from .location_sketch import (
    DEFAULT_MAP_LAYER,
    MAP_LAYERS,
    TILE_SIZE,
    SketchData,
    SketchPoint,
    _inverse_xy,
    _xy,
    default_layer_visibility,
    generate_sketch_dxf,
    generate_sketch_pdf,
    render_location_map,
    visible_features,
)
from .osm_vector import CATEGORY_LABELS, feature_counts, fetch_osm_features, selection_area_km2
from .terrain_contours import fetch_elevation_contours
from .ux_components import attach_tooltip, help_badge


class LocationSketchTool(ttk.Frame):
    def __init__(self, master, logo_path: Path, on_home):
        super().__init__(master, style="App.TFrame")
        self.logo_path = logo_path
        self.on_home = on_home
        self.data = SketchData()
        saved_layer = str(SETTINGS.get("sketches.map_layer", DEFAULT_MAP_LAYER))
        self.data.map_layer = saved_layer if saved_layer in MAP_LAYERS else DEFAULT_MAP_LAYER
        self.data.contour_interval = str(SETTINGS.get("sketches.contour_interval", "Automática"))
        self.json_path: Path | None = None
        self._editing_point: int | None = None
        self._loading = False
        self._map_snapshot = None
        self._map_photo = None
        self._map_token = 0
        self._map_results = queue.Queue()
        self._vector_results = queue.Queue()
        self._resize_job = None
        self._display_box = (0, 0, 0, 0)
        self._fixed_view: tuple[float, float, int] | None = None
        self._drag_start = None
        self._drag_last = None
        self._rectangle_preview = None
        self._zoom_job = None
        self._vectorizing = False
        self.status_var = StringVar(value="Croquis nuevo")
        self._make_vars()
        self._build()
        self._load_to_form()
        self.after(250, self.refresh_map)
        self.after(100, self._poll_map_results)

    def _make_vars(self):
        self.vars = {
            "title": StringVar(value=self.data.title),
            "client": StringVar(),
            "project": StringVar(),
            "location": StringVar(),
            "sketch_date": StringVar(value=self.data.sketch_date),
            "geometry": StringVar(value=self.data.geometry),
            "map_layer": StringVar(value=self.data.map_layer),
            "contour_interval": StringVar(value=self.data.contour_interval),
        }
        self.draw_mode_var = StringVar(value="pan")
        self.point_vars = {
            "name": StringVar(),
            "latitude": StringVar(),
            "longitude": StringVar(),
            "description": StringVar(),
        }
        self.layer_vars = {key: BooleanVar(value=visible) for key, visible in default_layer_visibility().items()}
        self.layer_count_vars = {key: StringVar(value="0") for key in self.layer_vars}
        for variable in self.vars.values():
            variable.trace_add("write", lambda *_: self._changed())
        for variable in self.point_vars.values():
            variable.trace_add("write", lambda *_: self._point_changed())

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self, style="Header.TFrame", padding=(18, 10))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="‹ Herramientas", style="HeaderButton.TButton", command=self.on_home).pack(side="left")
        ttk.Label(toolbar, text="Croquis de ubicación", style="HeaderTitle.TLabel").pack(side="left", padx=16)
        ttk.Button(toolbar, text="Nuevo", style="HeaderButton.TButton", command=self.new_sketch).pack(side="left", padx=(8, 2))
        ttk.Button(toolbar, text="Abrir JSON", style="HeaderButton.TButton", command=self.open_json).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Guardar JSON", style="HeaderButton.TButton", command=self.save_json).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Exportar DXF", style="HeaderAccent.TButton", command=self.export_dxf).pack(side="right")
        ttk.Button(toolbar, text="Exportar PDF", style="HeaderAccent.TButton", command=self.export_pdf).pack(side="right", padx=(0, 6))
        ttk.Label(toolbar, textvariable=self.status_var, style="HeaderSub.TLabel").pack(side="right", padx=14)

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        editor = ttk.Frame(panes, style="Card.TFrame", padding=10, width=510)
        preview = ttk.Frame(panes, style="Card.TFrame", padding=12)
        panes.add(editor, weight=2)
        panes.add(preview, weight=3)
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(editor)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self._build_data_tab()
        self._build_points_tab()
        self._build_layers_tab()
        self._build_preview(preview)

    def _tab(self, title):
        frame = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        self.notebook.add(frame, text=title)
        return frame

    def _entry(self, parent, row, label, variable, column=0, colspan=1):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=row, column=column, columnspan=colspan, sticky="ew", padx=5, pady=5)
        ttk.Label(box, text=label, style="Field.Card.TLabel").pack(anchor="w")
        entry = ttk.Entry(box, textvariable=variable)
        entry.pack(fill="x", pady=(3, 0))
        return entry

    def _build_data_tab(self):
        tab = self._tab("Datos del croquis")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(6, weight=1)
        ttk.Label(tab, text="INFORMACIÓN GENERAL", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 7))
        self._entry(tab, 1, "Título del croquis", self.vars["title"], 0, 2)
        self._entry(tab, 2, "Proyecto", self.vars["project"], 0, 2)
        self._entry(tab, 3, "Cliente", self.vars["client"])
        self._entry(tab, 3, "Fecha", self.vars["sketch_date"], 1)
        self._entry(tab, 4, "Ubicación / referencia", self.vars["location"], 0, 2)
        ttk.Label(tab, text="NOTAS", style="Section.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", padx=5, pady=(13, 3))
        self.notes_text = Text(tab, height=8, wrap="word", font=("Segoe UI", 9), relief="solid", bd=1, padx=8, pady=7)
        self.notes_text.grid(row=6, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        self.notes_text.bind("<KeyRelease>", lambda _: self._changed())

    def _build_points_tab(self):
        tab = self._tab("Área de selección")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(3, weight=1)
        ttk.Label(tab, text="VÉRTICES DEL ÁREA", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=(0, 7))
        edit = ttk.Frame(tab, style="Soft.TFrame", padding=11)
        edit.grid(row=1, column=0, sticky="ew")
        for column in range(2):
            edit.columnconfigure(column, weight=1)
        self._soft_entry(edit, 0, 0, "Latitud", self.point_vars["latitude"])
        self._soft_entry(edit, 0, 1, "Longitud", self.point_vars["longitude"])
        actions = ttk.Frame(edit, style="Soft.TFrame")
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="Agregar vértice", style="Accent.TButton", command=self.add_point).pack(side="left")
        ttk.Button(actions, text="Actualizar seleccionado", style="Secondary.TButton", command=self.update_point).pack(side="left", padx=5)
        ttk.Button(actions, text="Limpiar", style="Secondary.TButton", command=self.clear_point_editor).pack(side="left")
        ttk.Label(tab, text="Usa Polígono o Rectángulo sobre el mapa; también puedes capturar coordenadas manualmente.", style="Hint.Card.TLabel").grid(row=2, column=0, sticky="w", padx=4, pady=(8, 5))
        listing = ttk.Frame(tab, style="Card.TFrame")
        listing.grid(row=3, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.points_tree = ttk.Treeview(listing, columns=("number", "latitude", "longitude"), show="headings", selectmode="browse")
        for key, label, width in (("number", "#", 40), ("latitude", "Latitud", 150), ("longitude", "Longitud", 150)):
            self.points_tree.heading(key, text=label)
            self.points_tree.column(key, width=width, stretch=key != "number", anchor="center")
        self.points_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.points_tree.yview)
        self.points_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.points_tree.bind("<<TreeviewSelect>>", self._select_point)
        bottom = ttk.Frame(tab, style="Card.TFrame")
        bottom.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(bottom, text="Importar CSV", style="Secondary.TButton", command=self.import_csv).pack(side="left")
        ttk.Button(bottom, text="Eliminar vértice", style="Secondary.TButton", command=self.delete_point).pack(side="right")
        ttk.Button(bottom, text="↓", width=3, style="Secondary.TButton", command=lambda: self.move_point(1)).pack(side="right", padx=(4, 0))
        ttk.Button(bottom, text="↑", width=3, style="Secondary.TButton", command=lambda: self.move_point(-1)).pack(side="right")

    def _build_layers_tab(self):
        tab = self._tab("Capas")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text="CAPAS VISIBLES Y EXPORTABLES", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 4))
        ttk.Label(
            tab,
            text="Apaga lo que no quieras ver ni exportar. La geometría permanece guardada en el proyecto.",
            style="Hint.Card.TLabel", wraplength=440, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 9))
        layer_labels = list(CATEGORY_LABELS.items()) + [
            ("selection", "Límite de selección"),
            ("labels", "Etiquetas PDF / DXF"),
        ]
        for index, (key, label) in enumerate(layer_labels):
            row, column = 2 + index // 2, index % 2
            card = ttk.Frame(tab, style="Soft.TFrame", padding=(9, 7))
            card.grid(row=row, column=column, sticky="ew", padx=4, pady=3)
            card.columnconfigure(0, weight=1)
            ttk.Checkbutton(
                card, text=label, variable=self.layer_vars[key],
                command=lambda selected=key: self._layer_changed(selected),
            ).grid(row=0, column=0, sticky="w")
            ttk.Label(card, textvariable=self.layer_count_vars[key], style="SoftHint.TLabel").grid(row=0, column=1, sticky="e", padx=(7, 0))
        actions = ttk.Frame(tab, style="Card.TFrame")
        actions_row = 2 + (len(layer_labels) + 1) // 2
        actions.grid(row=actions_row, column=0, columnspan=2, sticky="ew", padx=4, pady=(11, 0))
        ttk.Button(actions, text="Mostrar todo", style="Secondary.TButton", command=lambda: self._set_layers("all")).pack(side="left")
        ttk.Button(actions, text="Ocultar cartografía", style="Secondary.TButton", command=lambda: self._set_layers("none")).pack(side="left", padx=5)
        ttk.Button(actions, text="Solo principales", style="Accent.TButton", command=lambda: self._set_layers("main")).pack(side="right")

    def _layer_changed(self, key: str):
        if self._loading:
            return
        self.data.layer_visibility[key] = bool(self.layer_vars[key].get())
        self.status_var.set("Visibilidad de capas actualizada")
        if key == "selection":
            self._draw_selection_overlay()
        elif key != "labels":
            self.refresh_map()
        self._update_map_info()

    def _set_layers(self, mode: str):
        principal = {"road", "building", "water", "contour", "selection", "labels"}
        self._loading = True
        try:
            for key, variable in self.layer_vars.items():
                visible = True if mode == "all" else key in {"selection", "labels"} if mode == "none" else key in principal
                variable.set(visible)
                self.data.layer_visibility[key] = visible
        finally:
            self._loading = False
        self.status_var.set("Capas visibles actualizadas")
        self.refresh_map()

    def _refresh_layer_counts(self):
        counts = feature_counts(self.data.features)
        for key, variable in self.layer_count_vars.items():
            if key == "selection":
                count = 1 if len(self.data.points) >= 3 else 0
            elif key == "labels":
                count = sum(1 for feature in self.data.features if feature.name)
            else:
                count = counts.get(key, 0)
            variable.set(str(count))

    def _soft_entry(self, parent, row, column, label, variable):
        box = ttk.Frame(parent, style="Soft.TFrame")
        box.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 5, 5 if column == 0 else 0), pady=3)
        ttk.Label(box, text=label, style="SoftHint.TLabel").pack(anchor="w")
        ttk.Entry(box, textvariable=variable).pack(fill="x", pady=(2, 0))

    def _build_preview(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)
        heading = ttk.Frame(parent, style="Card.TFrame")
        heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(heading, text="VISTA PREVIA DEL CROQUIS", style="Section.TLabel").pack(side="left")
        ttk.Button(heading, text="Actualizar mapa", style="Secondary.TButton", command=self.refresh_map).pack(side="right")
        layer_controls = ttk.Frame(parent, style="Soft.TFrame", padding=(8, 5))
        layer_controls.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        ttk.Label(layer_controls, text="Mapa base", style="SoftHint.TLabel").pack(side="left", padx=(0, 5))
        self.layer_combo = ttk.Combobox(layer_controls, textvariable=self.vars["map_layer"], values=list(MAP_LAYERS), state="readonly", width=23)
        self.layer_combo.pack(side="left")
        ttk.Label(layer_controls, text="Curvas", style="SoftHint.TLabel").pack(side="left", padx=(12, 4))
        self.contour_combo = ttk.Combobox(
            layer_controls, textvariable=self.vars["contour_interval"],
            values=("Automática", "1 m", "2 m", "5 m", "10 m", "20 m", "50 m"),
            state="readonly", width=10,
        )
        self.contour_combo.pack(side="left")
        help_badge(
            layer_controls,
            "Equidistancia vertical entre curvas. Automática elige un intervalo según el área y el relieve; usa valores pequeños solo en zonas reducidas para evitar DXF demasiado pesados.",
        ).pack(side="left", padx=(3, 0))
        attach_tooltip(self.contour_combo, "Equidistancia vertical entre curvas de nivel. Menor intervalo produce más curvas y aumenta el tiempo y tamaño del DXF.")
        ttk.Button(layer_controls, text="−", width=3, style="Secondary.TButton", command=lambda: self.zoom_map(-1)).pack(side="right", padx=(3, 0))
        ttk.Button(layer_controls, text="+", width=3, style="Secondary.TButton", command=lambda: self.zoom_map(1)).pack(side="right", padx=(8, 0))
        ttk.Button(layer_controls, text="Ajustar área", style="Secondary.TButton", command=self.fit_area).pack(side="right", padx=(8, 3))
        actions = ttk.Frame(parent, style="Soft.TFrame", padding=(8, 5))
        actions.grid(row=2, column=0, sticky="ew", pady=(3, 0))
        ttk.Label(actions, text="Herramienta:", style="SoftHint.TLabel").pack(side="left", padx=(0, 4))
        ttk.Radiobutton(actions, text="Mover", variable=self.draw_mode_var, value="pan", command=self._drawing_mode_changed).pack(side="left")
        ttk.Radiobutton(actions, text="Polígono", variable=self.draw_mode_var, value="polygon", command=self._drawing_mode_changed).pack(side="left", padx=3)
        ttk.Radiobutton(actions, text="Rectángulo", variable=self.draw_mode_var, value="rectangle", command=self._drawing_mode_changed).pack(side="left")
        undo_button = ttk.Button(actions, text="↶ Deshacer", style="Secondary.TButton", command=self.undo_last_point)
        undo_button.pack(side="left", padx=3)
        attach_tooltip(undo_button, "Elimina el último vértice agregado al área de selección.")
        reset_button = ttk.Button(actions, text="Restablecer área", style="Secondary.TButton", command=self.clear_area)
        reset_button.pack(side="left", padx=3)
        attach_tooltip(reset_button, "Elimina el polígono y su vectorización para comenzar de nuevo.")
        self.vectorize_button = ttk.Button(actions, text="Vectorizar mapa", style="Accent.TButton", command=self.vectorize_osm)
        self.vectorize_button.pack(side="right")
        self.vector_progress = ttk.Progressbar(actions, mode="determinate", maximum=100, length=105)
        self.vector_progress.pack(side="right", padx=(4, 7))
        self.vector_progress.pack_forget()
        attach_tooltip(self.vectorize_button, "Consulta OpenStreetMap, recorta la geometría al polígono y, en modo topográfico, genera curvas de nivel desde el modelo de elevación.")
        self.map_info = ttk.Label(parent, text="Sin puntos", style="Hint.Card.TLabel")
        self.map_info.grid(row=3, column=0, sticky="w", pady=(7, 6))
        self.map_preview = Canvas(parent, background="#DCE5EA", highlightthickness=1, highlightbackground="#B8C7D1", cursor="fleur")
        self.map_preview.grid(row=4, column=0, sticky="nsew")
        self.map_preview.bind("<Configure>", self._preview_resized)
        self.map_preview.bind("<ButtonPress-1>", self._map_press)
        self.map_preview.bind("<B1-Motion>", self._map_motion)
        self.map_preview.bind("<ButtonRelease-1>", self._map_release)
        self.map_preview.bind("<MouseWheel>", self._map_wheel)
        self.map_preview.bind("<Escape>", lambda _event: self._cancel_drawing())
        self.map_preview.bind("<Return>", lambda _event: self.finish_drawing())
        ttk.Label(parent, text="Rueda: zoom · Mover: arrastra el mapa · Polígono: clic por vértice · Rectángulo: arrastra (Shift = cuadrado) · Topográfico: genera curvas de nivel.", style="Hint.Card.TLabel", wraplength=760, justify="left").grid(row=5, column=0, sticky="w", pady=(8, 0))

    def _changed(self):
        if self._loading:
            return
        self.status_var.set("Cambios sin guardar")
        map_changed = self.vars["map_layer"].get() != self.data.map_layer
        contours_changed = self.vars["contour_interval"].get() != self.data.contour_interval
        if map_changed or contours_changed:
            SETTINGS.update({
                "sketches.map_layer": self.vars["map_layer"].get(),
                "sketches.contour_interval": self.vars["contour_interval"].get(),
            })
            self._invalidate_vectors()
        if self.vars["geometry"].get() != self.data.geometry or map_changed:
            self.after_idle(self.refresh_map)

    def _point_changed(self):
        if not self._loading:
            self.status_var.set("Cambios sin guardar")

    def _float(self, value: str) -> float:
        return float(value.strip().replace(",", "."))

    def _sync_data(self):
        for field in ("title", "client", "project", "location", "sketch_date", "geometry", "map_layer", "contour_interval"):
            setattr(self.data, field, self.vars[field].get().strip())
        self.data.notes = self.notes_text.get("1.0", "end-1c").strip()

    def _load_to_form(self):
        self._loading = True
        try:
            for field in ("title", "client", "project", "location", "sketch_date", "geometry", "map_layer", "contour_interval"):
                self.vars[field].set(getattr(self.data, field))
            self.notes_text.delete("1.0", END)
            self.notes_text.insert("1.0", self.data.notes)
            for key, variable in self.layer_vars.items():
                variable.set(bool(self.data.layer_visibility.get(key, True)))
            self._refresh_tree()
            self._refresh_layer_counts()
            self.clear_point_editor()
        finally:
            self._loading = False

    def _refresh_tree(self, select: int | None = None):
        self.points_tree.delete(*self.points_tree.get_children())
        for index, point in enumerate(self.data.points):
            self.points_tree.insert("", END, iid=str(index), values=(index + 1, f"{point.latitude:.7f}", f"{point.longitude:.7f}"))
        if select is not None and self.data.points:
            select = max(0, min(select, len(self.data.points) - 1))
            self.points_tree.selection_set(str(select))
            self.points_tree.focus(str(select))
        self._refresh_layer_counts()
        self._update_map_info()

    def _update_map_info(self):
        count = len(self.data.points)
        if not count:
            self.map_info.configure(text="Navega al sitio y elige Polígono o Rectángulo para delimitar el recorte.")
            return
        zone = int((sum(point.longitude for point in self.data.points) / count + 180) / 6) + 1
        hemisphere = "N" if sum(point.latitude for point in self.data.points) / count >= 0 else "S"
        layer = self.vars["map_layer"].get() or DEFAULT_MAP_LAYER
        offline = " · sin conexión" if self._map_snapshot and not self._map_snapshot.online and layer != "Base neutra" else ""
        vectors = len(self.data.features)
        visible = len(visible_features(self.data))
        vector_text = f" · {visible}/{vectors} elementos visibles" if vectors else " · pendiente de vectorizar"
        area = selection_area_km2(self.data.points)
        area_text = f" · {area * 100:.2f} ha" if area < 1 else f" · {area:.2f} km²"
        self.map_info.configure(text=f"{count} vértices{area_text} · UTM {zone}{hemisphere}{vector_text} · {layer}{offline}")

    def clear_point_editor(self):
        self._loading = True
        try:
            self._editing_point = None
            self.point_vars["name"].set(f"V{len(self.data.points) + 1}")
            self.point_vars["latitude"].set("")
            self.point_vars["longitude"].set("")
            self.point_vars["description"].set("")
            if hasattr(self, "points_tree"):
                self.points_tree.selection_remove(self.points_tree.selection())
        finally:
            self._loading = False

    def _point_from_form(self) -> SketchPoint | None:
        try:
            latitude = self._float(self.point_vars["latitude"].get())
            longitude = self._float(self.point_vars["longitude"].get())
        except ValueError:
            messagebox.showwarning("Coordenadas incompletas", "Escribe una latitud y longitud válidas en grados decimales.")
            return None
        if not (-85.0511 <= latitude <= 85.0511 and -180 <= longitude <= 180):
            messagebox.showwarning("Coordenadas fuera de rango", "La latitud debe estar entre -85 y 85 y la longitud entre -180 y 180.")
            return None
        return SketchPoint(
            self.point_vars["name"].get().strip() or f"V{len(self.data.points) + 1}",
            latitude,
            longitude,
            self.point_vars["description"].get().strip(),
        )

    def add_point(self):
        point = self._point_from_form()
        if point is None:
            return
        self.data.points.append(point)
        self._invalidate_vectors()
        self._refresh_tree(select=len(self.data.points) - 1)
        self.clear_point_editor()
        self.status_var.set("Punto agregado")
        self._show_map_image()

    def update_point(self):
        if self._editing_point is None:
            messagebox.showinfo("Selecciona un punto", "Selecciona un punto de la tabla para actualizarlo.")
            return
        point = self._point_from_form()
        if point is None:
            return
        index = self._editing_point
        self.data.points[index] = point
        self._invalidate_vectors()
        self._refresh_tree(select=index)
        self.status_var.set("Punto actualizado")
        self._show_map_image()

    def _select_point(self, _event=None):
        selected = self.points_tree.selection()
        if not selected:
            return
        self._editing_point = int(selected[0])
        point = self.data.points[self._editing_point]
        self._loading = True
        try:
            self.point_vars["name"].set(point.name)
            self.point_vars["latitude"].set(f"{point.latitude:.7f}")
            self.point_vars["longitude"].set(f"{point.longitude:.7f}")
            self.point_vars["description"].set(point.description)
        finally:
            self._loading = False

    def delete_point(self):
        selected = self.points_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        self.data.points.pop(index)
        self._invalidate_vectors()
        self.clear_point_editor()
        self._refresh_tree(select=min(index, len(self.data.points) - 1) if self.data.points else None)
        self.status_var.set("Punto eliminado")
        self._show_map_image()

    def move_point(self, direction: int):
        selected = self.points_tree.selection()
        if not selected:
            return
        old = int(selected[0])
        new = max(0, min(len(self.data.points) - 1, old + direction))
        if old != new:
            self.data.points.insert(new, self.data.points.pop(old))
            self._invalidate_vectors()
            self._editing_point = None
            self._refresh_tree(select=new)
            self._show_map_image()

    def undo_last_point(self):
        if not self.data.points:
            return
        self.data.points.pop()
        self._invalidate_vectors()
        self.clear_point_editor()
        self._refresh_tree(select=len(self.data.points) - 1 if self.data.points else None)
        self.status_var.set("Último punto eliminado")
        self._show_map_image()

    def _invalidate_vectors(self):
        if self.data.features:
            self.data.features = []
            self.data.vectorized_at = ""
            self.status_var.set("El área cambió; vuelve a vectorizar el mapa")
            self._refresh_layer_counts()

    def clear_area(self):
        if not self.data.points:
            return
        self.data.points = []
        self.data.features = []
        self.data.vectorized_at = ""
        self._editing_point = None
        self._fixed_view = None
        self.draw_mode_var.set("pan")
        self.clear_point_editor()
        self._refresh_tree()
        self.status_var.set("Área de selección eliminada")
        self._show_map_image()

    def vectorize_osm(self):
        self._sync_data()
        if len(self.data.points) < 3:
            messagebox.showwarning("Área incompleta", "Elige Polígono o Rectángulo y delimita el área sobre el mapa.")
            return
        if self._vectorizing:
            return
        self.draw_mode_var.set("pan")
        self._fixed_view = None
        self.map_preview.configure(cursor="fleur")
        selection = [SketchPoint(point.name, point.latitude, point.longitude, point.description) for point in self.data.points]
        include_contours = self.data.map_layer.startswith("Topográfico")
        contour_interval = self.data.contour_interval
        self._vectorizing = True
        self.vectorize_button.state(["disabled"])
        self.vector_progress.pack(side="right", padx=(4, 7), before=self.vectorize_button)
        self.vector_progress.configure(mode="determinate")
        self.vector_progress["value"] = 3
        self.status_var.set("Consultando y recortando geometría de OpenStreetMap...")
        self.map_info.configure(text="Vectorizando calles, edificios, agua y demás geometría dentro del área...")

        def progress(message):
            self._vector_results.put(("progress", message, None))

        def worker():
            try:
                features = fetch_osm_features(selection, progress=progress)
                if include_contours:
                    features.extend(fetch_elevation_contours(selection, contour_interval, progress=progress))
                self._vector_results.put(("done", features, None))
            except Exception as exc:
                self._vector_results.put(("done", None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_vectorization(self, features, error):
        self._vectorizing = False
        self.vectorize_button.state(["!disabled"])
        self.vector_progress["value"] = 0
        self.vector_progress.pack_forget()
        if error:
            self.status_var.set("No se pudo vectorizar")
            self._update_map_info()
            messagebox.showerror("No se pudo vectorizar el mapa", str(error))
            return
        self.data.features = features or []
        self.data.vectorized_at = datetime.now().isoformat(timespec="seconds")
        self._refresh_layer_counts()
        counts = feature_counts(self.data.features)
        summary = ", ".join(f"{CATEGORY_LABELS.get(category, category)}: {count}" for category, count in sorted(counts.items()))
        self.status_var.set(f"{len(self.data.features)} elementos OSM vectorizados")
        self.refresh_map()
        if not self.data.features:
            messagebox.showinfo("Sin geometría", "OpenStreetMap no devolvió geometría dentro del área seleccionada.")
        else:
            messagebox.showinfo("Vectorización terminada", f"Se obtuvieron {len(self.data.features)} elementos recortados al polígono.\n\n{summary}")

    @staticmethod
    def _vector_progress_value(message: str) -> int:
        lowered = message.lower()
        if "caché" in lowered:
            return 18
        if "servidor" in lowered:
            match = re.search(r"servidor\s+(\d+)\s+de\s+(\d+)", lowered)
            return 6 + (round(int(match.group(1)) / max(int(match.group(2)), 1) * 12) if match else 5)
        if "recortando" in lowered:
            return 30
        if "detalle osm" in lowered:
            match = re.search(r"([\d,]+)\s+de\s+([\d,]+)", lowered)
            if match:
                current = int(match.group(1).replace(",", ""))
                total = int(match.group(2).replace(",", ""))
                return 32 + round(current / max(total, 1) * 38)
            return 50
        if "capas cad" in lowered:
            return 72
        if "descargando modelo" in lowered:
            return 77
        if "generando curvas" in lowered:
            return 84
        if "trazando curvas" in lowered:
            match = re.search(r"(\d+)\s+de\s+(\d+)", lowered)
            if match:
                return 85 + round(int(match.group(1)) / max(int(match.group(2)), 1) * 13)
            return 90
        return 12

    def _drawing_mode_changed(self):
        mode = self.draw_mode_var.get()
        self.map_preview.focus_set()
        self.map_preview.configure(cursor="fleur" if mode == "pan" else "crosshair")
        if mode == "polygon":
            self.status_var.set("Polígono: cada clic agrega un vértice · Enter para terminar")
        elif mode == "rectangle":
            self.status_var.set("Rectángulo: arrastra sobre el mapa · mantén Shift para un cuadrado")
        else:
            self.status_var.set("Arrastra para mover el mapa · usa la rueda para acercar o alejar")

    def _cancel_drawing(self):
        self.draw_mode_var.set("pan")
        self._drag_start = None
        self.map_preview.delete("rectangle-preview")
        self._drawing_mode_changed()

    def finish_drawing(self):
        self.draw_mode_var.set("pan")
        self.map_preview.configure(cursor="fleur")
        self.status_var.set("Área lista; pulsa Vectorizar mapa")
        self._show_map_image()

    def import_csv(self):
        chosen = filedialog.askopenfilename(title="Importar puntos", filetypes=(("Archivos CSV", "*.csv"), ("Todos los archivos", "*.*")))
        if not chosen:
            return
        try:
            with open(chosen, "r", encoding="utf-8-sig", newline="") as stream:
                sample = stream.read(4096)
                stream.seek(0)
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                reader = csv.DictReader(stream, dialect=dialect)
                added = 0
                for row in reader:
                    normalized = {str(key).strip().lower(): (value or "").strip() for key, value in row.items()}
                    lat = normalized.get("latitud") or normalized.get("latitude") or normalized.get("lat")
                    lon = normalized.get("longitud") or normalized.get("longitude") or normalized.get("lon") or normalized.get("lng")
                    if not lat or not lon:
                        continue
                    latitude, longitude = self._float(lat), self._float(lon)
                    name = normalized.get("punto") or normalized.get("nombre") or normalized.get("name") or f"V{len(self.data.points) + 1}"
                    description = normalized.get("descripcion") or normalized.get("descripción") or normalized.get("description") or ""
                    self.data.points.append(SketchPoint(name, latitude, longitude, description))
                    added += 1
            if not added:
                raise ValueError("No se encontraron columnas de latitud y longitud.")
            self._invalidate_vectors()
            self._refresh_tree(select=len(self.data.points) - 1)
            self.status_var.set(f"{added} puntos importados")
            self.refresh_map()
        except Exception as exc:
            messagebox.showerror("No se pudo importar", f"Usa encabezados como Punto, Latitud, Longitud y Descripción.\n\n{exc}")

    def refresh_map(self):
        self._sync_data()
        self._map_token += 1
        token = self._map_token
        points = [SketchPoint(point.name, point.latitude, point.longitude, point.description) for point in self.data.points]
        geometry = self.data.geometry
        layer = self.data.map_layer or DEFAULT_MAP_LAYER
        features = visible_features(self.data)
        fixed_view = self._fixed_view
        preview_width = max(560, self.map_preview.winfo_width())
        preview_height = max(380, self.map_preview.winfo_height())
        render_width = 900
        render_height = max(520, min(760, round(render_width * preview_height / preview_width)))
        if self._map_snapshot is None:
            self.map_preview.delete("all")
            self.map_preview.create_text(
                max(130, self.map_preview.winfo_width() / 2),
                max(130, self.map_preview.winfo_height() / 2),
                text="Actualizando mapa…", fill="#526575", font=("Segoe UI", 11, "bold"),
            )
        else:
            self.map_info.configure(text="Actualizando mapa sin interrumpir el dibujo...")

        def worker():
            try:
                center = (fixed_view[0], fixed_view[1]) if fixed_view else None
                zoom = fixed_view[2] if fixed_view else None
                snapshot = render_location_map(
                    points, geometry, layer, features, size=(render_width, render_height), center=center,
                    zoom_override=zoom, draw_selection=False,
                )
                self._map_results.put((token, snapshot, None))
            except Exception as exc:
                self._map_results.put((token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_map_results(self):
        try:
            while True:
                token, snapshot, error = self._map_results.get_nowait()
                self._finish_map(token, snapshot, error)
        except queue.Empty:
            pass
        try:
            while True:
                kind, payload, error = self._vector_results.get_nowait()
                if kind == "progress":
                    self.status_var.set(payload)
                    self.map_info.configure(text=payload)
                    self.vector_progress["value"] = self._vector_progress_value(payload)
                else:
                    self._finish_vectorization(payload, error)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll_map_results)

    def _finish_map(self, token, snapshot, error):
        if token != self._map_token or not self.winfo_exists():
            return
        if error:
            self.map_preview.delete("all")
            self.map_preview.create_text(
                max(130, self.map_preview.winfo_width() / 2),
                max(130, self.map_preview.winfo_height() / 2),
                text=f"No se pudo crear el mapa:\n{error}", fill="#9B2C2C", font=("Segoe UI", 10), justify="center",
            )
            return
        self._map_snapshot = snapshot
        self._show_map_image()
        self._update_map_info()

    def _show_map_image(self):
        if not self._map_snapshot:
            return
        image = self._map_snapshot.image.copy()
        widget_width = max(260, self.map_preview.winfo_width() - 8)
        widget_height = max(260, self.map_preview.winfo_height() - 8)
        image.thumbnail((widget_width, widget_height), Image.Resampling.LANCZOS)
        offset_x = max(0, (self.map_preview.winfo_width() - image.width) / 2)
        offset_y = max(0, (self.map_preview.winfo_height() - image.height) / 2)
        self._display_box = (offset_x, offset_y, image.width, image.height)
        self._map_photo = ImageTk.PhotoImage(image)
        self.map_preview.delete("all")
        self.map_preview.create_image(offset_x, offset_y, image=self._map_photo, anchor="nw", tags=("map-base",))
        self._draw_selection_overlay()

    def _display_point(self, latitude: float, longitude: float) -> tuple[float, float]:
        offset_x, offset_y, width, height = self._display_box
        pixel_x, pixel_y = self._map_snapshot.latlon_to_pixel(latitude, longitude)
        return (
            offset_x + pixel_x / self._map_snapshot.image.width * width,
            offset_y + pixel_y / self._map_snapshot.image.height * height,
        )

    def _draw_selection_overlay(self):
        self.map_preview.delete("selection-overlay")
        if not self._map_snapshot or not self.data.points or not self.data.layer_visibility.get("selection", True):
            return
        points = [self._display_point(point.latitude, point.longitude) for point in self.data.points]
        flat = [coordinate for point in points for coordinate in point]
        if len(points) >= 3:
            self.map_preview.create_polygon(
                *flat, fill="#0B7FAB", stipple="gray25", outline="", tags=("selection-overlay",),
            )
        if len(points) >= 2:
            route = points + ([points[0]] if len(points) >= 3 else [])
            route_flat = [coordinate for point in route for coordinate in point]
            self.map_preview.create_line(*route_flat, fill="white", width=7, joinstyle="round", tags=("selection-overlay",))
            self.map_preview.create_line(*route_flat, fill="#0B7FAB", width=3, joinstyle="round", tags=("selection-overlay",))
        for index, (x, y) in enumerate(points, 1):
            self.map_preview.create_oval(x - 7, y - 7, x + 7, y + 7, fill="white", outline="#0B7FAB", width=3, tags=("selection-overlay",))
            self.map_preview.create_text(x + 12, y - 12, text=f"V{index}", fill="#07356F", font=("Segoe UI", 8, "bold"), anchor="sw", tags=("selection-overlay",))

    def _preview_resized(self, _event=None):
        if not self._map_snapshot:
            return
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(180, self._finish_resize)

    def _finish_resize(self):
        self._resize_job = None
        if self.winfo_exists():
            self._show_map_image()

    def _event_coordinate(self, event):
        return self._coordinate_at_display(event.x, event.y)

    def _coordinate_at_display(self, display_x, display_y):
        if not self._map_snapshot:
            return None
        offset_x, offset_y, width, height = self._display_box
        if not (offset_x <= display_x <= offset_x + width and offset_y <= display_y <= offset_y + height):
            return None
        x = (display_x - offset_x) / width * self._map_snapshot.image.width
        y = (display_y - offset_y) / height * self._map_snapshot.image.height
        return self._map_snapshot.pixel_to_latlon(x, y)

    def _map_press(self, event):
        self.map_preview.focus_set()
        self._drag_start = (event.x, event.y)
        self._drag_last = (event.x, event.y)
        if self.draw_mode_var.get() == "pan":
            self.map_preview.configure(cursor="fleur")

    def _map_motion(self, event):
        if self.draw_mode_var.get() == "pan" and self._drag_start and self._drag_last:
            delta_x = event.x - self._drag_last[0]
            delta_y = event.y - self._drag_last[1]
            self._drag_last = (event.x, event.y)
            self.map_preview.move("map-base", delta_x, delta_y)
            self.map_preview.move("selection-overlay", delta_x, delta_y)
            return
        if self.draw_mode_var.get() != "rectangle" or not self._drag_start:
            return
        start_x, start_y = self._drag_start
        end_x, end_y = event.x, event.y
        if event.state & 0x0001:
            size = max(abs(end_x - start_x), abs(end_y - start_y))
            end_x = start_x + (size if end_x >= start_x else -size)
            end_y = start_y + (size if end_y >= start_y else -size)
        self.map_preview.delete("rectangle-preview")
        self.map_preview.create_rectangle(
            start_x, start_y, end_x, end_y, outline="#0B7FAB", width=3,
            dash=(7, 4), fill="#D7EEF5", stipple="gray25", tags=("rectangle-preview",),
        )

    def _map_release(self, event):
        if not self._map_snapshot or not self._drag_start:
            return
        start_x, start_y = self._drag_start
        self._drag_start = None
        self._drag_last = None
        movement = math.hypot(event.x - start_x, event.y - start_y)
        mode = self.draw_mode_var.get()
        if mode == "polygon":
            if movement > 6:
                return
            coordinate = self._event_coordinate(event)
            if coordinate is None:
                return
            latitude, longitude = coordinate
            point = SketchPoint(f"V{len(self.data.points) + 1}", latitude, longitude)
            self.data.points.append(point)
            self._invalidate_vectors()
            self._refresh_tree(select=len(self.data.points) - 1)
            self.status_var.set(f"{point.name} agregado · continúa o pulsa Enter")
            self._show_map_image()
            return
        if mode == "rectangle":
            self.map_preview.delete("rectangle-preview")
            if movement < 8:
                self.status_var.set("Arrastra para formar el rectángulo")
                return
            end_x, end_y = event.x, event.y
            if event.state & 0x0001:
                size = max(abs(end_x - start_x), abs(end_y - start_y))
                end_x = start_x + (size if end_x >= start_x else -size)
                end_y = start_y + (size if end_y >= start_y else -size)
            offset_x, offset_y, width, height = self._display_box
            start_x = max(offset_x, min(offset_x + width, start_x))
            start_y = max(offset_y, min(offset_y + height, start_y))
            end_x = max(offset_x, min(offset_x + width, end_x))
            end_y = max(offset_y, min(offset_y + height, end_y))
            corners = ((start_x, start_y), (end_x, start_y), (end_x, end_y), (start_x, end_y))
            coordinates = [self._coordinate_at_display(x, y) for x, y in corners]
            if any(coordinate is None for coordinate in coordinates):
                return
            self.data.points = [SketchPoint(f"V{index}", latitude, longitude) for index, (latitude, longitude) in enumerate(coordinates, 1)]
            self._invalidate_vectors()
            self._refresh_tree(select=0)
            self.status_var.set("Rectángulo creado · pulsa Vectorizar mapa")
            self.draw_mode_var.set("pan")
            self.map_preview.configure(cursor="fleur")
            self._show_map_image()
            return
        self.map_preview.configure(cursor="fleur")
        if movement > 6:
            offset_x, offset_y, width, height = self._display_box
            ratio_x = self._map_snapshot.image.width / width
            ratio_y = self._map_snapshot.image.height / height
            new_x = self._map_snapshot.image.width / 2 - (event.x - start_x) * ratio_x
            new_y = self._map_snapshot.image.height / 2 - (event.y - start_y) * ratio_y
            latitude, longitude = self._map_snapshot.pixel_to_latlon(new_x, new_y)
            self._fixed_view = (latitude, longitude, self._map_snapshot.zoom)
            self.status_var.set("Mapa desplazado")
            self.refresh_map()
            return
        coordinate = self._event_coordinate(event)
        if coordinate is None:
            return
        latitude, longitude = coordinate
        self._loading = True
        try:
            if self._editing_point is None:
                self.point_vars["name"].set(f"V{len(self.data.points) + 1}")
            self.point_vars["latitude"].set(f"{latitude:.7f}")
            self.point_vars["longitude"].set(f"{longitude:.7f}")
        finally:
            self._loading = False
        self.notebook.select(1)
        self.status_var.set("Coordenadas capturadas; confirma con Agregar vértice")

    def _map_wheel(self, event):
        self.zoom_map(1 if event.delta > 0 else -1, event)
        return "break"

    def zoom_map(self, direction: int, event=None):
        if not self._map_snapshot:
            return
        current_zoom = self._fixed_view[2] if self._fixed_view else self._map_snapshot.zoom
        max_zoom = MAP_LAYERS.get(self.vars["map_layer"].get(), MAP_LAYERS[DEFAULT_MAP_LAYER])["max_zoom"]
        zoom = max(3, min(max_zoom, current_zoom + direction))
        if zoom == current_zoom:
            return
        coordinate = self._event_coordinate(event) if event is not None else None
        if coordinate and event is not None:
            offset_x, offset_y, width, height = self._display_box
            internal_x = (event.x - offset_x) / width * self._map_snapshot.image.width
            internal_y = (event.y - offset_y) / height * self._map_snapshot.image.height
            target_x, target_y = _xy(coordinate[0], coordinate[1], zoom)
            center_x = target_x * TILE_SIZE - internal_x + self._map_snapshot.image.width / 2
            center_y = target_y * TILE_SIZE - internal_y + self._map_snapshot.image.height / 2
            latitude, longitude = _inverse_xy(center_x / TILE_SIZE, center_y / TILE_SIZE, zoom)
        else:
            latitude, longitude = self._fixed_view[:2] if self._fixed_view else self._map_snapshot.center()
        self._fixed_view = (latitude, longitude, zoom)
        self.status_var.set(f"Zoom {zoom}")
        if self._zoom_job:
            self.after_cancel(self._zoom_job)
        self._zoom_job = self.after(90, self._apply_zoom)

    def _apply_zoom(self):
        self._zoom_job = None
        self.refresh_map()

    def fit_area(self):
        self._fixed_view = None
        self.status_var.set("Ajustando el mapa al área…")
        self.refresh_map()

    def new_sketch(self):
        if not messagebox.askyesno("Nuevo croquis", "¿Crear un croquis nuevo? Los cambios no guardados se perderán."):
            return
        self.data = SketchData()
        saved_layer = str(SETTINGS.get("sketches.map_layer", DEFAULT_MAP_LAYER))
        self.data.map_layer = saved_layer if saved_layer in MAP_LAYERS else DEFAULT_MAP_LAYER
        self.data.contour_interval = str(SETTINGS.get("sketches.contour_interval", "Automática"))
        self.json_path = None
        self._editing_point = None
        self._fixed_view = None
        self.draw_mode_var.set("pan")
        self._load_to_form()
        self.status_var.set("Croquis nuevo")
        self.refresh_map()

    def save_json(self):
        self._sync_data()
        target = self.json_path
        if not target:
            chosen = filedialog.asksaveasfilename(
                title="Guardar croquis editable", defaultextension=".json",
                filetypes=(("Croquis JSON", "*.json"),), initialfile="croquis_ubicacion.json",
                initialdir=str(category_dir("sketches")),
            )
            if not chosen:
                return
            target = Path(chosen)
        self.data.save(target)
        preserve_artifact(target, "sketches")
        backup_editable(target, "Croquis")
        self.json_path = target
        self.status_var.set(f"Guardado: {target.name}")

    def open_json(self):
        chosen = filedialog.askopenfilename(
            title="Abrir croquis", filetypes=(("Croquis JSON", "*.json"),),
            initialdir=str(category_dir("sketches")),
        )
        if not chosen:
            return
        try:
            self.data = SketchData.load(chosen)
            self.json_path = Path(chosen)
            self._editing_point = None
            self._load_to_form()
            self.status_var.set(f"Abierto: {self.json_path.name}")
            self.refresh_map()
        except Exception as exc:
            messagebox.showerror("No se pudo abrir", str(exc))

    def _validate_export(self) -> bool:
        self._sync_data()
        if len(self.data.points) < 3:
            messagebox.showwarning("Sin área", "Selecciona un área con al menos tres vértices antes de exportar.")
            return False
        if not self.data.features:
            messagebox.showwarning("Sin geometría", "Pulsa Vectorizar mapa para obtener la cartografía del área antes de exportar.")
            return False
        if self.data.map_layer.startswith("Topográfico") and not any(feature.category == "contour" for feature in self.data.features):
            messagebox.showwarning("Faltan curvas de nivel", "La capa topográfica requiere volver a vectorizar para generar las curvas de nivel del área.")
            return False
        return True

    def export_pdf(self):
        if not self._validate_export():
            return
        chosen = filedialog.asksaveasfilename(
            title="Exportar croquis PDF", defaultextension=".pdf",
            filetypes=(("Documento PDF", "*.pdf"),), initialfile="CROQUIS DE UBICACION.pdf",
            initialdir=str(category_dir("sketches")),
        )
        if not chosen:
            return
        try:
            output = generate_sketch_pdf(self.data, chosen, self.logo_path)
            preserve_artifact(output, "sketches")
            self._backup_export_editable(output)
            self.status_var.set(f"PDF creado: {output.name}")
            if messagebox.askyesno("Croquis listo", "El PDF se creó correctamente. ¿Deseas abrirlo?"):
                os.startfile(output)
        except Exception as exc:
            messagebox.showerror("No se pudo exportar", str(exc))

    def export_dxf(self):
        if not self._validate_export():
            return
        chosen = filedialog.asksaveasfilename(
            title="Exportar croquis DXF", defaultextension=".dxf",
            filetypes=(("Archivo DXF", "*.dxf"),), initialfile="CROQUIS DE UBICACION.dxf",
            initialdir=str(category_dir("sketches")),
        )
        if not chosen:
            return
        try:
            output = generate_sketch_dxf(self.data, chosen)
            preserve_artifact(output, "sketches")
            self._backup_export_editable(output)
            average_longitude = sum(point.longitude for point in self.data.points) / len(self.data.points)
            zone = max(1, min(60, int((average_longitude + 180) / 6) + 1))
            self.status_var.set(f"DXF creado: {output.name} · UTM zona {zone}")
            messagebox.showinfo("DXF listo", f"Se creó el DXF en metros, proyectado a WGS84 / UTM zona {zone}.\n\n{output}")
        except Exception as exc:
            messagebox.showerror("No se pudo exportar", str(exc))

    def _backup_export_editable(self, output: str | Path):
        target = category_dir("sketches") / f"EDITABLE_{Path(output).stem}.json"
        self.data.save(target)
        backup_editable(target, "Croquis")
