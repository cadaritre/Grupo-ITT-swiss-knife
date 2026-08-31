from __future__ import annotations

import math
import os
import queue
import threading
from pathlib import Path
from tkinter import BooleanVar, Button, Canvas, StringVar, colorchooser, filedialog, messagebox
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from .app_storage import SETTINGS
from .triangulation import (
    FlowRange,
    SlopeRange,
    analyze_flow,
    analyze_slopes,
    clone_default_flow_ranges,
    clone_default_ranges,
    create_tin_model,
    extract_triangles,
    triangle_layers,
    write_flow_dxf,
    write_slope_dxf,
    write_tin_dxf,
)
from .ux_components import ProgressStrip, attach_tooltip, help_badge


TECHNICAL_HELP = {
    "Arista máxima (m, opcional)": "Elimina triángulos cuya arista más larga supere este valor. Es útil para que el TIN no conecte puntos a través de calles, huecos o límites muy separados.",
    "Área mínima (m²)": "Descarta triángulos casi degenerados. En levantamientos normales puede dejarse en 0.000001 m².",
    "Decimales para duplicados XY": "Dos puntos con el mismo XY redondeado a esta precisión se consideran duplicados. Se conserva la primera elevación Z.",
    "Longitud base (m, vacío = auto)": "Longitud de referencia de las flechas. En automático se calcula con el tamaño mediano de los triángulos.",
    "Tamaño de punta (%)": "Proporción de la longitud total usada para la cabeza de la flecha. Un valor entre 20% y 35% suele verse bien.",
    "Dibujar cada N triángulos": "Controla la densidad visual. 1 dibuja todas las flechas; 3 dibuja una de cada tres.",
    "Pendiente mínima (%)": "No dibuja flechas en superficies más planas que este valor, donde la dirección puede ser poco significativa.",
}


class ScrolledPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = Canvas(self, background="white", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.body = ttk.Frame(self.canvas, style="Card.TFrame", padding=14)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.window, width=event.width))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.body.bind("<MouseWheel>", self._wheel)

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"


class TriangulationTool(ttk.Frame):
    def __init__(self, master, logo_path: Path, on_home):
        super().__init__(master, style="App.TFrame")
        self.logo_path = logo_path
        self.on_home = on_home
        self.tin_path: Path | None = None
        self.slope_path: Path | None = None
        self.flow_path: Path | None = None
        self.tin_model = None
        self.slope_analysis = None
        self.flow_analysis = None
        self._results = queue.Queue()
        self._token = 0
        self._working = False
        self._preview_kind = "tin"
        self._view_bounds = None
        self._drag_start = None
        self._drag_last = None
        self._resize_job = None
        self._preview_photo = None
        self._history = {"tin": [], "slope": [], "flow": []}
        self._last_applied = {}
        self.range_rows: list[dict] = []
        self.flow_range_rows: list[dict] = []
        self.status_var = StringVar(value="Selecciona un DXF para comenzar")
        self.tin_file_var = StringVar(value="Ningún archivo seleccionado")
        self.slope_file_var = StringVar(value="Ningún archivo seleccionado")
        self.flow_file_var = StringVar(value="Ningún archivo seleccionado")
        self.tin_summary_var = StringVar(value="La triangulación calculada quedará lista para revisar o exportar.")
        self.slope_summary_var = StringVar(value="La zonificación calculada quedará lista para revisar o exportar.")
        self.flow_summary_var = StringVar(value="Los escurrimientos calculados quedarán listos para revisar o exportar.")
        self.include_points_var = BooleanVar(value=True)
        self.include_inserts_var = BooleanVar(value=True)
        self.include_poly_vertices_var = BooleanVar(value=False)
        self.max_edge_var = StringVar(value="")
        self.min_area_var = StringVar(value="0.000001")
        self.dedup_var = StringVar(value="6")
        self.write_points_var = BooleanVar(value=True)
        self.slope_text_var = BooleanVar(value=False)
        self.slope_faces_var = BooleanVar(value=True)
        self.decimals_var = StringVar(value="2")
        self.flow_base_length_var = StringVar(value="")
        self.flow_head_size_var = StringVar(value="28")
        self.flow_density_var = StringVar(value="1")
        self.flow_min_slope_var = StringVar(value="0.10")
        self.flow_tin_reference_var = BooleanVar(value=True)
        self.flow_slope_text_var = BooleanVar(value=False)
        self.draw_preview_var = BooleanVar(value=bool(SETTINGS.get("triangulation.draw_preview", True)))
        self._build()
        self.after(100, self._poll_results)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self, style="Header.TFrame", padding=(18, 10))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="‹ Herramientas", style="HeaderButton.TButton", command=self.on_home).pack(side="left")
        ttk.Label(toolbar, text="Herramientas de triangulación DXF", style="HeaderTitle.TLabel").pack(side="left", padx=16)
        undo_button = ttk.Button(toolbar, text="↶ Deshacer", style="HeaderButton.TButton", command=self.undo_configuration)
        undo_button.pack(side="left", padx=(4, 2))
        attach_tooltip(undo_button, "Recupera la configuración aplicada en el cálculo anterior de esta pestaña.")
        reset_button = ttk.Button(toolbar, text="Restablecer", style="HeaderButton.TButton", command=self.reset_configuration)
        reset_button.pack(side="left", padx=2)
        attach_tooltip(reset_button, "Devuelve los parámetros de la pestaña actual a sus valores recomendados.")
        self.export_button = ttk.Button(toolbar, text="Exportar DXF", style="HeaderAccent.TButton", command=self.export_current)
        self.export_button.pack(side="right")
        self.export_button.state(["disabled"])
        self.progress_strip = ProgressStrip(toolbar, 150)
        ttk.Label(toolbar, textvariable=self.status_var, style="HeaderSub.TLabel").pack(side="right", padx=14)

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        controls = ttk.Frame(panes, style="Card.TFrame", width=455)
        preview = ttk.Frame(panes, style="Card.TFrame", padding=12)
        panes.add(controls, weight=2)
        panes.add(preview, weight=5)
        controls.columnconfigure(0, weight=1)
        controls.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(controls)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self._build_tin_tab()
        self._build_slope_tab()
        self._build_flow_tab()
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._build_preview(preview)

    @staticmethod
    def _section(parent, text, row):
        ttk.Label(parent, text=text, style="Section.TLabel").grid(row=row, column=0, sticky="w", pady=(12 if row else 0, 5))

    def _build_tin_tab(self):
        panel = ScrolledPanel(self.notebook)
        self.notebook.add(panel, text="Puntos → TIN")
        body = panel.body
        body.columnconfigure(0, weight=1)
        self._section(body, "1. DXF CON PUNTOS", 0)
        source = ttk.Frame(body, style="Soft.TFrame", padding=11)
        source.grid(row=1, column=0, sticky="ew")
        ttk.Label(source, textvariable=self.tin_file_var, style="SoftSection.TLabel", wraplength=365).pack(anchor="w")
        ttk.Label(source, textvariable=self.tin_summary_var, style="SoftHint.TLabel", wraplength=365, justify="left").pack(anchor="w", pady=(5, 8))
        ttk.Button(source, text="Seleccionar DXF de puntos", style="Accent.TButton", command=self.choose_tin_file).pack(anchor="w")

        self._section(body, "2. ENTIDADES A UTILIZAR", 2)
        entities = ttk.Frame(body, style="Soft.TFrame", padding=11)
        entities.grid(row=3, column=0, sticky="ew")
        ttk.Checkbutton(entities, text="Puntos de AutoCAD (POINT)", variable=self.include_points_var).pack(anchor="w")
        ttk.Checkbutton(entities, text="Bloques por punto de inserción (INSERT)", variable=self.include_inserts_var).pack(anchor="w", pady=3)
        ttk.Checkbutton(entities, text="Vértices de polilíneas (usar con cuidado)", variable=self.include_poly_vertices_var).pack(anchor="w")

        self._section(body, "3. FILTROS DEL TIN", 4)
        filters = ttk.Frame(body, style="Soft.TFrame", padding=11)
        filters.grid(row=5, column=0, sticky="ew")
        filters.columnconfigure(0, weight=1)
        filters.columnconfigure(1, weight=1)
        self._entry_field(filters, 0, 0, "Arista máxima (m, opcional)", self.max_edge_var)
        self._entry_field(filters, 0, 1, "Área mínima (m²)", self.min_area_var)
        self._entry_field(filters, 1, 0, "Decimales para duplicados XY", self.dedup_var)
        ttk.Checkbutton(filters, text="Conservar puntos en el DXF", variable=self.write_points_var).grid(row=1, column=1, sticky="sw", padx=5, pady=5)
        ttk.Label(
            filters,
            text="La arista máxima elimina puentes largos en huecos o límites irregulares. Déjala vacía si no deseas filtrar.",
            style="SoftHint.TLabel", wraplength=350, justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=5, pady=(7, 0))
        ttk.Button(body, text="Calcular triangulación TIN", style="Accent.TButton", command=self.start_tin_preview).grid(row=6, column=0, sticky="ew", pady=(12, 4))

    def _build_slope_tab(self):
        panel = ScrolledPanel(self.notebook)
        self.notebook.add(panel, text="TIN → Pendientes")
        body = panel.body
        body.columnconfigure(0, weight=1)
        self._section(body, "1. DXF CON TRIANGULACIÓN", 0)
        source = ttk.Frame(body, style="Soft.TFrame", padding=11)
        source.grid(row=1, column=0, sticky="ew")
        ttk.Label(source, textvariable=self.slope_file_var, style="SoftSection.TLabel", wraplength=365).pack(anchor="w")
        ttk.Label(source, textvariable=self.slope_summary_var, style="SoftHint.TLabel", wraplength=365, justify="left").pack(anchor="w", pady=(5, 8))
        buttons = ttk.Frame(source, style="Soft.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Seleccionar TIN DXF", style="Accent.TButton", command=self.choose_slope_file).pack(side="left")
        ttk.Button(buttons, text="Usar TIN calculado", style="Secondary.TButton", command=self.use_preview_tin).pack(side="left", padx=5)

        self._section(body, "2. CAPAS TRIANGULADAS", 2)
        layers = ttk.Frame(body, style="Soft.TFrame", padding=8)
        layers.grid(row=3, column=0, sticky="ew")
        layers.columnconfigure(0, weight=1)
        self.layer_tree = ttk.Treeview(layers, show="tree", selectmode="extended", height=4)
        self.layer_tree.grid(row=0, column=0, sticky="ew")
        layer_bar = ttk.Scrollbar(layers, orient="vertical", command=self.layer_tree.yview)
        layer_bar.grid(row=0, column=1, sticky="ns")
        self.layer_tree.configure(yscrollcommand=layer_bar.set)
        layer_buttons = ttk.Frame(layers, style="Soft.TFrame")
        layer_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(layer_buttons, text="Todas", style="Secondary.TButton", command=self.select_all_layers).pack(side="left")
        ttk.Button(layer_buttons, text="Solo TIN_3DFACE", style="Secondary.TButton", command=self.select_tin_layer).pack(side="left", padx=5)

        self._section(body, "3. RANGOS DE PENDIENTE", 4)
        range_card = ttk.Frame(body, style="Soft.TFrame", padding=8)
        range_card.grid(row=5, column=0, sticky="ew")
        range_card.columnconfigure(0, weight=1)
        headings = ttk.Frame(range_card, style="Soft.TFrame")
        headings.grid(row=0, column=0, sticky="ew")
        ttk.Label(headings, text="Color / descripción", style="SoftHint.TLabel").pack(side="left")
        ttk.Label(headings, text="Desde / hasta %", style="SoftHint.TLabel").pack(side="right")
        self.range_frame = ttk.Frame(range_card, style="Soft.TFrame")
        self.range_frame.grid(row=1, column=0, sticky="ew")
        self._set_range_rows(clone_default_ranges())
        range_buttons = ttk.Frame(range_card, style="Soft.TFrame")
        range_buttons.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        ttk.Button(range_buttons, text="+ Rango", style="Secondary.TButton", command=self.add_range).pack(side="left")
        ttk.Button(range_buttons, text="Restaurar", style="Secondary.TButton", command=lambda: self._set_range_rows(clone_default_ranges())).pack(side="left", padx=5)

        self._section(body, "4. SALIDA", 6)
        options = ttk.Frame(body, style="Soft.TFrame", padding=10)
        options.grid(row=7, column=0, sticky="ew")
        ttk.Checkbutton(options, text="Incluir superficie 3D coloreada", variable=self.slope_faces_var).pack(anchor="w")
        ttk.Checkbutton(options, text="Escribir porcentaje en cada triángulo", variable=self.slope_text_var).pack(anchor="w", pady=3)
        decimals = ttk.Frame(options, style="Soft.TFrame")
        decimals.pack(fill="x", pady=(5, 0))
        ttk.Label(decimals, text="Decimales de áreas", style="SoftHint.TLabel").pack(side="left")
        ttk.Spinbox(decimals, from_=0, to=6, textvariable=self.decimals_var, width=6).pack(side="right")
        ttk.Button(body, text="Calcular zonificación", style="Accent.TButton", command=self.start_slope_preview).grid(row=8, column=0, sticky="ew", pady=(12, 4))

    def _build_flow_tab(self):
        panel = ScrolledPanel(self.notebook)
        self.notebook.add(panel, text="Escurrimientos")
        body = panel.body
        body.columnconfigure(0, weight=1)
        self._section(body, "1. SUPERFICIE TIN", 0)
        source = ttk.Frame(body, style="Soft.TFrame", padding=11)
        source.grid(row=1, column=0, sticky="ew")
        ttk.Label(source, textvariable=self.flow_file_var, style="SoftSection.TLabel", wraplength=365).pack(anchor="w")
        ttk.Label(source, textvariable=self.flow_summary_var, style="SoftHint.TLabel", wraplength=365, justify="left").pack(anchor="w", pady=(5, 8))
        buttons = ttk.Frame(source, style="Soft.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Seleccionar TIN DXF", style="Accent.TButton", command=self.choose_flow_file).pack(side="left")
        ttk.Button(buttons, text="Usar TIN calculado", style="Secondary.TButton", command=self.use_preview_tin_for_flow).pack(side="left", padx=5)

        self._section(body, "2. CAPAS TRIANGULADAS", 2)
        layers = ttk.Frame(body, style="Soft.TFrame", padding=8)
        layers.grid(row=3, column=0, sticky="ew")
        layers.columnconfigure(0, weight=1)
        self.flow_layer_tree = ttk.Treeview(layers, show="tree", selectmode="extended", height=4)
        self.flow_layer_tree.grid(row=0, column=0, sticky="ew")
        layer_bar = ttk.Scrollbar(layers, orient="vertical", command=self.flow_layer_tree.yview)
        layer_bar.grid(row=0, column=1, sticky="ns")
        self.flow_layer_tree.configure(yscrollcommand=layer_bar.set)
        layer_buttons = ttk.Frame(layers, style="Soft.TFrame")
        layer_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(layer_buttons, text="Todas", style="Secondary.TButton", command=self.select_all_flow_layers).pack(side="left")
        ttk.Button(layer_buttons, text="Solo TIN_3DFACE", style="Secondary.TButton", command=self.select_flow_tin_layer).pack(side="left", padx=5)

        self._section(body, "3. GEOMETRÍA DE LAS FLECHAS", 4)
        geometry = ttk.Frame(body, style="Soft.TFrame", padding=10)
        geometry.grid(row=5, column=0, sticky="ew")
        geometry.columnconfigure(0, weight=1)
        geometry.columnconfigure(1, weight=1)
        self._entry_field(geometry, 0, 0, "Longitud base (m, vacío = auto)", self.flow_base_length_var)
        self._entry_field(geometry, 0, 1, "Tamaño de punta (%)", self.flow_head_size_var)
        self._entry_field(geometry, 1, 0, "Dibujar cada N triángulos", self.flow_density_var)
        self._entry_field(geometry, 1, 1, "Pendiente mínima (%)", self.flow_min_slope_var)
        ttk.Checkbutton(geometry, text="Incluir malla TIN de referencia", variable=self.flow_tin_reference_var).grid(row=2, column=0, columnspan=2, sticky="w", padx=5, pady=(6, 2))
        ttk.Checkbutton(geometry, text="Escribir pendiente junto a cada flecha", variable=self.flow_slope_text_var).grid(row=3, column=0, columnspan=2, sticky="w", padx=5)

        self._section(body, "4. RANGOS, COLORES Y TAMAÑOS", 6)
        range_card = ttk.Frame(body, style="Soft.TFrame", padding=8)
        range_card.grid(row=7, column=0, sticky="ew")
        range_card.columnconfigure(0, weight=1)
        ttk.Label(
            range_card,
            text="Cada rango controla color, longitud relativa y grosor de línea.",
            style="SoftHint.TLabel", wraplength=350, justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.flow_range_frame = ttk.Frame(range_card, style="Soft.TFrame")
        self.flow_range_frame.grid(row=1, column=0, sticky="ew")
        self._set_flow_range_rows(clone_default_flow_ranges())
        range_buttons = ttk.Frame(range_card, style="Soft.TFrame")
        range_buttons.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        ttk.Button(range_buttons, text="+ Rango", style="Secondary.TButton", command=self.add_flow_range).pack(side="left")
        ttk.Button(range_buttons, text="Restaurar", style="Secondary.TButton", command=lambda: self._set_flow_range_rows(clone_default_flow_ranges())).pack(side="left", padx=5)
        ttk.Button(body, text="Calcular flechas de escurrimiento", style="Accent.TButton", command=self.start_flow_preview).grid(row=8, column=0, sticky="ew", pady=(12, 4))

    @staticmethod
    def _entry_field(parent, row, column, label, variable):
        box = ttk.Frame(parent, style="Soft.TFrame")
        box.grid(row=row, column=column, sticky="ew", padx=5, pady=5)
        heading = ttk.Frame(box, style="Soft.TFrame")
        heading.pack(fill="x")
        label_widget = ttk.Label(heading, text=label, style="SoftHint.TLabel")
        label_widget.pack(side="left")
        explanation = TECHNICAL_HELP.get(label)
        if explanation:
            help_badge(heading, explanation).pack(side="left", padx=(3, 0))
            attach_tooltip(label_widget, explanation)
        entry = ttk.Entry(box, textvariable=variable)
        entry.pack(fill="x", pady=(2, 0))
        if explanation:
            attach_tooltip(entry, explanation)

    def _set_range_rows(self, ranges):
        for child in self.range_frame.winfo_children():
            child.destroy()
        self.range_rows = []
        for index, item in enumerate(ranges):
            self._append_range_row(item, index)

    def _append_range_row(self, item: SlopeRange, index: int):
        row = ttk.Frame(self.range_frame, style="Soft.TFrame")
        row.grid(row=index, column=0, sticky="ew", pady=2)
        row.columnconfigure(1, weight=1)
        color_var = StringVar(value=item.color_hex)
        name_var = StringVar(value=item.name)
        min_var = StringVar(value=f"{item.min_pct:g}")
        max_var = StringVar(value="" if item.max_pct is None else f"{item.max_pct:g}")
        swatch = Button(row, text="", width=2, height=1, background=item.color_hex, relief="flat", cursor="hand2")
        swatch.grid(row=0, column=0, padx=(0, 5))
        swatch.configure(command=lambda: self.choose_range_color(color_var, swatch))
        ttk.Entry(row, textvariable=name_var, width=17).grid(row=0, column=1, sticky="ew")
        ttk.Entry(row, textvariable=min_var, width=6).grid(row=0, column=2, padx=(5, 2))
        ttk.Entry(row, textvariable=max_var, width=6).grid(row=0, column=3, padx=2)
        ttk.Button(row, text="×", width=3, style="Secondary.TButton", command=lambda current=row: self.delete_range(current)).grid(row=0, column=4, padx=(4, 0))
        self.range_rows.append({"frame": row, "color": color_var, "name": name_var, "min": min_var, "max": max_var})

    def choose_range_color(self, variable, swatch):
        chosen = colorchooser.askcolor(color=variable.get(), title="Color del rango", parent=self)
        if chosen and chosen[1]:
            variable.set(chosen[1].upper())
            swatch.configure(background=chosen[1])

    def add_range(self):
        try:
            last = self.range_rows[-1]
            previous = float(last["min"].get() or 0)
            if last["max"].get().strip():
                previous = float(last["max"].get())
                last["max"].set(f"{previous + 10:g}")
            else:
                last["max"].set(f"{previous + 10:g}")
            self._append_range_row(SlopeRange("Nuevo rango", previous + 10, None, "#7653A6"), len(self.range_rows))
        except Exception:
            messagebox.showwarning("Rangos", "Corrige primero los valores del último rango.")

    def delete_range(self, frame):
        if len(self.range_rows) <= 2:
            messagebox.showwarning("Rangos", "Deben quedar al menos dos rangos.")
            return
        ranges = [item for item in self._ranges_from_form(validate=False) if item is not None]
        index = next((i for i, row in enumerate(self.range_rows) if row["frame"] is frame), -1)
        if index >= 0:
            ranges.pop(index)
            if ranges:
                ranges[-1].max_pct = None
            self._set_range_rows(ranges)

    def _ranges_from_form(self, validate=True):
        ranges = []
        for row in self.range_rows:
            try:
                minimum = float(row["min"].get().strip())
                maximum_text = row["max"].get().strip()
                ranges.append(SlopeRange(row["name"].get().strip() or "Sin nombre", minimum, None if not maximum_text else float(maximum_text), row["color"].get()))
            except ValueError:
                if validate:
                    raise ValueError("Todos los límites de pendiente deben ser números.")
                ranges.append(None)
        return ranges

    def _set_flow_range_rows(self, ranges):
        for child in self.flow_range_frame.winfo_children():
            child.destroy()
        self.flow_range_rows = []
        for index, item in enumerate(ranges):
            self._append_flow_range_row(item, index)

    def _append_flow_range_row(self, item: FlowRange, index: int):
        card = ttk.Frame(self.flow_range_frame, style="Card.TFrame", padding=6)
        card.grid(row=index, column=0, sticky="ew", pady=2)
        card.columnconfigure(1, weight=1)
        color_var = StringVar(value=item.color_hex)
        name_var = StringVar(value=item.name)
        min_var = StringVar(value=f"{item.min_pct:g}")
        max_var = StringVar(value="" if item.max_pct is None else f"{item.max_pct:g}")
        factor_var = StringVar(value=f"{item.length_factor:g}")
        weight_var = StringVar(value=f"{item.lineweight_mm:g}")
        swatch = Button(card, text="", width=2, height=1, background=item.color_hex, relief="flat", cursor="hand2")
        swatch.grid(row=0, column=0, rowspan=2, padx=(0, 6), sticky="ns")
        swatch.configure(command=lambda: self.choose_range_color(color_var, swatch))
        ttk.Entry(card, textvariable=name_var).grid(row=0, column=1, columnspan=4, sticky="ew")
        ttk.Button(card, text="×", width=3, style="Secondary.TButton", command=lambda current=card: self.delete_flow_range(current)).grid(row=0, column=5, padx=(5, 0))
        values = (("Desde", min_var, 5), ("Hasta", max_var, 5), ("Tamaño ×", factor_var, 5), ("Grosor mm", weight_var, 6))
        for column, (label, variable, width) in enumerate(values, 1):
            box = ttk.Frame(card, style="Card.TFrame")
            box.grid(row=1, column=column, sticky="ew", padx=(0, 4), pady=(5, 0))
            ttk.Label(box, text=label, style="Field.Card.TLabel").pack(anchor="w")
            ttk.Entry(box, textvariable=variable, width=width).pack(fill="x")
        self.flow_range_rows.append({
            "frame": card, "color": color_var, "name": name_var, "min": min_var,
            "max": max_var, "factor": factor_var, "weight": weight_var,
        })

    def _flow_ranges_from_form(self, validate=True):
        ranges = []
        for row in self.flow_range_rows:
            try:
                minimum = float(row["min"].get().strip())
                maximum_text = row["max"].get().strip()
                ranges.append(FlowRange(
                    row["name"].get().strip() or "Sin nombre", minimum,
                    None if not maximum_text else float(maximum_text), row["color"].get(),
                    float(row["factor"].get().strip()), float(row["weight"].get().strip()),
                ))
            except ValueError:
                if validate:
                    raise ValueError("Los límites, tamaños y grosores de las flechas deben ser números.")
                ranges.append(None)
        return ranges

    def add_flow_range(self):
        try:
            ranges = self._flow_ranges_from_form()
            last = ranges[-1]
            new_minimum = last.min_pct + 10 if last.max_pct is None else last.max_pct
            last.max_pct = new_minimum
            ranges.append(FlowRange("Nuevo escurrimiento", new_minimum, None, "#7653A6", 1.0, 0.25))
            self._set_flow_range_rows(ranges)
        except Exception:
            messagebox.showwarning("Rangos", "Corrige primero los valores del último rango.")

    def delete_flow_range(self, frame):
        if len(self.flow_range_rows) <= 2:
            messagebox.showwarning("Rangos", "Deben quedar al menos dos rangos.")
            return
        ranges = [item for item in self._flow_ranges_from_form(validate=False) if item is not None]
        index = next((i for i, row in enumerate(self.flow_range_rows) if row["frame"] is frame), -1)
        if index >= 0:
            ranges.pop(index)
            ranges[-1].max_pct = None
            self._set_flow_range_rows(ranges)

    def _build_preview(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        header = ttk.Frame(parent, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="VISTA PREVIA EN PLANTA", style="Section.TLabel").pack(side="left")
        preview_toggle = ttk.Checkbutton(
            header, text="Dibujar vista previa", variable=self.draw_preview_var,
            command=self._preview_enabled_changed,
        )
        preview_toggle.pack(side="left", padx=(14, 0))
        attach_tooltip(preview_toggle, "Desactívala antes de cargar un DXF grande. El cálculo y la exportación continúan, pero Tkinter no dibuja miles de entidades.")
        ttk.Button(header, text="Ajustar", style="Secondary.TButton", command=self.fit_preview).pack(side="right")
        ttk.Button(header, text="+", width=3, style="Secondary.TButton", command=lambda: self.zoom_preview(0.8)).pack(side="right", padx=4)
        ttk.Button(header, text="−", width=3, style="Secondary.TButton", command=lambda: self.zoom_preview(1.25)).pack(side="right")
        self.preview_info = ttk.Label(parent, text="Carga un archivo para revisar la geometría antes de exportar.", style="Hint.Card.TLabel")
        self.preview_info.grid(row=1, column=0, sticky="w", pady=(7, 6))
        self.preview = Canvas(
            parent, background="#132331", highlightthickness=1, highlightbackground="#8FA3B0",
            cursor="fleur" if self.draw_preview_var.get() else "",
        )
        self.preview.grid(row=2, column=0, sticky="nsew")
        self.preview.bind("<Configure>", self._preview_resized)
        self.preview.bind("<MouseWheel>", self._preview_wheel)
        self.preview.bind("<ButtonPress-1>", self._preview_press)
        self.preview.bind("<B1-Motion>", self._preview_motion)
        self.preview.bind("<ButtonRelease-1>", self._preview_release)
        ttk.Label(parent, text="Rueda: zoom · Arrastra: mover · Puedes desactivar el dibujo sin afectar el DXF exportado.", style="Hint.Card.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))

    def _preview_enabled_changed(self):
        enabled = bool(self.draw_preview_var.get())
        SETTINGS.set("triangulation.draw_preview", enabled)
        self._view_bounds = None
        self.preview.configure(cursor="fleur" if enabled else "")
        self.redraw_preview()
        self.status_var.set("Vista previa activada" if enabled else "Vista previa desactivada · cálculo y exportación siguen disponibles")

    def choose_tin_file(self):
        chosen = filedialog.askopenfilename(title="DXF con puntos", filetypes=(("AutoCAD DXF", "*.dxf"),))
        if chosen:
            self.tin_path = Path(chosen)
            self.tin_file_var.set(self.tin_path.name)
            self.start_tin_preview()

    def _tin_options(self):
        if not self.include_points_var.get() and not self.include_inserts_var.get() and not self.include_poly_vertices_var.get():
            raise ValueError("Activa al menos un tipo de entidad para obtener puntos.")
        max_text = self.max_edge_var.get().strip().replace(",", ".")
        max_edge = None if not max_text else float(max_text)
        min_area = float(self.min_area_var.get().strip().replace(",", "."))
        decimals = int(self.dedup_var.get())
        if max_edge is not None and max_edge <= 0:
            raise ValueError("La arista máxima debe ser mayor que cero.")
        if min_area < 0 or not 0 <= decimals <= 12:
            raise ValueError("Revisa el área mínima y los decimales de duplicados.")
        return max_edge, min_area, decimals

    def start_tin_preview(self):
        if not self.tin_path:
            messagebox.showinfo("DXF requerido", "Selecciona primero un DXF con puntos.")
            return
        try:
            max_edge, min_area, decimals = self._tin_options()
        except Exception as exc:
            messagebox.showwarning("Opciones inválidas", str(exc))
            return
        self._remember_applied_configuration("tin")
        source = self.tin_path
        options = (bool(self.include_points_var.get()), bool(self.include_inserts_var.get()), bool(self.include_poly_vertices_var.get()), decimals, max_edge, min_area)
        token = self._begin_work("Generando triangulación TIN…")

        def worker():
            try:
                model = create_tin_model(source, *options, progress=self._progress_callback(token))
                self._results.put(("tin", token, model, None))
            except Exception as exc:
                self._results.put(("tin", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def choose_slope_file(self):
        chosen = filedialog.askopenfilename(title="DXF con triangulación", filetypes=(("AutoCAD DXF", "*.dxf"),))
        if chosen:
            self.slope_path = Path(chosen)
            self.slope_file_var.set(self.slope_path.name)
            self._load_slope_source()

    def _load_slope_source(self):
        source = self.slope_path
        if not source:
            return
        try:
            ranges = self._ranges_from_form()
        except Exception as exc:
            messagebox.showwarning("Rangos inválidos", str(exc))
            return
        self._remember_applied_configuration("slope")
        token = self._begin_work("Leyendo capas y calculando pendientes…")

        def worker():
            try:
                layers = triangle_layers(source)
                selected = ["TIN_3DFACE"] if "TIN_3DFACE" in layers else layers
                triangles = extract_triangles(source, selected, progress=self._progress_callback(token, 0.0, 0.48))
                analysis = analyze_slopes(triangles, ranges, source.name, progress=self._progress_callback(token, 0.48, 0.52))
                self._results.put(("slope_load", token, (layers, selected, analysis), None))
            except Exception as exc:
                self._results.put(("slope_load", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def start_slope_preview(self):
        if not self.slope_path:
            if self.tin_model:
                self.use_preview_tin()
            else:
                messagebox.showinfo("TIN requerido", "Selecciona un DXF triangulado o calcula primero el TIN.")
            return
        selected_layers = [self.layer_tree.item(iid, "text") for iid in self.layer_tree.selection()]
        if not selected_layers:
            messagebox.showwarning("Capas", "Selecciona al menos una capa triangulada.")
            return
        try:
            ranges = self._ranges_from_form()
        except Exception as exc:
            messagebox.showwarning("Rangos inválidos", str(exc))
            return
        self._remember_applied_configuration("slope")
        source = self.slope_path
        token = self._begin_work("Recalculando zonificación…")

        def worker():
            try:
                triangles = extract_triangles(source, selected_layers, progress=self._progress_callback(token, 0.0, 0.48))
                analysis = analyze_slopes(triangles, ranges, source.name, progress=self._progress_callback(token, 0.48, 0.52))
                self._results.put(("slope", token, analysis, None))
            except Exception as exc:
                self._results.put(("slope", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def use_preview_tin(self):
        if not self.tin_model:
            messagebox.showinfo("Sin TIN", "Primero calcula la triangulación en la pestaña Puntos → TIN.")
            return
        try:
            ranges = self._ranges_from_form()
        except Exception as exc:
            messagebox.showwarning("Rangos inválidos", str(exc))
            return
        self._remember_applied_configuration("slope")
        model = self.tin_model
        token = self._begin_work("Calculando pendientes del TIN en memoria…")

        def worker():
            try:
                analysis = analyze_slopes(model.triangles, ranges, model.source_name, progress=self._progress_callback(token))
                self._results.put(("slope_memory", token, analysis, None))
            except Exception as exc:
                self._results.put(("slope_memory", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def choose_flow_file(self):
        chosen = filedialog.askopenfilename(title="DXF con superficie TIN", filetypes=(("AutoCAD DXF", "*.dxf"),))
        if chosen:
            self.flow_path = Path(chosen)
            self.flow_file_var.set(self.flow_path.name)
            self._load_flow_source()

    def _flow_options(self):
        ranges = self._flow_ranges_from_form()
        length_text = self.flow_base_length_var.get().strip().replace(",", ".")
        base_length = None if not length_text else float(length_text)
        head_ratio = float(self.flow_head_size_var.get().strip().replace(",", ".")) / 100.0
        density = int(self.flow_density_var.get().strip())
        minimum_slope = float(self.flow_min_slope_var.get().strip().replace(",", "."))
        if base_length is not None and base_length <= 0:
            raise ValueError("La longitud base debe ser mayor que cero.")
        if density < 1:
            raise ValueError("La densidad debe ser 1 o mayor.")
        return ranges, base_length, head_ratio, density, minimum_slope

    def _load_flow_source(self):
        source = self.flow_path
        if not source:
            return
        try:
            options = self._flow_options()
        except Exception as exc:
            messagebox.showwarning("Configuración inválida", str(exc))
            return
        self._remember_applied_configuration("flow")
        token = self._begin_work("Leyendo TIN y calculando escurrimientos…")

        def worker():
            try:
                layers = triangle_layers(source)
                selected = ["TIN_3DFACE"] if "TIN_3DFACE" in layers else layers
                triangles = extract_triangles(source, selected, progress=self._progress_callback(token, 0.0, 0.48))
                ranges, base_length, head_ratio, density, minimum_slope = options
                analysis = analyze_flow(triangles, ranges, source.name, base_length, head_ratio, density, minimum_slope, self._progress_callback(token, 0.48, 0.52))
                self._results.put(("flow_load", token, (layers, selected, analysis), None))
            except Exception as exc:
                self._results.put(("flow_load", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def start_flow_preview(self):
        if not self.flow_path:
            if self.tin_model:
                self.use_preview_tin_for_flow()
            else:
                messagebox.showinfo("TIN requerido", "Selecciona un DXF triangulado o calcula primero el TIN.")
            return
        selected_layers = [self.flow_layer_tree.item(iid, "text") for iid in self.flow_layer_tree.selection()]
        if not selected_layers:
            messagebox.showwarning("Capas", "Selecciona al menos una capa triangulada.")
            return
        try:
            options = self._flow_options()
        except Exception as exc:
            messagebox.showwarning("Configuración inválida", str(exc))
            return
        self._remember_applied_configuration("flow")
        source = self.flow_path
        token = self._begin_work("Recalculando flechas de escurrimiento…")

        def worker():
            try:
                triangles = extract_triangles(source, selected_layers, progress=self._progress_callback(token, 0.0, 0.48))
                ranges, base_length, head_ratio, density, minimum_slope = options
                analysis = analyze_flow(triangles, ranges, source.name, base_length, head_ratio, density, minimum_slope, self._progress_callback(token, 0.48, 0.52))
                self._results.put(("flow", token, analysis, None))
            except Exception as exc:
                self._results.put(("flow", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def use_preview_tin_for_flow(self):
        triangles = self.tin_model.triangles if self.tin_model else self.slope_analysis.triangles if self.slope_analysis else None
        source_name = self.tin_model.source_name if self.tin_model else self.slope_analysis.source_name if self.slope_analysis else "TIN"
        if not triangles:
            messagebox.showinfo("Sin TIN", "Primero calcula un TIN o una zonificación de pendientes.")
            return
        try:
            options = self._flow_options()
        except Exception as exc:
            messagebox.showwarning("Configuración inválida", str(exc))
            return
        self._remember_applied_configuration("flow")
        token = self._begin_work("Calculando escurrimientos del TIN en memoria…")

        def worker():
            try:
                ranges, base_length, head_ratio, density, minimum_slope = options
                analysis = analyze_flow(triangles, ranges, source_name, base_length, head_ratio, density, minimum_slope, self._progress_callback(token))
                self._results.put(("flow_memory", token, analysis, None))
            except Exception as exc:
                self._results.put(("flow_memory", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _begin_work(self, status):
        self._token += 1
        self._working = True
        self.status_var.set(status)
        self.progress_strip.show(0, status)
        self.export_button.state(["disabled"])
        return self._token

    def _progress_callback(self, token, start=0.0, span=1.0):
        def notify(fraction, message):
            self._results.put(("progress", token, (start + span * fraction, message), None))
        return notify

    def _poll_results(self):
        try:
            while True:
                kind, token, result, error = self._results.get_nowait()
                if kind == "progress":
                    if token == self._token:
                        fraction, message = result
                        self.progress_strip.update_progress(fraction * 100, message)
                        self.status_var.set(message)
                elif kind == "export":
                    self._finish_export(result, error)
                elif token == self._token:
                    self._finish_preview_result(kind, result, error)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll_results)

    def _finish_preview_result(self, kind, result, error):
        self._working = False
        if error:
            self.progress_strip.hide()
            self.status_var.set("No se pudo completar el cálculo")
            messagebox.showerror("Error de triangulación", str(error))
            return
        if kind == "tin":
            self.tin_model = result
            stats = result.stats
            self.tin_summary_var.set(
                f"{len(result.points):,} puntos únicos · {len(result.triangles):,} triángulos · "
                f"{stats.duplicates:,} duplicados · {stats.filtered_edge + stats.filtered_area:,} filtrados"
            )
            self.status_var.set("TIN listo para revisar y exportar")
            self._preview_kind = "tin"
        elif kind == "slope_load":
            layers, selected, analysis = result
            self._populate_layers(layers, selected)
            self.slope_analysis = analysis
            self.slope_summary_var.set(f"{len(analysis.triangles):,} triángulos · {analysis.total_area:,.2f} m² · {len(analysis.ranges)} rangos")
            self.status_var.set("Zonificación lista para revisar y exportar")
            self._preview_kind = "slope"
        elif kind in {"slope", "slope_memory"}:
            self.slope_analysis = result
            if kind == "slope_memory":
                self.slope_path = None
                self.slope_file_var.set(f"TIN en memoria · {result.source_name}")
                self._populate_layers([], [])
            self.slope_summary_var.set(f"{len(result.triangles):,} triángulos · {result.total_area:,.2f} m² · {len(result.ranges)} rangos")
            self.status_var.set("Zonificación lista para revisar y exportar")
            self._preview_kind = "slope"
        elif kind == "flow_load":
            layers, selected, analysis = result
            self._populate_flow_layers(layers, selected)
            self.flow_analysis = analysis
            self.flow_summary_var.set(
                f"{len(analysis.arrows):,} flechas · longitud base {analysis.base_length:.2f} m · "
                f"cada {analysis.density} triángulo(s)"
            )
            self.status_var.set("Escurrimientos listos para revisar y exportar")
            self._preview_kind = "flow"
        else:
            self.flow_analysis = result
            if kind == "flow_memory":
                self.flow_path = None
                self.flow_file_var.set(f"TIN en memoria · {result.source_name}")
                self._populate_flow_layers([], [])
            self.flow_summary_var.set(
                f"{len(result.arrows):,} flechas · longitud base {result.base_length:.2f} m · "
                f"cada {result.density} triángulo(s)"
            )
            self.status_var.set("Escurrimientos listos para revisar y exportar")
            self._preview_kind = "flow"
        self.export_button.state(["!disabled"])
        self.progress_strip.finish("Vista previa lista" if self.draw_preview_var.get() else "Cálculo listo")
        self._view_bounds = None
        self.redraw_preview()

    def _populate_layers(self, layers, selected):
        self.layer_tree.delete(*self.layer_tree.get_children())
        selected_iids = []
        for index, layer in enumerate(layers):
            iid = str(index)
            self.layer_tree.insert("", "end", iid=iid, text=layer)
            if layer in selected:
                selected_iids.append(iid)
        if selected_iids:
            self.layer_tree.selection_set(selected_iids)

    def select_all_layers(self):
        self.layer_tree.selection_set(self.layer_tree.get_children())

    def select_tin_layer(self):
        matches = [iid for iid in self.layer_tree.get_children() if self.layer_tree.item(iid, "text") == "TIN_3DFACE"]
        if matches:
            self.layer_tree.selection_set(matches)

    def _populate_flow_layers(self, layers, selected):
        self.flow_layer_tree.delete(*self.flow_layer_tree.get_children())
        selected_iids = []
        for index, layer in enumerate(layers):
            iid = str(index)
            self.flow_layer_tree.insert("", "end", iid=iid, text=layer)
            if layer in selected:
                selected_iids.append(iid)
        if selected_iids:
            self.flow_layer_tree.selection_set(selected_iids)

    def select_all_flow_layers(self):
        self.flow_layer_tree.selection_set(self.flow_layer_tree.get_children())

    def select_flow_tin_layer(self):
        matches = [iid for iid in self.flow_layer_tree.get_children() if self.flow_layer_tree.item(iid, "text") == "TIN_3DFACE"]
        if matches:
            self.flow_layer_tree.selection_set(matches)

    def _tab_changed(self, _event=None):
        index = self.notebook.index("current")
        self._preview_kind = ("tin", "slope", "flow")[index]
        self._view_bounds = None
        current = {"tin": self.tin_model, "slope": self.slope_analysis, "flow": self.flow_analysis}[self._preview_kind]
        self.export_button.state(["!disabled"] if current and not self._working else ["disabled"])
        self.redraw_preview()

    def _data_bounds(self):
        model = {"tin": self.tin_model, "slope": self.slope_analysis, "flow": self.flow_analysis}[self._preview_kind]
        return model.bounds() if model else None

    def fit_preview(self):
        if not self.draw_preview_var.get():
            return
        self._view_bounds = None
        self.redraw_preview()

    def _fitted_bounds(self, bounds):
        min_x, min_y, max_x, max_y = bounds
        width = max(max_x - min_x, 1e-6)
        height = max(max_y - min_y, 1e-6)
        margin = 0.06
        min_x -= width * margin
        max_x += width * margin
        min_y -= height * margin
        max_y += height * margin
        canvas_ratio = max(self.preview.winfo_width(), 1) / max(self.preview.winfo_height(), 1)
        data_ratio = (max_x - min_x) / (max_y - min_y)
        if data_ratio > canvas_ratio:
            desired = (max_x - min_x) / canvas_ratio
            extra = desired - (max_y - min_y)
            min_y -= extra / 2
            max_y += extra / 2
        else:
            desired = (max_y - min_y) * canvas_ratio
            extra = desired - (max_x - min_x)
            min_x -= extra / 2
            max_x += extra / 2
        return min_x, min_y, max_x, max_y

    def _screen_point(self, point):
        min_x, min_y, max_x, max_y = self._view_bounds
        width = max(self.preview.winfo_width(), 1)
        height = max(self.preview.winfo_height(), 1)
        return (point[0] - min_x) / (max_x - min_x) * width, height - (point[1] - min_y) / (max_y - min_y) * height

    def redraw_preview(self):
        self.preview.delete("all")
        if not self.draw_preview_var.get():
            self.preview_info.configure(text="Vista previa desactivada para ahorrar memoria y mantener fluida la aplicación.")
            self.preview.create_text(
                max(self.preview.winfo_width(), 300) / 2, max(self.preview.winfo_height(), 300) / 2,
                text="VISTA PREVIA DESACTIVADA\nEl cálculo y la exportación siguen funcionando",
                justify="center", fill="#91A9B8", font=("Segoe UI", 13),
            )
            return
        model = {"tin": self.tin_model, "slope": self.slope_analysis, "flow": self.flow_analysis}[self._preview_kind]
        if not model:
            self.preview.create_text(
                max(self.preview.winfo_width(), 300) / 2, max(self.preview.winfo_height(), 300) / 2,
                text="Selecciona un DXF y genera la vista previa", fill="#91A9B8", font=("Segoe UI", 13),
            )
            return
        if self._view_bounds is None:
            self._view_bounds = self._fitted_bounds(model.bounds())
        triangles = model.triangles
        step = max(1, math.ceil(len(triangles) / 3500))
        if self._preview_kind == "tin":
            elevations = [sum(point[2] for point in triangle) / 3 for triangle in triangles]
            min_z, max_z = min(elevations), max(elevations)
            colors = []
            for index in range(len(triangles)):
                ratio = 0.5 if max_z == min_z else (elevations[index] - min_z) / (max_z - min_z)
                colors.append(self._elevation_color(ratio))
            self._draw_surface_raster(triangles, colors, model.points)
            self.preview_info.configure(text=f"{len(model.points):,} puntos · {len(triangles):,} triángulos · elevación {min(point[2] for point in model.points):,.3f} a {max(point[2] for point in model.points):,.3f} m")
        elif self._preview_kind == "slope":
            colors = [model.ranges[model.assignments[index]].color_hex for index in range(len(triangles))]
            self._draw_surface_raster(triangles, colors)
            self._draw_slope_legend(model)
            self.preview_info.configure(text=f"{len(triangles):,} triángulos · área 2D {model.total_area:,.2f} m² · rellenos agrupados por rango")
        else:
            for index in range(0, len(triangles), step):
                coords = [value for point in triangles[index] for value in self._screen_point(point)]
                self.preview.create_polygon(coords, fill="#1B3241", outline="#496171", width=1, tags=("geometry",))
            arrow_step = max(1, math.ceil(len(model.arrows) / 4000))
            for arrow in model.arrows[::arrow_step]:
                item = model.ranges[arrow.range_index]
                start = self._screen_point(arrow.start)
                tip = self._screen_point(arrow.tip)
                left = self._screen_point(arrow.head_left)
                right = self._screen_point(arrow.head_right)
                width = max(1, min(5, round(item.lineweight_mm * 7)))
                self.preview.create_line(*start, *tip, fill=item.color_hex, width=width, tags=("geometry",))
                self.preview.create_polygon((*tip, *left, *right), fill=item.color_hex, outline=item.color_hex, tags=("geometry",))
            self._draw_flow_legend(model)
            self.preview_info.configure(
                text=f"{len(model.arrows):,} flechas · longitud base {model.base_length:.2f} m · "
                f"pendiente mínima {model.minimum_slope:g}% · dirección de máxima bajada"
            )
        if self._preview_kind == "flow" and step > 1:
            self.preview.create_text(12, self.preview.winfo_height() - 12, anchor="sw", text=f"Vista optimizada: 1 de cada {step} triángulos", fill="#AEC0CB", font=("Segoe UI", 8))

    def _draw_surface_raster(self, triangles, colors, points=None):
        """Render a complete dense surface as one Tk image.

        Skipping every Nth triangle creates holes because adjacent Delaunay
        faces are independent. Pillow can rasterize every face much more
        cheaply than creating tens of thousands of Canvas polygon objects.
        """
        width = max(2, self.preview.winfo_width())
        height = max(2, self.preview.winfo_height())
        image = Image.new("RGB", (width, height), "#132331")
        draw = ImageDraw.Draw(image)
        # Keep the mesh legible for normal surveys. Above this threshold the
        # fill remains complete but edges are omitted to protect interaction.
        outline = "#27495C" if len(triangles) <= 120_000 else None
        for triangle, fill in zip(triangles, colors):
            coordinates = [self._screen_point(point) for point in triangle]
            draw.polygon(coordinates, fill=fill, outline=outline)
        if points:
            point_step = max(1, math.ceil(len(points) / 2000))
            for point in points[::point_step]:
                x, y = self._screen_point(point)
                draw.ellipse((x - 1.0, y - 1.0, x + 1.0, y + 1.0), fill="#FF6B55")
        self._preview_photo = ImageTk.PhotoImage(image)
        self.preview.create_image(0, 0, image=self._preview_photo, anchor="nw", tags=("geometry",))

    @staticmethod
    def _elevation_color(ratio):
        ratio = max(0.0, min(1.0, ratio))
        stops = ((18, 90, 125), (25, 160, 170), (138, 190, 120), (244, 190, 75), (205, 72, 58))
        position = ratio * (len(stops) - 1)
        index = min(len(stops) - 2, int(position))
        fraction = position - index
        color = tuple(round(stops[index][channel] * (1 - fraction) + stops[index + 1][channel] * fraction) for channel in range(3))
        return "#%02X%02X%02X" % color

    def _draw_slope_legend(self, analysis):
        x, y = 14, 14
        total = analysis.total_area
        height = 26 + len(analysis.ranges) * 22
        self.preview.create_rectangle(x, y, x + 250, y + height, fill="#F7FAFB", outline="#8FA3B0")
        self.preview.create_text(x + 10, y + 10, anchor="nw", text="PENDIENTES", fill="#173B5F", font=("Segoe UI Semibold", 10))
        for index, item in enumerate(analysis.ranges):
            row_y = y + 30 + index * 22
            percentage = item.area_2d / total * 100 if total else 0
            self.preview.create_rectangle(x + 10, row_y, x + 27, row_y + 13, fill=item.color_hex, outline="")
            self.preview.create_text(x + 35, row_y + 7, anchor="w", text=f"{item.label()} · {percentage:.1f}%", fill="#263746", font=("Segoe UI", 8))

    def _draw_flow_legend(self, analysis):
        x, y = 14, 14
        height = 28 + len(analysis.ranges) * 24
        self.preview.create_rectangle(x, y, x + 300, y + height, fill="#F7FAFB", outline="#8FA3B0")
        self.preview.create_text(x + 10, y + 9, anchor="nw", text="ESCURRIMIENTOS", fill="#173B5F", font=("Segoe UI Semibold", 10))
        for index, item in enumerate(analysis.ranges):
            row_y = y + 31 + index * 24
            self.preview.create_line(x + 12, row_y + 7, x + 32, row_y + 7, fill=item.color_hex, width=max(2, round(item.lineweight_mm * 7)))
            self.preview.create_polygon(x + 32, row_y + 7, x + 24, row_y + 2, x + 24, row_y + 12, fill=item.color_hex, outline="")
            self.preview.create_text(
                x + 42, row_y + 7, anchor="w",
                text=f"{item.label()} · x{item.length_factor:g} · {item.lineweight_mm:g} mm · {item.count}",
                fill="#263746", font=("Segoe UI", 8),
            )

    def zoom_preview(self, factor, center=None):
        if not self.draw_preview_var.get():
            return
        if not self._view_bounds:
            return
        min_x, min_y, max_x, max_y = self._view_bounds
        center_x = (min_x + max_x) / 2 if center is None else center[0]
        center_y = (min_y + max_y) / 2 if center is None else center[1]
        width = (max_x - min_x) * factor
        height = (max_y - min_y) * factor
        self._view_bounds = center_x - width / 2, center_y - height / 2, center_x + width / 2, center_y + height / 2
        self.redraw_preview()

    def _preview_wheel(self, event):
        if not self.draw_preview_var.get():
            return "break"
        if not self._view_bounds:
            return "break"
        min_x, min_y, max_x, max_y = self._view_bounds
        world_x = min_x + event.x / max(self.preview.winfo_width(), 1) * (max_x - min_x)
        world_y = max_y - event.y / max(self.preview.winfo_height(), 1) * (max_y - min_y)
        self.zoom_preview(0.82 if event.delta > 0 else 1.22, (world_x, world_y))
        return "break"

    def _preview_press(self, event):
        if not self.draw_preview_var.get():
            return
        self._drag_start = self._drag_last = (event.x, event.y)

    def _preview_motion(self, event):
        if not self.draw_preview_var.get():
            return
        if not self._drag_last:
            return
        dx, dy = event.x - self._drag_last[0], event.y - self._drag_last[1]
        self._drag_last = (event.x, event.y)
        self.preview.move("geometry", dx, dy)

    def _preview_release(self, event):
        if not self.draw_preview_var.get():
            return
        if not self._drag_start or not self._view_bounds:
            return
        dx, dy = event.x - self._drag_start[0], event.y - self._drag_start[1]
        self._drag_start = self._drag_last = None
        min_x, min_y, max_x, max_y = self._view_bounds
        shift_x = -dx / max(self.preview.winfo_width(), 1) * (max_x - min_x)
        shift_y = dy / max(self.preview.winfo_height(), 1) * (max_y - min_y)
        self._view_bounds = min_x + shift_x, min_y + shift_y, max_x + shift_x, max_y + shift_y
        self.redraw_preview()

    def _preview_resized(self, _event=None):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(160, self._finish_resize)

    def _finish_resize(self):
        self._resize_job = None
        self.redraw_preview()

    def _configuration_snapshot(self, kind):
        if kind == "tin":
            return {
                "points": bool(self.include_points_var.get()), "inserts": bool(self.include_inserts_var.get()),
                "vertices": bool(self.include_poly_vertices_var.get()), "max_edge": self.max_edge_var.get(),
                "min_area": self.min_area_var.get(), "dedup": self.dedup_var.get(),
                "write_points": bool(self.write_points_var.get()),
            }
        if kind == "slope":
            return {
                "ranges": self._ranges_from_form(), "text": bool(self.slope_text_var.get()),
                "faces": bool(self.slope_faces_var.get()), "decimals": self.decimals_var.get(),
            }
        return {
            "ranges": self._flow_ranges_from_form(), "length": self.flow_base_length_var.get(),
            "head": self.flow_head_size_var.get(), "density": self.flow_density_var.get(),
            "minimum": self.flow_min_slope_var.get(), "reference": bool(self.flow_tin_reference_var.get()),
            "text": bool(self.flow_slope_text_var.get()),
        }

    def _apply_configuration(self, kind, snapshot):
        if kind == "tin":
            self.include_points_var.set(snapshot["points"])
            self.include_inserts_var.set(snapshot["inserts"])
            self.include_poly_vertices_var.set(snapshot["vertices"])
            self.max_edge_var.set(snapshot["max_edge"])
            self.min_area_var.set(snapshot["min_area"])
            self.dedup_var.set(snapshot["dedup"])
            self.write_points_var.set(snapshot["write_points"])
        elif kind == "slope":
            self._set_range_rows(snapshot["ranges"])
            self.slope_text_var.set(snapshot["text"])
            self.slope_faces_var.set(snapshot["faces"])
            self.decimals_var.set(snapshot["decimals"])
        else:
            self._set_flow_range_rows(snapshot["ranges"])
            self.flow_base_length_var.set(snapshot["length"])
            self.flow_head_size_var.set(snapshot["head"])
            self.flow_density_var.set(snapshot["density"])
            self.flow_min_slope_var.set(snapshot["minimum"])
            self.flow_tin_reference_var.set(snapshot["reference"])
            self.flow_slope_text_var.set(snapshot["text"])

    def _remember_applied_configuration(self, kind):
        current = self._configuration_snapshot(kind)
        previous = self._last_applied.get(kind)
        if previous is not None and previous != current:
            self._history[kind].append(previous)
            self._history[kind] = self._history[kind][-20:]
        self._last_applied[kind] = current

    def undo_configuration(self):
        kind = ("tin", "slope", "flow")[self.notebook.index("current")]
        if not self._history[kind]:
            self.status_var.set("No hay una configuración anterior para deshacer")
            return
        snapshot = self._history[kind].pop()
        self._apply_configuration(kind, snapshot)
        self._last_applied[kind] = snapshot
        self.status_var.set("Configuración anterior recuperada · recalculando")
        {"tin": self.start_tin_preview, "slope": self.start_slope_preview, "flow": self.start_flow_preview}[kind]()

    def reset_configuration(self):
        kind = ("tin", "slope", "flow")[self.notebook.index("current")]
        try:
            current = self._configuration_snapshot(kind)
            self._history[kind].append(current)
        except Exception:
            pass
        if kind == "tin":
            snapshot = {"points": True, "inserts": True, "vertices": False, "max_edge": "", "min_area": "0.000001", "dedup": "6", "write_points": True}
        elif kind == "slope":
            snapshot = {"ranges": clone_default_ranges(), "text": False, "faces": True, "decimals": "2"}
        else:
            snapshot = {"ranges": clone_default_flow_ranges(), "length": "", "head": "28", "density": "1", "minimum": "0.10", "reference": True, "text": False}
        self._apply_configuration(kind, snapshot)
        self._last_applied[kind] = snapshot
        self.status_var.set("Parámetros restablecidos · recalculando")
        {"tin": self.start_tin_preview, "slope": self.start_slope_preview, "flow": self.start_flow_preview}[kind]()

    def export_current(self):
        kind = ("tin", "slope", "flow")[self.notebook.index("current")]
        model = {"tin": self.tin_model, "slope": self.slope_analysis, "flow": self.flow_analysis}[kind]
        if not model:
            messagebox.showinfo("Sin resultado", "Calcula primero el resultado que deseas exportar.")
            return
        source_stem = Path(model.source_name).stem
        suffix = {"tin": "TIN", "slope": "ZONIFICACION_PENDIENTES", "flow": "ESCURRIMIENTOS"}[kind]
        initial = f"{source_stem}_{suffix}.dxf"
        chosen = filedialog.asksaveasfilename(title="Exportar triangulación DXF", defaultextension=".dxf", initialfile=initial, filetypes=(("AutoCAD DXF", "*.dxf"),))
        if not chosen:
            return
        target = Path(chosen)
        write_points = bool(self.write_points_var.get())
        slope_text = bool(self.slope_text_var.get())
        slope_faces = bool(self.slope_faces_var.get())
        flow_tin_reference = bool(self.flow_tin_reference_var.get())
        flow_slope_text = bool(self.flow_slope_text_var.get())
        try:
            decimals = int(self.decimals_var.get())
        except ValueError:
            decimals = 2
        token = self._begin_work(f"Exportando {target.name}…")

        def worker():
            try:
                if kind == "tin":
                    result = write_tin_dxf(model, target, write_points, self._progress_callback(token))
                elif kind == "slope":
                    result = write_slope_dxf(model, target, slope_text, slope_faces, decimals, self._progress_callback(token))
                else:
                    result = write_flow_dxf(model, target, flow_tin_reference, flow_slope_text, self._progress_callback(token))
                self._results.put(("export", token, result, None))
            except Exception as exc:
                self._results.put(("export", token, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_export(self, result, error):
        self._working = False
        self.export_button.state(["!disabled"])
        if error:
            self.progress_strip.hide()
            self.status_var.set("No se pudo exportar el DXF")
            messagebox.showerror("Error al exportar", str(error))
            return
        self.status_var.set(f"DXF creado: {result.name}")
        self.progress_strip.finish("Exportación terminada")
        if messagebox.askyesno("Exportación terminada", f"Se creó correctamente:\n\n{result}\n\n¿Deseas abrir su carpeta?"):
            os.startfile(str(result.parent))
