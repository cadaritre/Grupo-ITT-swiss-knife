from __future__ import annotations

import math
import os
import queue
import threading
from pathlib import Path
from tkinter import BooleanVar, Canvas, StringVar, filedialog, messagebox
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from .app_storage import SETTINGS, category_dir, preserve_artifact
from .geospatial_converter import (
    GeoDataset,
    convert_file,
    layer_color,
    read_geospatial,
    suggested_utm,
)
from .location_sketch import DEFAULT_MAP_LAYER, MAP_LAYERS, SketchPoint, render_location_map
from .ux_components import ProgressStrip, attach_tooltip, help_badge


class GeospatialConverterTool(ttk.Frame):
    def __init__(self, master, logo_path: Path, on_home):
        super().__init__(master, style="App.TFrame")
        self.logo_path = logo_path
        self.on_home = on_home
        self.source_path: Path | None = None
        self.dataset: GeoDataset | None = None
        self.layer_vars: dict[str, BooleanVar] = {}
        self._results = queue.Queue()
        self._loading = False
        self._exporting = False
        self._load_token = 0
        self._map_token = 0
        self._map_snapshot = None
        self._map_photo = None
        self._display_box = (0.0, 0.0, 0.0, 0.0)
        self._fixed_view: tuple[float, float, int] | None = None
        self._drag_start = None
        self._drag_last = None
        self._resize_job = None
        self._restoring_settings = False
        self._settings_history = []
        self._last_settings = None
        self.status_var = StringVar(value="Selecciona un archivo DXF, KML o KMZ")
        self.file_var = StringVar(value="Ningún archivo seleccionado")
        self.summary_var = StringVar(value="La geometría aparecerá aquí antes de exportarla.")
        self.warning_var = StringVar(value="")
        self.coordinate_diagnostic_var = StringVar(value="Esperando coordenadas del archivo…")
        try:
            saved_zone = max(1, min(60, int(SETTINGS.get("geospatial.utm_zone", 13) or 13)))
        except (TypeError, ValueError):
            saved_zone = 13
        saved_hemisphere = str(SETTINGS.get("geospatial.hemisphere", "N")).upper()
        saved_map = str(SETTINGS.get("geospatial.map_layer", DEFAULT_MAP_LAYER))
        self.zone_var = StringVar(value=str(saved_zone))
        self.hemisphere_var = StringVar(value=saved_hemisphere if saved_hemisphere in {"N", "S"} else "N")
        self.output_var = StringVar(value="KMZ")
        self.map_layer_var = StringVar(value=saved_map if saved_map in MAP_LAYERS else DEFAULT_MAP_LAYER)
        epsg = (32700 if self.hemisphere_var.get() == "S" else 32600) + saved_zone
        self.crs_var = StringVar(value=f"WGS84 · EPSG:{epsg}")
        self.labels_var = BooleanVar(value=bool(SETTINGS.get("geospatial.labels", True)))
        self.hatches_var = BooleanVar(value=bool(SETTINGS.get("geospatial.hatches", True)))
        self.ground_var = BooleanVar(value=bool(SETTINGS.get("geospatial.clamp_to_ground", True)))
        self._build()
        self._last_settings = self._settings_snapshot()
        self.after(120, self._poll_results)
        self.after(300, self.refresh_map)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self, style="Header.TFrame", padding=(18, 10))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="‹ Herramientas", style="HeaderButton.TButton", command=self.on_home).pack(side="left")
        ttk.Label(toolbar, text="Convertidor DXF ↔ KML/KMZ", style="HeaderTitle.TLabel").pack(side="left", padx=16)
        ttk.Button(toolbar, text="Abrir archivo", style="HeaderButton.TButton", command=self.choose_file).pack(side="left", padx=(8, 2))
        undo_button = ttk.Button(toolbar, text="↶ Deshacer", style="HeaderButton.TButton", command=self.undo_settings)
        undo_button.pack(side="left", padx=2)
        attach_tooltip(undo_button, "Recupera la configuración anterior de proyección, salida y capas.")
        reset_button = ttk.Button(toolbar, text="Restablecer", style="HeaderButton.TButton", command=self.reset_settings)
        reset_button.pack(side="left", padx=2)
        attach_tooltip(reset_button, "Regresa a UTM 13N, activa todas las capas y restaura las opciones recomendadas.")
        self.export_button = ttk.Button(toolbar, text="Exportar", style="HeaderAccent.TButton", command=self.export_file)
        self.export_button.pack(side="right")
        self.export_button.state(["disabled"])
        self.progress_strip = ProgressStrip(toolbar, 145)
        ttk.Label(toolbar, textvariable=self.status_var, style="HeaderSub.TLabel").pack(side="right", padx=14)

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        controls = ttk.Frame(panes, style="Card.TFrame", width=430)
        preview = ttk.Frame(panes, style="Card.TFrame", padding=12)
        panes.add(controls, weight=2)
        panes.add(preview, weight=4)
        self._build_scrolled_controls(controls)
        self._build_preview(preview)

    def _build_scrolled_controls(self, host):
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        self.controls_canvas = Canvas(host, background="white", highlightthickness=0)
        self.controls_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(host, orient="vertical", command=self.controls_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.controls_canvas.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(self.controls_canvas, style="Card.TFrame", padding=14)
        window = self.controls_canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: self.controls_canvas.configure(scrollregion=self.controls_canvas.bbox("all")))
        self.controls_canvas.bind("<Configure>", lambda event: self.controls_canvas.itemconfigure(window, width=event.width))
        self.controls_canvas.bind("<MouseWheel>", lambda event: self.controls_canvas.yview_scroll(int(-event.delta / 120), "units"))
        self._build_controls(body)

    def _build_controls(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(7, weight=1)
        ttk.Label(parent, text="ARCHIVO DE ORIGEN", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        source = ttk.Frame(parent, style="Soft.TFrame", padding=11)
        source.grid(row=1, column=0, sticky="ew", pady=(7, 12))
        source.columnconfigure(0, weight=1)
        ttk.Label(source, textvariable=self.file_var, style="SoftSection.TLabel", wraplength=350, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Label(source, textvariable=self.summary_var, style="SoftHint.TLabel", wraplength=350, justify="left").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Button(source, text="Seleccionar DXF, KML o KMZ", style="Accent.TButton", command=self.choose_file).grid(row=2, column=0, sticky="w", pady=(9, 0))

        ttk.Label(parent, text="PROYECCIÓN", style="Section.TLabel").grid(row=2, column=0, sticky="w")
        projection = ttk.Frame(parent, style="Soft.TFrame", padding=11)
        projection.grid(row=3, column=0, sticky="ew", pady=(7, 12))
        projection.columnconfigure(0, weight=1)
        projection.columnconfigure(1, weight=1)
        self._field(projection, 0, 0, "Zona UTM", self.zone_var, [str(value) for value in range(1, 61)], 10, help_text="Zona longitudinal del sistema UTM original. Chihuahua normalmente utiliza 13N; no cambia las unidades, que siempre se interpretan como metros.")
        self._field(projection, 0, 1, "Hemisferio", self.hemisphere_var, ["N", "S"], 10, help_text="Usa N para México. El hemisferio modifica el falso norte empleado por la proyección UTM.")
        buttons = ttk.Frame(projection, style="Soft.TFrame")
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Detectar zona desde KML", style="Secondary.TButton", command=self.detect_projection).pack(side="left")
        ttk.Label(buttons, textvariable=self.crs_var, style="SoftHint.TLabel").pack(side="right", padx=3)
        ttk.Label(
            projection,
            text="Para DXF debes indicar la zona original. Para KML/KMZ esta será la zona del DXF de salida.",
            style="SoftHint.TLabel", wraplength=350, justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            projection, textvariable=self.coordinate_diagnostic_var,
            style="SoftSection.TLabel", wraplength=350, justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))

        ttk.Label(parent, text="SALIDA", style="Section.TLabel").grid(row=4, column=0, sticky="w")
        output = ttk.Frame(parent, style="Soft.TFrame", padding=11)
        output.grid(row=5, column=0, sticky="ew", pady=(7, 12))
        output.columnconfigure(0, weight=1)
        self.output_combo = ttk.Combobox(output, textvariable=self.output_var, values=("KMZ", "KML"), state="readonly")
        self.output_combo.grid(row=0, column=0, sticky="ew")
        self.output_combo.bind("<<ComboboxSelected>>", self._option_changed)
        ttk.Checkbutton(output, text="Conservar nombres como etiquetas", variable=self.labels_var, command=self._option_changed).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(output, text="Crear rellenos para polígonos en DXF", variable=self.hatches_var, command=self._option_changed).grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.ground_check = ttk.Checkbutton(
            output,
            text="Pegar KML/KMZ al relieve de Google Earth",
            variable=self.ground_var,
            command=self._option_changed,
        )
        self.ground_check.grid(row=3, column=0, sticky="w", pady=(4, 0))
        attach_tooltip(self.ground_check, "Hace que Google Earth dibuje puntos, líneas y polígonos sobre el relieve para evitar que queden enterrados bajo el terreno 3D.")

        layer_header = ttk.Frame(parent, style="Card.TFrame")
        layer_header.grid(row=6, column=0, sticky="ew")
        ttk.Label(layer_header, text="CAPAS A EXPORTAR", style="Section.TLabel").pack(side="left")
        ttk.Button(layer_header, text="Todas", style="Secondary.TButton", command=lambda: self._set_all_layers(True)).pack(side="right")
        ttk.Button(layer_header, text="Ninguna", style="Secondary.TButton", command=lambda: self._set_all_layers(False)).pack(side="right", padx=4)
        layer_box = ttk.Frame(parent, style="Card.TFrame")
        layer_box.grid(row=7, column=0, sticky="nsew", pady=(7, 0))
        layer_box.columnconfigure(0, weight=1)
        layer_box.rowconfigure(0, weight=1)
        self.layer_canvas = Canvas(layer_box, height=175, background="#EEF3F6", highlightthickness=1, highlightbackground="#D5E0E6")
        self.layer_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(layer_box, orient="vertical", command=self.layer_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.layer_canvas.configure(yscrollcommand=scrollbar.set)
        self.layer_frame = ttk.Frame(self.layer_canvas, style="Soft.TFrame", padding=5)
        self.layer_window = self.layer_canvas.create_window((0, 0), window=self.layer_frame, anchor="nw")
        self.layer_frame.bind("<Configure>", self._layer_frame_changed)
        self.layer_canvas.bind("<Configure>", lambda event: self.layer_canvas.itemconfigure(self.layer_window, width=event.width))
        self.layer_canvas.bind("<MouseWheel>", self._layer_wheel)

        self.warning_label = ttk.Label(parent, textvariable=self.warning_var, style="Hint.Card.TLabel", foreground="#9A5D00", wraplength=380, justify="left")
        self.warning_label.grid(row=8, column=0, sticky="ew", pady=(8, 0))

        self.zone_var.trace_add("write", self._projection_changed)
        self.hemisphere_var.trace_add("write", self._projection_changed)

    def _field(self, parent, row, column, label, variable, values, width, colspan=1, help_text=None):
        box = ttk.Frame(parent, style="Soft.TFrame")
        box.grid(row=row, column=column, columnspan=colspan, sticky="ew", padx=(0, 4) if column == 0 and colspan == 1 else (4, 0) if column else 0, pady=3)
        heading = ttk.Frame(box, style="Soft.TFrame")
        heading.pack(fill="x")
        label_widget = ttk.Label(heading, text=label, style="SoftHint.TLabel")
        label_widget.pack(side="left")
        if help_text:
            help_badge(heading, help_text).pack(side="left", padx=(3, 0))
            attach_tooltip(label_widget, help_text)
        combo = ttk.Combobox(box, textvariable=variable, values=values, state="readonly", width=width)
        combo.pack(fill="x", pady=(2, 0))
        if help_text:
            attach_tooltip(combo, help_text)
        return combo

    def _build_preview(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        heading = ttk.Frame(parent, style="Card.TFrame")
        heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(heading, text="VISTA PREVIA GEOGRÁFICA", style="Section.TLabel").pack(side="left")
        ttk.Button(heading, text="Ajustar geometría", style="Secondary.TButton", command=self.fit_geometry).pack(side="right")
        controls = ttk.Frame(parent, style="Soft.TFrame", padding=(8, 5))
        controls.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        ttk.Label(controls, text="Mapa base", style="SoftHint.TLabel").pack(side="left", padx=(0, 5))
        combo = ttk.Combobox(controls, textvariable=self.map_layer_var, values=list(MAP_LAYERS), state="readonly", width=25)
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", self._map_layer_changed)
        ttk.Button(controls, text="−", width=3, style="Secondary.TButton", command=lambda: self.zoom_map(-1)).pack(side="right", padx=(3, 0))
        ttk.Button(controls, text="+", width=3, style="Secondary.TButton", command=lambda: self.zoom_map(1)).pack(side="right", padx=(8, 0))
        self.map_info = ttk.Label(parent, text="Mapa listo para mostrar la conversión", style="Hint.Card.TLabel")
        self.map_info.grid(row=2, column=0, sticky="w", pady=(7, 6))
        self.map_preview = Canvas(parent, background="#DCE5EA", highlightthickness=1, highlightbackground="#B8C7D1", cursor="fleur")
        self.map_preview.grid(row=3, column=0, sticky="nsew")
        self.map_preview.bind("<Configure>", self._preview_resized)
        self.map_preview.bind("<ButtonPress-1>", self._map_press)
        self.map_preview.bind("<B1-Motion>", self._map_motion)
        self.map_preview.bind("<ButtonRelease-1>", self._map_release)
        self.map_preview.bind("<MouseWheel>", self._map_wheel)
        ttk.Label(
            parent,
            text="Rueda: zoom · Arrastra: mover · Las capas apagadas tampoco se exportarán.",
            style="Hint.Card.TLabel",
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))

    def _layer_frame_changed(self, _event=None):
        self.layer_canvas.configure(scrollregion=self.layer_canvas.bbox("all"))

    def _layer_wheel(self, event):
        self.layer_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def choose_file(self):
        chosen = filedialog.askopenfilename(
            title="Abrir archivo geoespacial",
            filetypes=(("DXF, KML y KMZ", "*.dxf *.kml *.kmz"), ("DXF", "*.dxf"), ("Google Earth", "*.kml *.kmz")),
        )
        if chosen:
            self.load_path(chosen)

    def load_path(self, path: str | Path):
        self.source_path = Path(path)
        self.file_var.set(self.source_path.name)
        self._start_loading()

    def _start_loading(self):
        if not self.source_path:
            return
        try:
            zone = int(self.zone_var.get())
        except ValueError:
            return
        self._remember_settings()
        self._load_token += 1
        token = self._load_token
        source = self.source_path
        hemisphere = self.hemisphere_var.get()
        self._loading = True
        self.export_button.state(["disabled"])
        self.status_var.set(f"Leyendo {source.name}…")
        self.progress_strip.show(0, f"Leyendo {source.name}…")
        self.map_info.configure(text="Transformando coordenadas y preparando geometría…")

        def worker():
            try:
                def progress(fraction, message):
                    self._results.put(("progress", token, (fraction, message), None))
                dataset = read_geospatial(source, zone, hemisphere, progress=progress)
                self._results.put(("load", token, dataset, None))
            except Exception as exc:
                self._results.put(("load", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _projection_changed(self, *_args):
        try:
            zone = int(self.zone_var.get())
            epsg = (32700 if self.hemisphere_var.get().upper() == "S" else 32600) + zone
            self.crs_var.set(f"WGS84 · EPSG:{epsg}")
        except ValueError:
            self.crs_var.set("WGS84 / UTM")
        if self._restoring_settings:
            return
        self._remember_settings()
        self._persist_settings()
        if self._loading or not self.source_path or self.source_path.suffix.lower() != ".dxf":
            return
        self.after(250, self._start_loading)

    def _option_changed(self, _event=None):
        if not self._restoring_settings:
            self._remember_settings()
            self._persist_settings()

    def _map_layer_changed(self, _event=None):
        self._option_changed()
        self.refresh_map()

    def _persist_settings(self):
        try:
            zone = max(1, min(60, int(self.zone_var.get())))
        except ValueError:
            zone = 13
        SETTINGS.update({
            "geospatial.utm_zone": zone,
            "geospatial.hemisphere": self.hemisphere_var.get().upper(),
            "geospatial.map_layer": self.map_layer_var.get(),
            "geospatial.labels": bool(self.labels_var.get()),
            "geospatial.hatches": bool(self.hatches_var.get()),
            "geospatial.clamp_to_ground": bool(self.ground_var.get()),
        })

    def _finish_loading(self, token, dataset, error):
        if token != self._load_token:
            return
        self._loading = False
        if error:
            self.progress_strip.hide()
            self.dataset = None
            self.status_var.set("No se pudo abrir el archivo")
            self.map_info.configure(text=str(error))
            messagebox.showerror("No se pudo abrir", str(error))
            return
        self.dataset = dataset
        suffix = self.source_path.suffix.lower() if self.source_path else ""
        if suffix == ".dxf":
            self.output_combo.configure(values=("KMZ", "KML"))
            self.output_var.set("KMZ")
            self.ground_check.configure(state="normal")
        else:
            self.output_combo.configure(values=("DXF",))
            self.output_var.set("DXF")
            self.ground_check.configure(state="disabled")
        self._rebuild_layers()
        counts = dataset.geometry_counts()
        pieces = [f"{label}: {counts.get(key, 0)}" for key, label in (("Point", "puntos"), ("LineString", "líneas"), ("Polygon", "polígonos")) if counts.get(key)]
        self.summary_var.set(f"{len(dataset.features):,} elementos · {len(dataset.layer_counts())} capas" + (" · " + " · ".join(pieces) if pieces else ""))
        self.warning_var.set("  ".join(f"⚠ {warning}" for warning in dataset.warnings))
        if dataset.source_format == "DXF":
            metadata = dataset.metadata
            self.coordinate_diagnostic_var.set(
                f"{metadata.get('utm_bounds', 'Sin rango UTM')} · "
                f"UTM {metadata.get('utm_zone', self.zone_var.get() + self.hemisphere_var.get())}"
            )
            self.status_var.set(f"DXF listo · UTM {metadata.get('utm_zone', '')}")
        else:
            bounds = dataset.bounds()
            self.coordinate_diagnostic_var.set(
                f"WGS84 · lon {bounds[0]:.6f}–{bounds[2]:.6f} · lat {bounds[1]:.6f}–{bounds[3]:.6f}"
                if bounds else "KML/KMZ sin extensión geográfica"
            )
            self.status_var.set(f"{dataset.source_format} listo para convertir")
        self.export_button.state(["!disabled"])
        self._last_settings = self._settings_snapshot()
        self.progress_strip.finish("Archivo leído")
        self._fixed_view = None
        self.refresh_map()

    def _rebuild_layers(self):
        for child in self.layer_frame.winfo_children():
            child.destroy()
        self.layer_vars = {}
        if not self.dataset:
            return
        for row, (layer, count) in enumerate(sorted(self.dataset.layer_counts().items(), key=lambda item: item[0].casefold())):
            variable = BooleanVar(value=True)
            self.layer_vars[layer] = variable
            card = ttk.Frame(self.layer_frame, style="Soft.TFrame", padding=(6, 5))
            card.grid(row=row, column=0, sticky="ew")
            card.columnconfigure(1, weight=1)
            swatch = Canvas(card, width=14, height=14, highlightthickness=0, background="#EEF3F6")
            swatch.grid(row=0, column=0, padx=(0, 5))
            swatch.create_rectangle(1, 1, 13, 13, fill=layer_color(layer), outline="")
            ttk.Checkbutton(card, text=layer, variable=variable, command=self._layers_changed).grid(row=0, column=1, sticky="w")
            ttk.Label(card, text=f"{count:,}", style="SoftHint.TLabel").grid(row=0, column=2, sticky="e", padx=(6, 0))
        self.layer_frame.columnconfigure(0, weight=1)
        self.after_idle(self._layer_frame_changed)

    def _set_all_layers(self, value: bool):
        for variable in self.layer_vars.values():
            variable.set(value)
        self._layers_changed()

    def _layers_changed(self):
        if not self._restoring_settings:
            self._remember_settings()
        selected = len(self.selected_layers())
        total = len(self.layer_vars)
        self.status_var.set(f"{selected} de {total} capas activas")
        self.refresh_map()

    def selected_layers(self) -> set[str]:
        return {layer for layer, variable in self.layer_vars.items() if variable.get()}

    def _settings_snapshot(self):
        return {
            "zone": self.zone_var.get(), "hemisphere": self.hemisphere_var.get(),
            "output": self.output_var.get(), "map": self.map_layer_var.get(),
            "labels": bool(self.labels_var.get()), "hatches": bool(self.hatches_var.get()),
            "ground": bool(self.ground_var.get()), "layers": tuple(sorted(self.selected_layers())),
        }

    def _remember_settings(self):
        if self._restoring_settings:
            return
        current = self._settings_snapshot()
        if self._last_settings is not None and current != self._last_settings:
            self._settings_history.append(self._last_settings)
            self._settings_history = self._settings_history[-20:]
        self._last_settings = current

    def _apply_settings(self, snapshot):
        self._restoring_settings = True
        try:
            self.zone_var.set(snapshot["zone"])
            self.hemisphere_var.set(snapshot["hemisphere"])
            self.output_var.set(snapshot["output"])
            self.map_layer_var.set(snapshot["map"])
            self.labels_var.set(snapshot["labels"])
            self.hatches_var.set(snapshot["hatches"])
            self.ground_var.set(snapshot["ground"])
            active = set(snapshot["layers"])
            for layer, variable in self.layer_vars.items():
                variable.set(layer in active if active else True)
        finally:
            self._restoring_settings = False
        self._last_settings = self._settings_snapshot()

    def undo_settings(self):
        if not self._settings_history:
            self.status_var.set("No hay una configuración anterior para deshacer")
            return
        self._apply_settings(self._settings_history.pop())
        self._persist_settings()
        self.status_var.set("Configuración anterior recuperada")
        if self.source_path and self.source_path.suffix.lower() == ".dxf":
            self._start_loading()
        else:
            self._fixed_view = None
            self.refresh_map()

    def reset_settings(self):
        self._settings_history.append(self._settings_snapshot())
        default_output = "DXF" if self.source_path and self.source_path.suffix.lower() in {".kml", ".kmz"} else "KMZ"
        snapshot = {
            "zone": "13", "hemisphere": "N", "output": default_output, "map": DEFAULT_MAP_LAYER,
            "labels": True, "hatches": True, "ground": True, "layers": tuple(self.layer_vars),
        }
        self._apply_settings(snapshot)
        self._persist_settings()
        self.status_var.set("Opciones restablecidas")
        if self.source_path and self.source_path.suffix.lower() == ".dxf":
            self._start_loading()
        else:
            self._fixed_view = None
            self.refresh_map()

    def detect_projection(self):
        if not self.dataset:
            messagebox.showinfo("Sin geometría", "Primero selecciona un archivo KML o KMZ.")
            return
        if self.source_path and self.source_path.suffix.lower() == ".dxf":
            messagebox.showinfo("DXF sin referencia", "Una zona UTM no puede deducirse únicamente de coordenadas X/Y. Selecciona la zona original del levantamiento.")
            return
        zone, hemisphere = suggested_utm(self.dataset)
        self.zone_var.set(str(zone))
        self.hemisphere_var.set(hemisphere)
        self.status_var.set(f"Proyección sugerida: UTM {zone}{hemisphere}")

    def export_file(self):
        if not self.dataset or not self.source_path or self._exporting:
            return
        layers = self.selected_layers()
        if not layers:
            messagebox.showwarning("Sin capas", "Activa al menos una capa para exportar.")
            return
        output_format = self.output_var.get().lower()
        chosen = filedialog.asksaveasfilename(
            title=f"Exportar {output_format.upper()}",
            defaultextension=f".{output_format}",
            filetypes=((output_format.upper(), f"*.{output_format}"),),
            initialfile=f"{self.source_path.stem}_convertido.{output_format}",
            initialdir=str(category_dir("conversions")),
        )
        if not chosen:
            return
        try:
            zone = int(self.zone_var.get())
        except ValueError:
            messagebox.showwarning("Zona inválida", "Selecciona una zona UTM entre 1 y 60.")
            return
        target = Path(chosen)
        hemisphere = self.hemisphere_var.get()
        labels = bool(self.labels_var.get())
        hatches = bool(self.hatches_var.get())
        clamp_to_ground = bool(self.ground_var.get())
        dataset = self.dataset
        self._exporting = True
        self.export_button.state(["disabled"])
        self.status_var.set(f"Creando {target.name}…")
        token = self._load_token
        self.progress_strip.show(0, f"Creando {target.name}…")

        def worker():
            try:
                def progress(fraction, message):
                    self._results.put(("progress", token, (fraction, message), None))
                result = convert_file(dataset, target, zone, hemisphere, layers, labels, hatches, clamp_to_ground, progress)
                self._results.put(("export", token, result, None))
            except Exception as exc:
                self._results.put(("export", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_export(self, result, error):
        self._exporting = False
        self.export_button.state(["!disabled"])
        if error:
            self.progress_strip.hide()
            self.status_var.set("No se pudo convertir")
            messagebox.showerror("Error de conversión", str(error))
            return
        self.status_var.set(f"Archivo creado: {result.name}")
        preserve_artifact(result, "conversions")
        self.progress_strip.finish("Conversión terminada")
        if messagebox.askyesno("Conversión terminada", f"Se creó correctamente:\n\n{result}\n\n¿Deseas abrir su carpeta?"):
            os.startfile(str(result.parent))

    def refresh_map(self):
        self._map_token += 1
        token = self._map_token
        dataset = self.dataset
        selected = self.selected_layers()
        fixed_view = self._fixed_view
        layer = self.map_layer_var.get() or DEFAULT_MAP_LAYER
        preview_width = max(560, self.map_preview.winfo_width())
        preview_height = max(380, self.map_preview.winfo_height())
        render_width = 1000
        render_height = max(540, min(780, round(render_width * preview_height / preview_width)))
        bounds = dataset.bounds() if dataset else None
        points = []
        if bounds:
            points = [SketchPoint("", bounds[1], bounds[0]), SketchPoint("", bounds[3], bounds[2])]
        self.map_info.configure(text="Actualizando vista previa…")

        def worker():
            try:
                center = (fixed_view[0], fixed_view[1]) if fixed_view else None
                zoom = fixed_view[2] if fixed_view else None
                snapshot = render_location_map(points, "Línea", layer, [], (render_width, render_height), center, zoom, False)
                image = snapshot.image.convert("RGBA")
                if dataset:
                    self._draw_dataset(image, snapshot, dataset, selected)
                self._results.put(("map", token, (snapshot, image.convert("RGB")), None))
            except Exception as exc:
                self._results.put(("map", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _draw_dataset(image: Image.Image, snapshot, dataset: GeoDataset, selected: set[str]):
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        for feature in dataset.features:
            if feature.layer not in selected:
                continue
            color = layer_color(feature.layer)
            red, green, blue = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
            if feature.geometry_type == "Polygon" and feature.parts:
                pixel_parts = [
                    [snapshot.latlon_to_pixel(latitude, longitude) for longitude, latitude, _altitude in part]
                    for part in feature.parts
                ]
                if len(pixel_parts[0]) >= 3:
                    draw.polygon(pixel_parts[0], fill=(red, green, blue, 34), outline=(red, green, blue, 235), width=2)
                    for inner in pixel_parts[1:]:
                        if len(inner) >= 3:
                            draw.polygon(inner, fill=(0, 0, 0, 0), outline=(red, green, blue, 180), width=1)
                continue
            for part in feature.parts:
                pixels = [snapshot.latlon_to_pixel(latitude, longitude) for longitude, latitude, _altitude in part]
                if not pixels:
                    continue
                if feature.geometry_type == "Point" or len(pixels) == 1:
                    x, y = pixels[0]
                    draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(red, green, blue, 245), outline=(255, 255, 255, 230), width=1)
                else:
                    draw.line(pixels, fill=(255, 255, 255, 225), width=5, joint="curve")
                    draw.line(pixels, fill=(red, green, blue, 245), width=2, joint="curve")
        image.alpha_composite(overlay)

    def _poll_results(self):
        try:
            while True:
                kind, token, result, error = self._results.get_nowait()
                if kind == "load":
                    self._finish_loading(token, result, error)
                elif kind == "export":
                    self._finish_export(result, error)
                elif kind == "progress" and token == self._load_token:
                    fraction, message = result
                    self.progress_strip.update_progress(fraction * 100, message)
                    self.status_var.set(message)
                elif kind == "map":
                    self._finish_map(token, result, error)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll_results)

    def _finish_map(self, token, result, error):
        if token != self._map_token:
            return
        if error:
            self.map_info.configure(text=f"No se pudo actualizar el mapa: {error}")
            return
        snapshot, image = result
        snapshot.image = image
        self._map_snapshot = snapshot
        self._show_map_image()
        if self.dataset:
            bounds = self.dataset.bounds()
            coordinate_text = ""
            if bounds:
                coordinate_text = f" · {bounds[1]:.5f}, {bounds[0]:.5f} a {bounds[3]:.5f}, {bounds[2]:.5f}"
            self.map_info.configure(text=f"{len(self.dataset.features):,} elementos · {len(self.selected_layers())} capas visibles{coordinate_text}")
        else:
            self.map_info.configure(text="Selecciona un archivo para mostrar su geometría")

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

    def _preview_resized(self, _event=None):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(180, self._finish_resize)

    def _finish_resize(self):
        self._resize_job = None
        if self._map_snapshot:
            self._show_map_image()

    def fit_geometry(self):
        self._fixed_view = None
        self.refresh_map()

    def zoom_map(self, delta: int):
        if not self._map_snapshot:
            return
        center = self._map_snapshot.center()
        maximum = MAP_LAYERS.get(self.map_layer_var.get(), MAP_LAYERS[DEFAULT_MAP_LAYER])["max_zoom"]
        zoom = max(3, min(maximum, self._map_snapshot.zoom + delta))
        self._fixed_view = (center[0], center[1], zoom)
        self.refresh_map()

    def _map_wheel(self, event):
        self.zoom_map(1 if event.delta > 0 else -1)
        return "break"

    def _map_press(self, event):
        self._drag_start = (event.x, event.y)
        self._drag_last = (event.x, event.y)

    def _map_motion(self, event):
        if not self._drag_last:
            return
        dx, dy = event.x - self._drag_last[0], event.y - self._drag_last[1]
        self._drag_last = (event.x, event.y)
        self.map_preview.move("map-base", dx, dy)

    def _map_release(self, event):
        if not self._drag_start or not self._map_snapshot:
            return
        dx, dy = event.x - self._drag_start[0], event.y - self._drag_start[1]
        self._drag_start = self._drag_last = None
        if math.hypot(dx, dy) < 3:
            self._show_map_image()
            return
        _ox, _oy, shown_width, shown_height = self._display_box
        scale_x = self._map_snapshot.image.width / max(shown_width, 1)
        scale_y = self._map_snapshot.image.height / max(shown_height, 1)
        latitude, longitude = self._map_snapshot.pixel_to_latlon(
            self._map_snapshot.image.width / 2 - dx * scale_x,
            self._map_snapshot.image.height / 2 - dy * scale_y,
        )
        self._fixed_view = (latitude, longitude, self._map_snapshot.zoom)
        self.refresh_map()
