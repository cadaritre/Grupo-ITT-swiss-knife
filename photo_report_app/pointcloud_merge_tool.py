from __future__ import annotations

import json
import importlib.util
import math
import multiprocessing
import os
import queue
import threading
from pathlib import Path
from tkinter import Canvas, DoubleVar, StringVar, Text, Toplevel, filedialog, messagebox
from tkinter import ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

from .app_storage import category_dir
from .pointcloud_registration import (
    UNIT_FACTORS,
    RegistrationError,
    RegistrationResult,
    inspect_cloud,
    run_merge_worker,
    sample_cloud_visual,
    solve_rigid_registration,
)
from .pointcloud_advanced_viewer import run_advanced_viewer


FILE_TYPES = [
    ("Nubes compatibles", "*.las *.laz *.e57 *.xyz *.pts *.txt *.csv"),
    ("LAS / LAZ", "*.las *.laz"),
    ("E57", "*.e57"),
    ("Texto XYZ / PTS", "*.xyz *.pts *.txt *.csv"),
    ("Todos los archivos", "*.*"),
]


class CloudPreview(Canvas):
    """A lightweight rasterized 3D sample. It never owns the full clouds."""

    def __init__(self, master):
        super().__init__(master, background="#101B25", highlightthickness=0, cursor="fleur")
        self.source: np.ndarray | None = None
        self.target: np.ndarray | None = None
        self._center = np.zeros(3, dtype=float)
        self._span = 1.0
        self.yaw = math.radians(-35)
        self.pitch = math.radians(55)
        self.zoom = 0.92
        self.pan = np.zeros(2, dtype=float)
        self._photo = None
        self._drag = None
        self._draw_job = None
        self.bind("<Configure>", lambda _e: self.schedule_draw())
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._drag_rotate)
        self.bind("<ButtonPress-3>", self._start_drag)
        self.bind("<B3-Motion>", self._drag_pan)
        self.bind("<MouseWheel>", self._wheel)

    def set_clouds(self, source: np.ndarray, target: np.ndarray):
        self.source = source
        self.target = target
        all_points = np.vstack((self.source, self.target))
        self._center = np.median(all_points, axis=0)
        low, high = np.percentile(all_points, (1, 99), axis=0)
        self._span = float(max(high - low))
        self.reset_view()

    def clear_clouds(self):
        self.source = self.target = None
        self.schedule_draw()

    def reset_view(self):
        self.yaw = math.radians(-35)
        self.pitch = math.radians(55)
        self.zoom = 0.92
        self.pan[:] = 0
        self.schedule_draw()

    def set_top_view(self):
        self.yaw = 0.0
        self.pitch = math.radians(89.8)
        self.schedule_draw()

    def _start_drag(self, event):
        self._drag = (event.x, event.y)

    def _drag_rotate(self, event):
        if self._drag is None:
            return
        dx, dy = event.x - self._drag[0], event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.yaw += dx * 0.009
        self.pitch = max(math.radians(-89), min(math.radians(89), self.pitch + dy * 0.009))
        self.schedule_draw()

    def _drag_pan(self, event):
        if self._drag is None:
            return
        dx, dy = event.x - self._drag[0], event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.pan += (dx, dy)
        self.schedule_draw()

    def _wheel(self, event):
        self.zoom = max(0.15, min(8.0, self.zoom * (1.12 if event.delta > 0 else 1 / 1.12)))
        self.schedule_draw()
        return "break"

    def schedule_draw(self):
        if self._draw_job is not None:
            try:
                self.after_cancel(self._draw_job)
            except Exception:
                pass
        self._draw_job = self.after(20, self._render)

    def _project(self, points: np.ndarray, center: np.ndarray, span: float, width: int, height: int):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        rz = np.array(((cy, -sy, 0), (sy, cy, 0), (0, 0, 1)), dtype=float)
        rx = np.array(((1, 0, 0), (0, cp, -sp), (0, sp, cp)), dtype=float)
        rotated = (points - center) @ (rx @ rz).T
        scale = min(width, height) * 0.80 / max(span, 1e-9) * self.zoom
        px = np.rint(rotated[:, 0] * scale + width / 2 + self.pan[0]).astype(np.int32)
        py = np.rint(-rotated[:, 1] * scale + height / 2 + self.pan[1]).astype(np.int32)
        return px, py, rotated[:, 2]

    def _render(self):
        self._draw_job = None
        width, height = max(200, self.winfo_width()), max(180, self.winfo_height())
        image = Image.new("RGB", (width, height), "#101B25")
        if self.source is None or self.target is None or not len(self.source) or not len(self.target):
            draw = ImageDraw.Draw(image)
            draw.text((width / 2, height / 2), "Calcula el registro y carga la vista previa", anchor="mm", fill="#B5C6D1")
        else:
            pixels = np.asarray(image).copy()
            for points, color in ((self.target, (85, 214, 232)), (self.source, (255, 170, 75))):
                px, py, _depth = self._project(points, self._center, self._span, width, height)
                valid = (px >= 0) & (px < width) & (py >= 0) & (py < height)
                pixels[py[valid], px[valid]] = color
            image = Image.fromarray(pixels, mode="RGB")
        draw = ImageDraw.Draw(image)
        draw.text((18, 16), "ESCÁNER / BASE LOCAL", fill="#55D6E8")
        draw.text((18, 35), "DRON AJUSTADO", fill="#FFAA4B")
        draw.text((18, height - 26), "Arrastra: girar · rueda: zoom · botón derecho: mover", fill="#8198A8")
        self._photo = ImageTk.PhotoImage(image)
        self.delete("all")
        self.create_image(0, 0, image=self._photo, anchor="nw")


class PointCloudMergeTool(ttk.Frame):
    def __init__(self, master, logo_path: Path, on_home):
        super().__init__(master, style="App.TFrame")
        self.logo_path = logo_path
        self.on_home = on_home
        self.source_path: Path | None = None
        self.target_path: Path | None = None
        self.registration: RegistrationResult | None = None
        self._preview_queue: queue.Queue = queue.Queue()
        self._preview_cancel = threading.Event()
        self._preview_token = 0
        self._visual_samples: dict | None = None
        self._open_advanced_when_ready = False
        self._advanced_viewers: list[dict] = []
        self._merge_queue = None
        self._merge_process = None
        self._merge_cancel = None
        self._merge_running = False
        self.coordinate_rows: list[dict] = []
        self.status_var = StringVar(value="Selecciona las dos nubes e ingresa al menos tres pares XYZ")
        self.source_label = StringVar(value="Ninguna nube de escáner seleccionada")
        self.target_label = StringVar(value="Ninguna nube de dron seleccionada")
        self.source_info_var = StringVar(value="Base fija: define el sistema local del resultado")
        self.target_info_var = StringVar(value="Nube móvil: sus coordenadas UTM se ajustarán al escáner")
        self.source_unit = StringVar(value="Metros")
        self.target_unit = StringVar(value="Metros")
        self.qc_var = StringVar(value="Aún no se ha calculado la transformación")
        self.preview_var = StringVar(value="Vista visual reducida; las coordenadas escritas mandan sobre el preview.")
        self.progress_var = DoubleVar(value=0.0)
        self._build()
        for _ in range(3):
            self.add_coordinate_row()
        self.after(100, self._poll_preview)
        self.after(120, self._poll_merge)
        self.after(250, self._poll_advanced_viewers)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self, style="Header.TFrame", padding=(18, 10))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="‹ Herramientas", style="HeaderButton.TButton", command=self.on_home).pack(side="left")
        ttk.Label(toolbar, text="Registro y fusión de nubes", style="HeaderTitle.TLabel").pack(side="left", padx=16)
        ttk.Button(toolbar, text="Nuevo", style="HeaderButton.TButton", command=self.new_project).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Abrir proyecto", style="HeaderButton.TButton", command=self.open_project).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Guardar proyecto", style="HeaderButton.TButton", command=self.save_project).pack(side="left", padx=2)
        self.merge_button = ttk.Button(toolbar, text="Fusionar a LAZ", style="HeaderAccent.TButton", command=self.start_merge)
        self.merge_button.pack(side="right")
        self.cancel_button = ttk.Button(toolbar, text="Cancelar", style="HeaderButton.TButton", command=self.cancel_work)
        self.cancel_button.pack(side="right", padx=4)
        self.cancel_button.state(["disabled"])

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", padx=14, pady=(14, 8))
        controls = ttk.Frame(panes, style="Card.TFrame", padding=12, width=650)
        preview_card = ttk.Frame(panes, style="Card.TFrame", padding=12)
        panes.add(controls, weight=4)
        panes.add(preview_card, weight=5)
        controls.columnconfigure(0, weight=1)
        controls.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(controls)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self._build_files_tab()
        self._build_controls_tab()
        self._build_export_tab()
        self._build_preview(preview_card)

        footer = ttk.Frame(self, style="StatusBar.TFrame", padding=(16, 5))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="StatusBar.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.progress = ttk.Progressbar(footer, variable=self.progress_var, maximum=1.0)
        self.progress.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ttk.Label(footer, text="Registro XY · Z absoluta intacta", style="StatusBar.TLabel").grid(row=0, column=2)

    def _build_files_tab(self):
        tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=13)
        self.notebook.add(tab, text="1. Nubes")
        tab.columnconfigure(0, weight=1)
        ttk.Label(tab, text="NUBE DE ESCÁNER · BASE LOCAL FIJA", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        source = ttk.Frame(tab, style="Soft.TFrame", padding=11)
        source.grid(row=1, column=0, sticky="ew", pady=(5, 13))
        source.columnconfigure(0, weight=1)
        ttk.Label(source, textvariable=self.source_label, style="SoftSection.TLabel", wraplength=520).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(source, textvariable=self.source_info_var, style="SoftHint.TLabel", wraplength=520).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 9))
        ttk.Button(source, text="Seleccionar escáner", style="Accent.TButton", command=self.choose_source).grid(row=2, column=0, sticky="w")
        self._unit_combo(source, self.source_unit).grid(row=2, column=1, sticky="e")

        ttk.Label(tab, text="NUBE DE DRON · MÓVIL (NORMALMENTE UTM)", style="Section.TLabel").grid(row=2, column=0, sticky="w")
        target = ttk.Frame(tab, style="Soft.TFrame", padding=11)
        target.grid(row=3, column=0, sticky="ew", pady=(5, 13))
        target.columnconfigure(0, weight=1)
        ttk.Label(target, textvariable=self.target_label, style="SoftSection.TLabel", wraplength=520).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(target, textvariable=self.target_info_var, style="SoftHint.TLabel", wraplength=520).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 9))
        ttk.Button(target, text="Seleccionar dron", style="Accent.TButton", command=self.choose_target).grid(row=2, column=0, sticky="w")
        self._unit_combo(target, self.target_unit).grid(row=2, column=1, sticky="e")

        note = (
            "Formatos: LAS/LAZ, E57 y texto XYZ/PTS. Para RCP/RCS exporta E57 o LAS desde ReCap. "
            "El escáner siempre manda: el resultado conserva su sistema local y no se etiqueta como UTM."
        )
        ttk.Label(tab, text=note, style="Hint.Card.TLabel", wraplength=555, justify="left").grid(row=4, column=0, sticky="ew")

    @staticmethod
    def _unit_combo(parent, variable):
        return ttk.Combobox(parent, state="readonly", textvariable=variable, values=list(UNIT_FACTORS), width=21)

    def _build_controls_tab(self):
        tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=12)
        self.notebook.add(tab, text="2. Puntos homólogos")
        tab.columnconfigure(0, weight=1)
        ttk.Label(
            tab,
            text="Escribe el mismo punto físico en ambos sistemas. Usa al menos tres puntos no alineados y, si puedes, agrega controles extra.",
            style="Hint.Card.TLabel", wraplength=570, justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header = ttk.Frame(tab, style="Soft.TFrame", padding=(4, 6))
        header.grid(row=1, column=0, sticky="ew")
        header.columnconfigure(0, minsize=36)
        for column in range(1, 7):
            header.columnconfigure(column, weight=1)
        labels = ("Punto", "Escáner X", "Escáner Y", "Escáner Z", "Dron X", "Dron Y", "Dron Z")
        for column, label in enumerate(labels):
            ttk.Label(header, text=label, style="SoftSection.TLabel").grid(row=0, column=column, padx=2)

        row_card = ttk.Frame(tab, style="Card.TFrame")
        row_card.grid(row=2, column=0, sticky="nsew", pady=5)
        row_card.columnconfigure(0, weight=1)
        self.rows_canvas = Canvas(row_card, height=265, background="white", highlightthickness=0)
        self.rows_canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(row_card, orient="vertical", command=self.rows_canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(row_card, orient="horizontal", command=self.rows_canvas.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.rows_canvas.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.rows_frame = ttk.Frame(self.rows_canvas, style="Card.TFrame")
        self.rows_window = self.rows_canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.bind("<Configure>", lambda _e: self.rows_canvas.configure(scrollregion=self.rows_canvas.bbox("all")))
        self.rows_canvas.bind(
            "<Configure>", lambda e: self.rows_canvas.itemconfigure(self.rows_window, width=max(e.width, 650))
        )

        buttons = ttk.Frame(tab, style="Card.TFrame")
        buttons.grid(row=3, column=0, sticky="ew", pady=(5, 8))
        ttk.Button(buttons, text="+ Agregar control", style="Secondary.TButton", command=self.add_coordinate_row).pack(side="left")
        ttk.Button(buttons, text="Quitar último", style="Secondary.TButton", command=self.remove_coordinate_row).pack(side="left", padx=5)
        ttk.Button(buttons, text="Pegar tabla", style="Secondary.TButton", command=self.open_paste_dialog).pack(side="left")
        ttk.Button(buttons, text="Calcular registro", style="Accent.TButton", command=self.calculate_registration).pack(side="right")

        qc = ttk.Frame(tab, style="Soft.TFrame", padding=9)
        qc.grid(row=4, column=0, sticky="ew")
        ttk.Label(qc, textvariable=self.qc_var, style="SoftSection.TLabel", wraplength=550, justify="left").pack(anchor="w")
        self.residual_tree = ttk.Treeview(qc, columns=("point", "xy", "z"), show="headings", height=4)
        self.residual_tree.heading("point", text="Control")
        self.residual_tree.heading("xy", text="Error XY (m)")
        self.residual_tree.heading("z", text="Diferencia Z (m)")
        self.residual_tree.column("point", width=80, anchor="center")
        self.residual_tree.column("xy", width=145, anchor="e")
        self.residual_tree.column("z", width=155, anchor="e")
        self.residual_tree.pack(fill="x", pady=(7, 0))

    def _build_export_tab(self):
        tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=13)
        self.notebook.add(tab, text="3. Exportación")
        tab.columnconfigure(0, weight=1)
        ttk.Label(tab, text="SISTEMA LOCAL DEL RESULTADO", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        local = ttk.Frame(tab, style="Soft.TFrame", padding=11)
        local.grid(row=1, column=0, sticky="ew", pady=(5, 13))
        ttk.Label(local, text="El escáner es la base fija", style="SoftSection.TLabel").pack(anchor="w")
        ttk.Label(
            local,
            text=("La nube de dron se rota y traslada desde sus coordenadas originales hacia el sistema local del escáner. "
                  "El LAZ final no lleva CRS UTM, aunque la nube de dron sí haya comenzado en UTM."),
            style="SoftHint.TLabel", wraplength=535, justify="left",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            tab,
            text=("El archivo final contiene primero el escáner sin modificar y después el dron ajustado. "
                  "Se agrega el atributo source_id: 0 = escáner base, 1 = dron ajustado. También se genera un JSON con matriz, controles y residuales."),
            style="Hint.Card.TLabel", wraplength=560, justify="left",
        ).grid(row=2, column=0, sticky="ew")
        warning = ttk.Frame(tab, style="Soft.TFrame", padding=11)
        warning.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        ttk.Label(warning, text="E57 Y ARCHIVOS GRANDES", style="SoftSection.TLabel").pack(anchor="w")
        ttk.Label(
            warning,
            text=("LAS/LAZ y texto se procesan por bloques. La biblioteca E57 carga un escaneo a la vez; si cada escaneo es enorme, "
                  "conviene convertirlo antes a LAZ. El proceso de fusión corre separado de Tkinter y puede cancelarse."),
            style="SoftHint.TLabel", wraplength=535, justify="left",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Button(tab, text="Fusionar las dos nubes a LAZ", style="Accent.TButton", command=self.start_merge).grid(row=4, column=0, sticky="ew", pady=(18, 0))

    def _build_preview(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        ttk.Label(parent, text="VISTA PREVIA 3D REDUCIDA", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        tools = ttk.Frame(parent, style="Card.TFrame")
        tools.grid(row=1, column=0, sticky="ew", pady=(5, 7))
        ttk.Button(tools, text="Cargar / actualizar", style="Accent.TButton", command=self.start_preview).pack(side="left")
        ttk.Button(tools, text="Vista superior", style="Secondary.TButton", command=lambda: self.preview.set_top_view()).pack(side="left", padx=5)
        ttk.Button(tools, text="Encuadrar", style="Secondary.TButton", command=lambda: self.preview.reset_view()).pack(side="left")
        ttk.Button(
            tools, text="Abrir visor 3D avanzado", style="Secondary.TButton",
            command=self.open_advanced_viewer,
        ).pack(side="right")
        self.preview = CloudPreview(parent)
        self.preview.grid(row=2, column=0, sticky="nsew")
        ttk.Label(parent, textvariable=self.preview_var, style="Hint.Card.TLabel", wraplength=650, justify="left").grid(row=3, column=0, sticky="ew", pady=(7, 0))

    def add_coordinate_row(self, values=None):
        row_index = len(self.coordinate_rows)
        variables = [StringVar(value=str(value) if value is not None else "") for value in (values or [None] * 6)]
        widgets = []
        ttk.Label(self.rows_frame, text=f"P{row_index + 1}", style="Card.TLabel").grid(row=row_index, column=0, padx=(4, 3), pady=3)
        self.rows_frame.columnconfigure(0, minsize=36)
        for column, variable in enumerate(variables, 1):
            self.rows_frame.columnconfigure(column, weight=1)
            entry = ttk.Entry(self.rows_frame, textvariable=variable, width=11)
            entry.grid(row=row_index, column=column, sticky="ew", padx=2, pady=3)
            widgets.append(entry)
        self.coordinate_rows.append({"vars": variables, "widgets": widgets})

    def remove_coordinate_row(self):
        if len(self.coordinate_rows) <= 3:
            messagebox.showinfo("Controles mínimos", "El registro necesita al menos tres pares de puntos.", parent=self)
            return
        row = self.coordinate_rows.pop()
        row_index = len(self.coordinate_rows)
        for widget in self.rows_frame.grid_slaves(row=row_index):
            widget.destroy()

    def _replace_rows(self, rows):
        for widget in self.rows_frame.winfo_children():
            widget.destroy()
        self.coordinate_rows.clear()
        for row in rows:
            self.add_coordinate_row(row)
        while len(self.coordinate_rows) < 3:
            self.add_coordinate_row()

    def open_paste_dialog(self):
        dialog = Toplevel(self)
        dialog.title("Pegar puntos homólogos")
        dialog.geometry("760x430")
        dialog.minsize(620, 360)
        dialog.transient(self.winfo_toplevel())
        dialog.configure(background="#F4F9FC")
        frame = ttk.Frame(dialog, style="Dialog.TFrame", padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Pega seis columnas por fila: Escáner X Y Z, Dron X Y Z", style="DialogSection.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Acepta columnas separadas por tabulador, espacios, comas o punto y coma.", style="Dialog.TLabel").pack(anchor="w", pady=(3, 8))
        editor = Text(frame, font=("Consolas", 10), wrap="none", undo=True)
        editor.pack(fill="both", expand=True)

        def apply_rows():
            import re

            rows = []
            for line in editor.get("1.0", "end").splitlines():
                parts = [part for part in re.split(r"[,;\t\s]+", line.strip()) if part]
                if not parts:
                    continue
                if len(parts) != 6:
                    messagebox.showerror("Fila incompleta", f"Cada fila debe tener 6 valores.\n\n{line}", parent=dialog)
                    return
                try:
                    rows.append([float(part) for part in parts])
                except ValueError:
                    messagebox.showerror("Valor inválido", f"No se pudo interpretar esta fila:\n\n{line}", parent=dialog)
                    return
            if len(rows) < 3:
                messagebox.showwarning("Faltan controles", "Pega al menos tres filas.", parent=dialog)
                return
            self._replace_rows(rows)
            dialog.destroy()

        ttk.Button(frame, text="Usar estas coordenadas", style="Accent.TButton", command=apply_rows).pack(anchor="e", pady=(10, 0))
        editor.focus_set()

    def _choose_cloud(self, role):
        filename = filedialog.askopenfilename(title=f"Seleccionar nube de {role}", filetypes=FILE_TYPES, parent=self)
        if not filename:
            return
        path = Path(filename)
        try:
            info = inspect_cloud(path)
        except Exception as exc:
            messagebox.showerror("No se pudo leer la nube", str(exc), parent=self)
            return
        count = f"{info.point_count:,} puntos" if info.point_count is not None else "cantidad por determinar"
        details = f"{info.format} · {count}"
        if info.scan_count is not None:
            details += f" · {info.scan_count} escaneo(s)"
        if role == "escáner":
            self.source_path = path
            self.source_label.set(path.name)
            self.source_info_var.set(details + " · permanecerá fija y definirá el sistema local final")
        else:
            self.target_path = path
            self.target_label.set(path.name)
            self.target_info_var.set(details + " · se moverá desde UTM hacia el sistema local del escáner")
        self.registration = None
        self._visual_samples = None
        self.preview.clear_clouds()

    def choose_source(self):
        self._choose_cloud("escáner")

    def choose_target(self):
        self._choose_cloud("dron")

    def collect_pairs(self):
        rows = []
        for index, row in enumerate(self.coordinate_rows, 1):
            values = [variable.get().strip().replace(",", ".") for variable in row["vars"]]
            if not any(values):
                continue
            if not all(values):
                raise RegistrationError(f"El punto P{index} está incompleto.")
            try:
                rows.append([float(value) for value in values])
            except ValueError as exc:
                raise RegistrationError(f"El punto P{index} contiene una coordenada inválida.") from exc
        if len(rows) < 3:
            raise RegistrationError("Completa al menos tres pares XYZ.")
        return rows

    def calculate_registration(self, show_errors=True):
        try:
            pairs = self.collect_pairs()
            scanner = [row[:3] for row in pairs]
            drone = [row[3:] for row in pairs]
            self.registration = solve_rigid_registration(
                drone, scanner, UNIT_FACTORS[self.target_unit.get()], UNIT_FACTORS[self.source_unit.get()]
            )
        except Exception as exc:
            self.registration = None
            if show_errors:
                messagebox.showerror("No se pudo calcular el registro", str(exc), parent=self)
            return False
        result = self.registration
        check = "con controles redundantes" if result.independent_check else "solución mínima; agrega un cuarto punto para verificar"
        scale_ratio = result.source_spread / result.target_spread if result.target_spread else float("inf")
        unit_note = ""
        if not 0.5 <= scale_ratio <= 2.0:
            unit_note = " · ⚠ revisa unidades o correspondencias: las separaciones difieren mucho"
        vertical_note = ""
        if result.vertical_max_difference > 0.03:
            vertical_note = " · ⚠ las cotas difieren; Z no fue modificada"
        self.qc_var.set(
            f"RMSE XY: {result.planar_rmse:.4f} m · ΔZ RMSE: {result.vertical_rmse:.4f} m · "
            f"giro Z: {result.yaw_degrees:.5f}° · {result.pair_count} pares, {check}"
            f"{vertical_note}{unit_note}"
        )
        for item in self.residual_tree.get_children():
            self.residual_tree.delete(item)
        for index, (planar, vertical) in enumerate(
            zip(result.planar_residuals, result.vertical_differences), 1
        ):
            self.residual_tree.insert(
                "", "end", values=(f"P{index}", f"{planar:.4f}", f"{vertical:+.4f}")
            )
        self.status_var.set(
            f"Registro calculado · XY {result.planar_rmse:.4f} m · Z conservada"
        )
        return True

    def start_preview(self):
        if not self.source_path or not self.target_path:
            messagebox.showwarning("Faltan nubes", "Selecciona la nube de escáner y la nube de dron.", parent=self)
            return
        if not self.calculate_registration():
            return
        self._preview_token += 1
        token = self._preview_token
        self._preview_cancel.set()
        self._preview_cancel = threading.Event()
        cancellation = self._preview_cancel
        registration = self.registration
        scanner_path, drone_path = self.source_path, self.target_path
        scanner_factor = UNIT_FACTORS[self.source_unit.get()]
        drone_factor = UNIT_FACTORS[self.target_unit.get()]
        self.preview_var.set("Muestreando ambas nubes en segundo plano…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.cancel_button.state(["!disabled"])

        def work():
            try:
                scanner = sample_cloud_visual(scanner_path, scanner_factor, 250_000, cancellation.is_set)
                drone = sample_cloud_visual(drone_path, drone_factor, 250_000, cancellation.is_set)
                rotation = np.asarray(registration.rotation)
                translation = np.asarray(registration.translation)
                drone_adjusted = drone.xyz @ rotation.T + translation
                visual = {
                    "scanner_xyz": scanner.xyz,
                    "scanner_rgb": scanner.rgb,
                    "drone_raw_xyz": drone.xyz,
                    "drone_adjusted_xyz": drone_adjusted,
                    "drone_rgb": drone.rgb,
                }
                self._preview_queue.put((token, "done", visual, None))
            except InterruptedError:
                self._preview_queue.put((token, "cancelled", None, None))
            except Exception as exc:
                self._preview_queue.put((token, "error", str(exc), None))

        threading.Thread(target=work, name="pointcloud-preview", daemon=True).start()

    def _poll_preview(self):
        try:
            while True:
                token, kind, first, second = self._preview_queue.get_nowait()
                if token != self._preview_token:
                    continue
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self.cancel_button.state(["disabled"] if not self._merge_running else ["!disabled"])
                if kind == "done":
                    self._visual_samples = first
                    self.preview.set_clouds(first["drone_adjusted_xyz"], first["scanner_xyz"])
                    self.preview_var.set(
                        f"Muestra cargada: {len(first['scanner_xyz']):,} puntos del escáner base y "
                        f"{len(first['drone_adjusted_xyz']):,} del dron ajustado. "
                        "Usa el visor avanzado para renderizado GPU."
                    )
                    if self._open_advanced_when_ready:
                        self._open_advanced_when_ready = False
                        self._launch_advanced_viewer()
                elif kind == "error":
                    self.preview_var.set(f"No se pudo cargar la vista previa: {first}")
                else:
                    self.preview_var.set("Vista previa cancelada.")
        except queue.Empty:
            pass
        self.after(100, self._poll_preview)

    def open_advanced_viewer(self):
        if importlib.util.find_spec("open3d") is None:
            messagebox.showerror(
                "Visor 3D no disponible",
                "Open3D no está instalado en esta versión de la aplicación.",
                parent=self,
            )
            return
        if self._visual_samples is None:
            self._open_advanced_when_ready = True
            self.preview_var.set("Preparando la muestra para abrir el visor 3D avanzado…")
            self.start_preview()
            return
        self._launch_advanced_viewer()

    def _launch_advanced_viewer(self):
        if self._visual_samples is None:
            return
        payload = dict(self._visual_samples)
        payload["names"] = {
            "scanner": self.source_path.name if self.source_path else "Escáner",
            "drone": self.target_path.name if self.target_path else "Dron",
        }
        context = multiprocessing.get_context("spawn")
        status_queue = context.Queue()
        process = context.Process(
            target=run_advanced_viewer,
            args=(payload, status_queue),
            name="pointcloud-open3d-viewer",
            daemon=True,
        )
        try:
            process.start()
        except Exception as exc:
            messagebox.showerror("No se pudo abrir el visor 3D", str(exc), parent=self)
            return
        self._advanced_viewers.append({"process": process, "queue": status_queue, "reported": False})
        self.status_var.set("Abriendo visor 3D avanzado en una ventana independiente…")

    def _poll_advanced_viewers(self):
        survivors = []
        for viewer in self._advanced_viewers:
            process = viewer["process"]
            status_queue = viewer["queue"]
            try:
                while True:
                    message = status_queue.get_nowait()
                    kind = message.get("kind")
                    if kind == "ready":
                        viewer["reported"] = True
                        self.status_var.set("Visor 3D avanzado abierto · renderizado con GPU")
                    elif kind == "error":
                        viewer["reported"] = True
                        messagebox.showerror(
                            "El visor 3D se cerró con error",
                            message.get("message", "Open3D no pudo inicializar la ventana."),
                            parent=self,
                        )
            except queue.Empty:
                pass
            if process.is_alive():
                survivors.append(viewer)
            else:
                process.join(timeout=0.05)
                if process.exitcode not in (0, None) and not viewer["reported"]:
                    messagebox.showerror(
                        "No se pudo iniciar el visor 3D",
                        f"El proceso gráfico terminó inesperadamente (código {process.exitcode}).",
                        parent=self,
                    )
        self._advanced_viewers = survivors
        self.after(250, self._poll_advanced_viewers)

    def _project_payload(self):
        return {
            "schema": "grupo-itt.pointcloud-merge-project.v3",
            "scanner_path": str(self.source_path or ""),
            "drone_path": str(self.target_path or ""),
            "scanner_unit": self.source_unit.get(),
            "drone_unit": self.target_unit.get(),
            "pairs": self.collect_pairs(),
            "output_reference": "scanner_local_metres",
            "registration_mode": "yaw_xy_preserve_z",
        }

    def save_project(self):
        try:
            payload = self._project_payload()
        except Exception as exc:
            messagebox.showerror("No se puede guardar", str(exc), parent=self)
            return
        filename = filedialog.asksaveasfilename(
            title="Guardar proyecto de registro", initialdir=category_dir("pointclouds"),
            defaultextension=".json", filetypes=[("Proyecto JSON", "*.json")], parent=self,
        )
        if filename:
            Path(filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_var.set(f"Proyecto guardado · {Path(filename).name}")

    def open_project(self):
        filename = filedialog.askopenfilename(
            title="Abrir proyecto de registro", initialdir=category_dir("pointclouds"),
            filetypes=[("Proyecto JSON", "*.json")], parent=self,
        )
        if not filename:
            return
        try:
            payload = json.loads(Path(filename).read_text(encoding="utf-8"))
            schema = payload.get("schema")
            if schema not in {
                "grupo-itt.pointcloud-merge-project.v1",
                "grupo-itt.pointcloud-merge-project.v2",
                "grupo-itt.pointcloud-merge-project.v3",
            }:
                raise ValueError("El archivo no es un proyecto de registro compatible.")
            if schema.endswith(".v1"):
                # v1 already stored scanner first and drone second, although it
                # originally interpreted the transformation in reverse.
                scanner_path = payload.get("source_path", "")
                drone_path = payload.get("target_path", "")
                scanner_unit = payload.get("source_unit", "Metros")
                drone_unit = payload.get("target_unit", "Metros")
            else:
                scanner_path = payload.get("scanner_path", "")
                drone_path = payload.get("drone_path", "")
                scanner_unit = payload.get("scanner_unit", "Metros")
                drone_unit = payload.get("drone_unit", "Metros")
            self.source_path = Path(scanner_path) if scanner_path else None
            self.target_path = Path(drone_path) if drone_path else None
            self.source_label.set(self.source_path.name if self.source_path else "Ninguna nube de escáner seleccionada")
            self.target_label.set(self.target_path.name if self.target_path else "Ninguna nube de dron seleccionada")
            self.source_unit.set(scanner_unit)
            self.target_unit.set(drone_unit)
            self._replace_rows(payload.get("pairs", []))
            self.calculate_registration(show_errors=False)
            self.status_var.set(f"Proyecto abierto · {Path(filename).name}")
        except Exception as exc:
            messagebox.showerror("No se pudo abrir el proyecto", str(exc), parent=self)

    def new_project(self):
        if self._merge_running:
            return
        self.source_path = self.target_path = None
        self.source_label.set("Ninguna nube de escáner seleccionada")
        self.target_label.set("Ninguna nube de dron seleccionada")
        self.source_info_var.set("Base fija: define el sistema local del resultado")
        self.target_info_var.set("Nube móvil: sus coordenadas UTM se ajustarán al escáner")
        self._replace_rows([])
        self.registration = None
        self._visual_samples = None
        self.preview.clear_clouds()
        self.qc_var.set("Aún no se ha calculado la transformación")
        self.status_var.set("Proyecto nuevo")

    def start_merge(self):
        if self._merge_running:
            return
        if not self.source_path or not self.target_path:
            messagebox.showwarning("Faltan nubes", "Selecciona la nube de escáner y la nube de dron.", parent=self)
            return
        if not self.calculate_registration():
            return
        filename = filedialog.asksaveasfilename(
            title="Guardar nube fusionada", initialdir=category_dir("pointclouds"), initialfile="nubes_fusionadas.laz",
            defaultextension=".laz", filetypes=[("Nube LAZ", "*.laz")], parent=self,
        )
        if not filename:
            return
        output = Path(filename)
        if output.resolve() in {self.source_path.resolve(), self.target_path.resolve()}:
            messagebox.showerror("Salida inválida", "La salida no puede reemplazar una nube original.", parent=self)
            return
        if output.exists() and not messagebox.askyesno("Reemplazar archivo", f"Ya existe {output.name}. ¿Deseas reemplazarlo?", parent=self):
            return
        pairs = self.collect_pairs()
        request = {
            "scanner_path": str(self.source_path),
            "drone_path": str(self.target_path),
            "scanner_unit": self.source_unit.get(),
            "drone_unit": self.target_unit.get(),
            "scanner_factor": UNIT_FACTORS[self.source_unit.get()],
            "drone_factor": UNIT_FACTORS[self.target_unit.get()],
            "pairs": pairs,
            "output_path": str(output),
            "chunk_size": 500_000,
        }
        context = multiprocessing.get_context("spawn")
        self._merge_queue = context.Queue()
        self._merge_cancel = context.Event()
        self._merge_process = context.Process(
            target=run_merge_worker, args=(request, self._merge_queue, self._merge_cancel),
            name="pointcloud-merge", daemon=True,
        )
        self._merge_process.start()
        self._merge_running = True
        self.merge_button.state(["disabled"])
        self.cancel_button.state(["!disabled"])
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress_var.set(0)
        self.status_var.set("Preparando archivos…")

    def _poll_merge(self):
        message_queue = self._merge_queue
        if message_queue is not None:
            try:
                while True:
                    message = message_queue.get_nowait()
                    kind = message.get("kind")
                    if kind == "progress":
                        progress = message.get("progress")
                        if progress is None:
                            self.progress.configure(mode="indeterminate")
                            self.progress.start(12)
                        else:
                            self.progress.stop()
                            self.progress.configure(mode="determinate")
                            self.progress_var.set(float(progress))
                        total = message.get("total")
                        count = message.get("completed", 0)
                        suffix = f" · {count:,}/{total:,}" if total else f" · {count:,} puntos"
                        self.status_var.set(message.get("message", "Procesando") + suffix)
                    elif kind == "done":
                        self._finish_merge()
                        self.progress_var.set(1.0)
                        output = Path(message["output_path"])
                        self.status_var.set(f"Fusión terminada · {message.get('point_count', 0):,} puntos")
                        audit = message.get("audit", {})
                        scanner_count = audit.get("scanner_input_points")
                        drone_count = audit.get("drone_input_points")
                        output_count = audit.get("output_header_points")
                        if scanner_count is not None and drone_count is not None and output_count is not None:
                            audit_text = (
                                f"\n\nAuditoría de puntos:\n"
                                f"Escáner: {scanner_count:,}\nDron: {drone_count:,}\n"
                                f"LAZ final: {output_count:,}\nDiferencia: {audit.get('difference', 0):+,}"
                            )
                        else:
                            audit_text = f"\n\nLAZ final: {message.get('point_count', 0):,} puntos escritos."
                        answer = messagebox.askyesno(
                            "Nubes fusionadas",
                            f"Se creó:\n{output}{audit_text}\n\nTambién se guardó el reporte de registro JSON.\n\n¿Abrir la carpeta?",
                            parent=self,
                        )
                        if answer:
                            os.startfile(output.parent)
                    elif kind == "cancelled":
                        self._finish_merge()
                        self.status_var.set("Fusión cancelada; no se conservaron archivos incompletos")
                    elif kind == "error":
                        self._finish_merge()
                        self.status_var.set("La fusión terminó con error")
                        messagebox.showerror("No se pudo fusionar", message.get("message", "Error desconocido"), parent=self)
            except queue.Empty:
                pass
        if self._merge_running and self._merge_process is not None and not self._merge_process.is_alive():
            # A hard worker crash may happen before it can put a structured error.
            exit_code = self._merge_process.exitcode
            if exit_code not in (None, 0):
                self._finish_merge()
                messagebox.showerror("El proceso se detuvo", f"El proceso de fusión terminó inesperadamente (código {exit_code}).", parent=self)
        self.after(120, self._poll_merge)

    def _finish_merge(self):
        self._merge_running = False
        self.merge_button.state(["!disabled"])
        self.cancel_button.state(["disabled"])
        if self._merge_process is not None:
            self._merge_process.join(timeout=0.2)
        self._merge_process = None
        self._merge_cancel = None
        self._merge_queue = None

    def cancel_work(self):
        self._preview_cancel.set()
        if self._merge_cancel is not None:
            self._merge_cancel.set()
            self.status_var.set("Cancelando al terminar el bloque actual…")
