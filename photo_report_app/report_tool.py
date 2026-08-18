from __future__ import annotations

import os
import queue
import threading
from datetime import date, datetime
from pathlib import Path
from tkinter import END, BooleanVar, StringVar, filedialog, messagebox
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk

from .app_storage import SETTINGS, category_dir, preserve_artifact
from .branding import active_profile
from .metadata import PhotoInfo, read_photo
from .pdf_generator import ReportOptions, generate_report


class ReportTool(ttk.Frame):
    """Editor de reportes con zonas fijas; sólo las listas tienen scroll."""

    def __init__(self, master, logo_path: Path, on_home):
        super().__init__(master, style="App.TFrame")
        self.logo_path = logo_path
        self.on_home = on_home
        self.photos: list[PhotoInfo] = []
        self.title_var = StringVar(value="Reporte fotográfico")
        self.date_var = StringVar(value=date.today().strftime("%d/%m/%Y"))
        self.open_var = BooleanVar(value=bool(SETTINGS.get("reports.open_pdf", True)))
        self.map_var = BooleanVar(value=bool(SETTINGS.get("reports.include_map", True)))
        self.status_var = StringVar(value="Agrega fotografías para comenzar")
        self._selected_index: int | None = None
        self._preview_image = None
        self._events = queue.Queue()
        self._build()
        self.after(120, self._poll)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self, style="Header.TFrame", padding=(22, 12))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="‹ Herramientas", style="HeaderButton.TButton", command=self.on_home).pack(side="left")
        ttk.Label(toolbar, text="Reporte fotográfico", style="HeaderTitle.TLabel").pack(side="left", padx=18)
        ttk.Label(toolbar, text="Fotos, croquis y descripciones en PDF", style="HeaderSub.TLabel").pack(side="left")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=18)
        settings = ttk.Frame(body, style="Card.TFrame", padding=20, width=300)
        workspace = ttk.Frame(body, style="Card.TFrame", padding=16)
        body.add(settings, weight=0)
        body.add(workspace, weight=1)

        ttk.Label(settings, text="DATOS DEL REPORTE", style="Section.TLabel").pack(anchor="w", pady=(0, 14))
        self._field(settings, "Nombre del reporte", self.title_var)
        self._field(settings, "Fecha (DD/MM/AAAA)", self.date_var)
        ttk.Separator(settings).pack(fill="x", pady=15)
        ttk.Checkbutton(settings, text="Incluir croquis de ubicación", variable=self.map_var, command=self._map_changed).pack(anchor="w", pady=4)
        ttk.Label(settings, text="Usa el GPS de las fotografías. Si se desactiva, la primera foto será la portada.", style="Hint.Card.TLabel", wraplength=245).pack(anchor="w", pady=(2, 9))
        ttk.Checkbutton(settings, text="Abrir PDF al terminar", variable=self.open_var, command=self._open_changed).pack(anchor="w", pady=4)
        ttk.Button(settings, text="Cambiar logo", style="Secondary.TButton", command=self._choose_logo).pack(fill="x", pady=(18, 7))
        self.generate_btn = ttk.Button(settings, text="Generar reporte PDF", style="Accent.TButton", command=self._generate)
        self.generate_btn.pack(fill="x")
        self.progress = ttk.Progressbar(settings, mode="determinate")
        self.progress.pack(fill="x", pady=(12, 0))

        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(1, weight=1)
        actions = ttk.Frame(workspace, style="Card.TFrame")
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(actions, text="FOTOGRAFÍAS", style="Section.TLabel").pack(side="left")
        ttk.Button(actions, text="+ Agregar", style="Accent.TButton", command=self._add).pack(side="right")
        ttk.Button(actions, text="Eliminar", style="Secondary.TButton", command=self._remove).pack(side="right", padx=6)
        ttk.Button(actions, text="Bajar", style="Secondary.TButton", command=lambda: self._move(1)).pack(side="right", padx=(6, 0))
        ttk.Button(actions, text="Subir", style="Secondary.TButton", command=lambda: self._move(-1)).pack(side="right")

        table = ttk.Frame(workspace, style="Card.TFrame")
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("order", "file", "gps", "date", "format"), show="headings", selectmode="extended")
        columns = [("order", "#", 42, False), ("file", "Archivo", 280, True), ("gps", "Ubicación", 105, False), ("date", "Captura", 135, False), ("format", "Formato", 85, False)]
        for key, label, width, stretch in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=width if not stretch else 150, anchor="w" if key == "file" else "center", stretch=stretch)
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ybar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", lambda _: self._open_photo())

        detail = ttk.Frame(workspace, style="Soft.TFrame", padding=12, height=205)
        detail.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        detail.grid_propagate(False)
        detail.columnconfigure(1, weight=1)
        detail.rowconfigure(1, weight=1)
        self.preview_label = ttk.Label(detail, text="Selecciona una fotografía", style="Soft.TLabel", anchor="center", width=31)
        self.preview_label.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(0, 14))
        ttk.Label(detail, text="Descripción opcional", style="SoftSection.TLabel").grid(row=0, column=1, sticky="w")
        self.description_text = __import__("tkinter").Text(detail, height=5, wrap="word", font=("Segoe UI", 10), relief="flat", bd=0, padx=8, pady=7, state="disabled", background="white", foreground="#263746")
        self.description_text.grid(row=1, column=1, sticky="nsew", pady=(6, 4))
        self.description_text.bind("<FocusOut>", lambda _: self._save_description())
        ttk.Label(detail, text="Se mostrará con esa fotografía en el PDF.", style="SoftHint.TLabel").grid(row=2, column=1, sticky="w")
        ttk.Label(workspace, textvariable=self.status_var, style="Hint.Card.TLabel").grid(row=3, column=0, sticky="w", pady=(9, 0))

    def _field(self, parent, label, variable):
        ttk.Label(parent, text=label, style="Field.Card.TLabel").pack(anchor="w")
        ttk.Entry(parent, textvariable=variable).pack(fill="x", pady=(4, 13))

    def _choose_logo(self):
        path = filedialog.askopenfilename(title="Seleccionar logo", filetypes=[("Imágenes", "*.png *.jpg *.jpeg")])
        if path:
            self.logo_path = Path(path)
            self.status_var.set(f"Logo: {self.logo_path.name}")

    def _map_changed(self):
        SETTINGS.set("reports.include_map", bool(self.map_var.get()))
        self.status_var.set("El reporte incluirá croquis GPS" if self.map_var.get() else "Sin croquis: la primera foto será portada")

    def _open_changed(self):
        SETTINGS.set("reports.open_pdf", bool(self.open_var.get()))

    def _add(self):
        paths = filedialog.askopenfilenames(title="Seleccionar fotografías", filetypes=[("Fotografías", "*.jpg *.jpeg *.png *.tif *.tiff *.webp")])
        existing = {photo.path.resolve() for photo in self.photos}
        errors = []
        for raw in paths:
            try:
                path = Path(raw).resolve()
                if path not in existing:
                    self.photos.append(read_photo(path))
                    existing.add(path)
            except Exception as exc:
                errors.append(f"{Path(raw).name}: {exc}")
        self._refresh()
        if errors:
            messagebox.showwarning("Algunas fotos no se cargaron", "\n".join(errors[:8]))

    def _refresh(self, select: int | None = None):
        self._save_description()
        self.tree.delete(*self.tree.get_children())
        gps = 0
        for index, photo in enumerate(self.photos):
            gps += int(photo.has_gps)
            captured = photo.taken_at.strftime("%d/%m/%Y %H:%M") if photo.taken_at else "No disponible"
            self.tree.insert("", END, iid=str(index), values=(index + 1, photo.path.name, "Con GPS" if photo.has_gps else "Sin GPS", captured, photo.orientation))
        self.status_var.set(f"{len(self.photos)} fotografías  |  {gps} con GPS  |  {len(self.photos) - gps} sin GPS")
        self._clear_detail()
        if select is not None and self.photos:
            select = max(0, min(select, len(self.photos) - 1))
            self.tree.selection_set(str(select))
            self.tree.focus(str(select))
            self.tree.see(str(select))

    def _clear_detail(self):
        self._selected_index = None
        self.description_text.configure(state="normal")
        self.description_text.delete("1.0", END)
        self.description_text.configure(state="disabled")
        self.preview_label.configure(image="", text="Selecciona una fotografía")

    def _save_description(self):
        if self._selected_index is not None and 0 <= self._selected_index < len(self.photos):
            self.photos[self._selected_index].description = self.description_text.get("1.0", "end-1c").strip()

    def _selection_changed(self, _event=None):
        selected = self.tree.selection()
        if len(selected) != 1:
            return
        self._save_description()
        index = int(selected[0])
        self._selected_index = index
        photo = self.photos[index]
        self.description_text.configure(state="normal")
        self.description_text.delete("1.0", END)
        self.description_text.insert("1.0", photo.description)
        try:
            with Image.open(photo.path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((270, 165), Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (270, 165), "#E8EFF4")
                canvas.paste(image, ((270 - image.width) // 2, (165 - image.height) // 2))
            self._preview_image = ImageTk.PhotoImage(canvas)
            self.preview_label.configure(image=self._preview_image, text="")
        except Exception:
            self.preview_label.configure(image="", text="Vista previa no disponible")

    def _remove(self):
        selected = sorted((int(i) for i in self.tree.selection()), reverse=True)
        for index in selected:
            self.photos.pop(index)
        self._refresh()

    def _move(self, direction):
        selected = self.tree.selection()
        if len(selected) != 1:
            return
        old = int(selected[0])
        new = max(0, min(len(self.photos) - 1, old + direction))
        if old != new:
            self._save_description()
            self.photos.insert(new, self.photos.pop(old))
            self._refresh(new)

    def _open_photo(self):
        selected = self.tree.selection()
        if selected:
            os.startfile(self.photos[int(selected[0])].path)

    def _generate(self):
        self._save_description()
        try:
            if not self.title_var.get().strip():
                raise ValueError("Escribe un nombre para el reporte.")
            if not self.photos:
                raise ValueError("Agrega al menos una fotografía.")
            report_date = datetime.strptime(self.date_var.get().strip(), "%d/%m/%Y").date()
        except ValueError as exc:
            messagebox.showwarning("Falta información", str(exc))
            return
        filename = "".join(c if c.isalnum() or c in " -_" else "" for c in self.title_var.get()).strip() + ".pdf"
        output = filedialog.asksaveasfilename(
            title="Guardar reporte", defaultextension=".pdf", initialfile=filename,
            filetypes=[("Documento PDF", "*.pdf")], initialdir=str(category_dir("reports")),
        )
        if not output:
            return
        company = active_profile()
        options = ReportOptions(
            self.title_var.get().strip(), report_date, list(self.photos), Path(output), self.logo_path,
            company=company.document_heading,
            website=company.website_label,
            footer=company.document_footer,
            include_map=self.map_var.get(),
        )
        self.generate_btn.state(["disabled"])
        self.progress["value"] = 2
        self.status_var.set("Preparando documento...")
        threading.Thread(target=self._worker, args=(options,), daemon=True).start()

    def _worker(self, options):
        try:
            path = generate_report(options, lambda done, total: self._events.put(("progress", int(15 + done / max(total, 1) * 84))))
            self._events.put(("done", path))
        except Exception as exc:
            self._events.put(("error", str(exc)))

    def _poll(self):
        try:
            while True:
                kind, value = self._events.get_nowait()
                if kind == "progress":
                    self.progress["value"] = value
                elif kind == "done":
                    self.progress["value"] = 100
                    self.generate_btn.state(["!disabled"])
                    preserve_artifact(value, "reports")
                    self.status_var.set(f"Reporte creado: {value.name}")
                    messagebox.showinfo("Reporte listo", f"El PDF se creó correctamente:\n{value}")
                    if self.open_var.get():
                        os.startfile(value)
                else:
                    self.generate_btn.state(["!disabled"])
                    self.status_var.set("No fue posible generar el reporte")
                    messagebox.showerror("Error al generar", value)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(120, self._poll)
