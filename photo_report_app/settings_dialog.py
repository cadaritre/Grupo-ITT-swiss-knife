from __future__ import annotations

import hashlib
import shutil
import webbrowser
from pathlib import Path
from tkinter import BooleanVar, Canvas, IntVar, StringVar, Toplevel, colorchooser, filedialog, messagebox
from tkinter import ttk
from urllib.parse import urlparse

from PIL import Image, ImageTk

from .app_storage import SETTINGS, category_dir
from .branding import TRESVIZO_LOGO, active_profile, profile_defaults
from .cloud_sync import (
    CLOUD_PARENT_FOLDER,
    ensure_cloud_folders,
    flush_cloud_queue,
    installer_operator_name,
)
from .document_texts import REPORT_TEXT_DEFAULTS, report_texts
from .quotation_i18n import LABELS, labels
from .quote_theme import QUOTE_PALETTE_PRESETS, matching_preset, quote_palette
from .ux_components import ScrollableDialogContent, show_responsive_dialog


PASSWORD_SHA256 = "ed58e0ec3525a7b51713121580d6acba34a1452b3580c6c1d013498e89a270e1"
MAP_LAYERS = ("Calles - OpenStreetMap", "Topográfico - OpenTopoMap", "Base neutra")

PROFILE_TEXT_FIELDS = (
    ("name", "Nombre de la empresa"),
    ("document_heading", "Encabezado en español"),
    ("document_heading_en", "Encabezado en inglés"),
    ("description", "Descripción de servicios en español"),
    ("description_en", "Descripción de servicios en inglés"),
    ("website", "Página web"),
    ("phone", "Teléfono / referencia"),
    ("phone_label", "Etiqueta de teléfono en español"),
    ("phone_label_en", "Etiqueta de teléfono en inglés"),
    ("address", "Dirección"),
    ("email", "Correo electrónico"),
    ("signer", "Nombre predeterminado de quien firma"),
    ("document_footer", "Pie de página en español"),
    ("document_footer_en", "Pie de página en inglés"),
)

REPORT_TEXT_FIELDS = (
    ("header_title", "Nombre del tipo de documento"),
    ("document_tag", "Etiqueta corta del encabezado"),
    ("location_sketch", "Título del croquis"),
    ("map_number_note", "Nota cuando el croquis tiene ubicaciones"),
    ("map_no_gps_note", "Nota cuando las fotos no tienen GPS"),
    ("photo_label", "Etiqueta de fotografía"),
    ("captured_label", "Etiqueta de fecha de captura"),
    ("gps_label", "Etiqueta de coordenadas"),
    ("page_label", "Etiqueta de página"),
    ("cover_date_template", "Formato de fecha de portada"),
    ("months", "Meses separados con |"),
)

QUOTE_TEXT_FIELD_LABELS = {
    "document": "Nombre del documento", "no_number": "Texto cuando no hay folio", "page": "Página",
    "date": "Fecha", "name_company": "Nombre / empresa", "contact": "Contacto", "email": "Correo",
    "phone": "Teléfono", "location": "Localización", "concept": "Concepto / descripción", "unit": "Unidad",
    "quantity": "Cantidad", "unit_price": "Precio unitario", "amount": "Importe", "continued": "Continuación",
    "no_description": "Sin descripción", "subtotal": "Subtotal", "vat": "IVA / VAT", "total": "Total",
    "advance": "Anticipo", "notes": "Notas", "currency": "Moneda", "payment_terms": "Forma de pago",
    "validity": "Vigencia", "days": "Días", "bank_clabe": "Banco / CLABE", "account": "Cuenta",
    "delivery_time": "Tiempo de entrega", "quoted_services": "Servicios cotizados", "no_items": "Sin conceptos",
    "place_date": "Lugar y fecha", "sincerely": "Despedida", "reference_image": "Imagen de referencia",
    "image": "Imagen",
}


class SettingsDialog(Toplevel):
    def __init__(self, master, on_restart):
        super().__init__(master)
        self.withdraw()
        self.title("Ajustes de la aplicación")
        self.transient(master)
        self.on_restart = on_restart
        self.password_var = StringVar()
        self.include_map_var = BooleanVar(value=bool(SETTINGS.get("reports.include_map", True)))
        self.open_pdf_var = BooleanVar(value=bool(SETTINGS.get("reports.open_pdf", True)))
        self.map_layer_var = StringVar(value=SETTINGS.get("sketches.map_layer", MAP_LAYERS[0]))
        self.utm_zone_var = IntVar(value=int(SETTINGS.get("geospatial.utm_zone", 13)))
        self.hemisphere_var = StringVar(value=SETTINGS.get("geospatial.hemisphere", "N"))
        self.cloud_enabled_var = BooleanVar(value=bool(SETTINGS.get("cloud.enabled", True)))
        self.operator_name_var = StringVar(
            value=SETTINGS.get("cloud.operator_name", "") or installer_operator_name()
        )
        self.drive_url_var = StringVar(value=SETTINGS.get("cloud.drive_url", ""))
        self.drive_sync_root_var = StringVar(value=SETTINGS.get("cloud.sync_root", ""))
        self.cloud_status_var = StringVar(value="")
        self._build()
        self.bind("<Escape>", lambda _event: self.destroy())
        show_responsive_dialog(self, master, preferred_width=660)

    def _build(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 15))
        header.pack(fill="x")
        ttk.Label(header, text="⚙ Ajustes", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(header, text="Preferencias recordadas para la siguiente sesión", style="HeaderSub.TLabel").pack(anchor="w", pady=(3, 0))

        actions = ttk.Frame(self, style="Dialog.TFrame", padding=(20, 10, 20, 16))
        actions.pack(fill="x", side="bottom")
        ttk.Button(actions, text="Cancelar", style="Secondary.TButton", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Guardar ajustes", style="Accent.TButton", command=self._save_general).pack(side="right", padx=(0, 8))
        scroll = ScrollableDialogContent(self, height=430, padding=20)
        scroll.pack(fill="both", expand=True)
        body = scroll.content
        general = ttk.LabelFrame(body, text=" PREFERENCIAS GENERALES ", style="Settings.TLabelframe", padding=16)
        general.pack(fill="x")
        ttk.Checkbutton(general, text="Incluir mapa GPS por defecto en reportes fotográficos", variable=self.include_map_var, style="Settings.TCheckbutton").grid(row=0, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Checkbutton(general, text="Abrir automáticamente los PDF generados", variable=self.open_pdf_var, style="Settings.TCheckbutton").grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Label(general, text="Mapa base predeterminado", style="Settings.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 4))
        ttk.Combobox(general, textvariable=self.map_layer_var, values=MAP_LAYERS, state="readonly", width=31).grid(row=2, column=1, sticky="ew", pady=(12, 4))
        ttk.Label(general, text="Zona UTM predeterminada", style="Settings.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Spinbox(general, from_=1, to=60, textvariable=self.utm_zone_var, width=8).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(general, text="Hemisferio predeterminado", style="Settings.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(general, textvariable=self.hemisphere_var, values=("N", "S"), state="readonly", width=7).grid(row=4, column=1, sticky="w", pady=4)
        general.columnconfigure(1, weight=1)

        ttk.Button(
            body, text="🎨 Paleta de cotizaciones PDF", style="Secondary.TButton",
            command=lambda: PaletteDialog(self),
        ).pack(fill="x", pady=(14, 0))
        ttk.Button(
            body, text="✎ Textos de reportes y cotizaciones PDF", style="Secondary.TButton",
            command=lambda: PdfTextDialog(self),
        ).pack(fill="x", pady=(8, 0))

        cloud = ttk.LabelFrame(body, text=" RESPALDO EN GOOGLE DRIVE ", style="Settings.TLabelframe", padding=16)
        cloud.pack(fill="x", pady=(14, 0))
        ttk.Checkbutton(
            cloud,
            text="Respaldar automáticamente cotizaciones, reportes y polígonos KML/KMZ",
            variable=self.cloud_enabled_var,
            style="Settings.TCheckbutton",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 9))
        ttk.Label(cloud, text="Nombre de la persona", style="Settings.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(cloud, textvariable=self.operator_name_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(cloud, text="Liga de la carpeta en Drive", style="Settings.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(cloud, textvariable=self.drive_url_var).grid(row=2, column=1, sticky="ew", pady=4, padx=(0, 7))
        ttk.Button(cloud, text="Abrir liga", style="Secondary.TButton", command=self._open_drive_url).grid(row=2, column=2, sticky="ew", pady=4)
        ttk.Label(cloud, text="Carpeta local sincronizada", style="Settings.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(cloud, textvariable=self.drive_sync_root_var).grid(row=3, column=1, sticky="ew", pady=4, padx=(0, 7))
        ttk.Button(cloud, text="Examinar…", style="Secondary.TButton", command=self._choose_drive_folder).grid(row=3, column=2, sticky="ew", pady=4)
        ttk.Label(
            cloud,
            text=(
                "La liga sirve para abrir la carpeta compartida. Para subir archivos, selecciona también "
                "su ubicación local en Google Drive para escritorio; así Drive sincroniza y reintenta aunque no haya internet."
            ),
            style="Settings.TLabel",
            foreground="#657887",
            wraplength=540,
            justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 4))
        ttk.Label(
            cloud,
            text=(
                f"Estructura fija: {CLOUD_PARENT_FOLDER}  ›  Persona  ›  "
                "Cotizaciones / Reportes fotograficos / Poligonos KML"
            ),
            style="SettingsHeading.TLabel",
            wraplength=540,
            justify="left",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 8))
        ttk.Button(
            cloud,
            text="Verificar y crear carpetas",
            style="Secondary.TButton",
            command=self._verify_drive_configuration,
        ).grid(row=6, column=1, columnspan=2, sticky="e")
        ttk.Label(
            cloud,
            textvariable=self.cloud_status_var,
            style="Settings.TLabel",
            foreground="#2B6F53",
            wraplength=540,
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))
        cloud.columnconfigure(1, weight=1)

        access = ttk.LabelFrame(body, text=" CONFIGURACIÓN AVANZADA ", style="Settings.TLabelframe", padding=16)
        access.pack(fill="x", pady=(14, 0))
        ttk.Label(access, text="Acceso restringido", style="SettingsHeading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(access, text="Ingresa la contraseña administrativa para abrir la identidad corporativa.", style="Settings.TLabel", wraplength=510).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 10))
        entry = ttk.Entry(access, textvariable=self.password_var, show="•")
        entry.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        entry.bind("<Return>", lambda _event: self._unlock())
        ttk.Button(access, text="Desbloquear", style="Secondary.TButton", command=self._unlock).grid(row=2, column=1)
        access.columnconfigure(0, weight=1)

    def _save_general(self):
        try:
            zone = max(1, min(60, int(self.utm_zone_var.get())))
        except (TypeError, ValueError):
            zone = 13
        operator_name = self.operator_name_var.get().strip()
        drive_url = self.drive_url_var.get().strip()
        sync_root = self.drive_sync_root_var.get().strip()
        if drive_url:
            parsed = urlparse(drive_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                messagebox.showerror(
                    "Liga no válida",
                    "Escribe una liga completa de Google Drive, por ejemplo https://drive.google.com/drive/folders/…",
                    parent=self,
                )
                return
        SETTINGS.update({
            "reports.include_map": bool(self.include_map_var.get()),
            "reports.open_pdf": bool(self.open_pdf_var.get()),
            "sketches.map_layer": self.map_layer_var.get() if self.map_layer_var.get() in MAP_LAYERS else MAP_LAYERS[0],
            "geospatial.map_layer": self.map_layer_var.get() if self.map_layer_var.get() in MAP_LAYERS else MAP_LAYERS[0],
            "geospatial.utm_zone": zone,
            "geospatial.hemisphere": self.hemisphere_var.get() if self.hemisphere_var.get() in {"N", "S"} else "N",
            "cloud.enabled": bool(self.cloud_enabled_var.get()),
            "cloud.operator_name": operator_name,
            "cloud.drive_url": drive_url,
            "cloud.sync_root": sync_root,
            "cloud.parent_folder": CLOUD_PARENT_FOLDER,
        })
        warning = ""
        if self.cloud_enabled_var.get() and (operator_name or drive_url or sync_root):
            if not operator_name or not sync_root:
                warning = "El respaldo en Drive quedó pendiente: falta el nombre de la persona o la carpeta local sincronizada."
            else:
                try:
                    ensure_cloud_folders()
                    delivered = flush_cloud_queue()
                    if delivered:
                        warning = f"Configuración guardada. También se entregaron {delivered} archivo(s) que estaban pendientes."
                except OSError as exc:
                    warning = f"Los ajustes se guardaron, pero Drive no está disponible ahora:\n{exc}"
        self.destroy()
        if warning:
            messagebox.showwarning("Respaldo de Drive", warning, parent=self.master)

    def _choose_drive_folder(self):
        initial = self.drive_sync_root_var.get().strip()
        chosen = filedialog.askdirectory(
            parent=self,
            title=f"Selecciona la carpeta local donde se creará {CLOUD_PARENT_FOLDER}",
            initialdir=initial if initial and Path(initial).exists() else None,
        )
        if not chosen:
            return
        selected = Path(chosen)
        if selected.name.casefold() == CLOUD_PARENT_FOLDER.casefold():
            selected = selected.parent
        self.drive_sync_root_var.set(str(selected))
        self.cloud_status_var.set("Carpeta seleccionada. Usa ‘Verificar y crear carpetas’ para probarla.")

    def _open_drive_url(self):
        url = self.drive_url_var.get().strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            messagebox.showinfo(
                "Liga de Google Drive",
                "Primero pega una liga completa, por ejemplo https://drive.google.com/drive/folders/…",
                parent=self,
            )
            return
        webbrowser.open(url)

    def _verify_drive_configuration(self):
        name = self.operator_name_var.get().strip()
        root = self.drive_sync_root_var.get().strip()
        try:
            employee_root = ensure_cloud_folders(name, root)
        except (OSError, ValueError) as exc:
            self.cloud_status_var.set("")
            messagebox.showerror("No se pudo preparar Drive", str(exc), parent=self)
            return
        self.cloud_status_var.set(f"Listo: {employee_root}")
        messagebox.showinfo(
            "Respaldo preparado",
            "Se crearon o verificaron las carpetas de cotizaciones, reportes fotográficos y polígonos KML/KMZ.",
            parent=self,
        )

    def _unlock(self):
        digest = hashlib.sha256(self.password_var.get().encode("utf-8")).hexdigest()
        if digest != PASSWORD_SHA256:
            messagebox.showerror("Acceso denegado", "La contraseña no es correcta.", parent=self)
            self.password_var.set("")
            return
        self.password_var.set("")
        BrandingDialog(self, self.on_restart)


class PdfTextDialog(Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.withdraw()
        self.title("Textos de reportes y cotizaciones PDF")
        self.transient(master)
        self.profile_key = active_profile().key
        defaults = profile_defaults(self.profile_key)
        self.profile_vars = {
            key: StringVar(value=SETTINGS.get(f"branding.{self.profile_key}.{key}", defaults[key]))
            for key, _label in PROFILE_TEXT_FIELDS
        }
        current_report = report_texts()
        self.report_vars = {key: StringVar(value=current_report[key]) for key, _label in REPORT_TEXT_FIELDS}
        self.quote_vars = {
            code: {key: StringVar(value=value) for key, value in labels(code).items()}
            for code in ("es", "en")
        }
        self._build()
        self.bind("<Escape>", lambda _event: self.destroy())
        show_responsive_dialog(self, master, preferred_width=860)

    def _build(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 15))
        header.pack(fill="x")
        ttk.Label(header, text="Textos de los documentos PDF", style="HeaderTitle.TLabel").pack(anchor="w")
        profile_name = "TresVizo" if self.profile_key == "tresvizo" else "Grupo ITT"
        ttk.Label(
            header, text=f"Perfil activo: {profile_name} · Los cambios se aplican a documentos nuevos",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        actions = ttk.Frame(self, style="Dialog.TFrame", padding=(18, 10, 18, 15))
        actions.pack(fill="x", side="bottom")
        ttk.Button(actions, text="Restaurar valores originales", style="Secondary.TButton", command=self._restore).pack(side="left")
        ttk.Button(actions, text="Cancelar", style="Secondary.TButton", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Guardar textos", style="Accent.TButton", command=self._save).pack(side="right", padx=(0, 8))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=16, pady=14)
        self._fields_tab(notebook, "Identidad del PDF", PROFILE_TEXT_FIELDS, self.profile_vars)
        self._fields_tab(
            notebook, "Reporte fotográfico", REPORT_TEXT_FIELDS, self.report_vars,
            "Formato de fecha disponible: {day}, {month}, {year}. Escribe exactamente 12 meses separados por |.",
        )
        quote_fields = tuple((key, QUOTE_TEXT_FIELD_LABELS.get(key, key)) for key in LABELS["es"])
        self._fields_tab(
            notebook, "Cotización ES", quote_fields, self.quote_vars["es"],
            "Conserva {date} en Lugar y fecha, y {number} en Imagen de referencia.",
        )
        self._fields_tab(
            notebook, "Quotation EN", quote_fields, self.quote_vars["en"],
            "Keep {date} in Place and date, and {number} in Reference image.",
        )

    @staticmethod
    def _fields_tab(notebook, title, fields, variables, note=""):
        tab = ttk.Frame(notebook, style="Dialog.TFrame")
        notebook.add(tab, text=title)
        scroll = ScrollableDialogContent(tab, height=455, padding=16)
        scroll.pack(fill="both", expand=True)
        body = scroll.content
        if note:
            ttk.Label(body, text=note, style="Dialog.TLabel", foreground="#657887", wraplength=760, justify="left").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 12),
            )
            offset = 1
        else:
            offset = 0
        for row, (key, label) in enumerate(fields, offset):
            ttk.Label(body, text=label, style="Dialog.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            ttk.Entry(body, textvariable=variables[key]).grid(row=row, column=1, sticky="ew", pady=5)
        body.columnconfigure(1, weight=1)

    def _restore(self):
        if not messagebox.askyesno(
            "Restaurar textos", "¿Regresar todos los textos de estas cuatro pestañas a sus valores originales?",
            parent=self,
        ):
            return
        defaults = profile_defaults(self.profile_key)
        for key, variable in self.profile_vars.items():
            variable.set(defaults[key])
        for key, variable in self.report_vars.items():
            variable.set(REPORT_TEXT_DEFAULTS[key])
        for code in ("es", "en"):
            for key, variable in self.quote_vars[code].items():
                variable.set(LABELS[code][key])

    def _save(self):
        months = [part.strip() for part in self.report_vars["months"].get().split("|") if part.strip()]
        if len(months) != 12:
            messagebox.showwarning("Meses incompletos", "Escribe exactamente 12 meses separados por el símbolo |.", parent=self)
            return
        values = {
            f"branding.{self.profile_key}.{key}": variable.get().strip()
            for key, variable in self.profile_vars.items()
        }
        values["documents.report_texts"] = {key: variable.get().strip() for key, variable in self.report_vars.items()}
        values["documents.quote_texts.es"] = {key: variable.get().strip() for key, variable in self.quote_vars["es"].items()}
        values["documents.quote_texts.en"] = {key: variable.get().strip() for key, variable in self.quote_vars["en"].items()}
        SETTINGS.update(values)
        self.destroy()
        messagebox.showinfo("Textos guardados", "Los documentos nuevos usarán la personalización guardada.", parent=self.master)


class PaletteDialog(Toplevel):
    KEYS = (
        ("primary", "Color principal de barras"),
        ("accent", "Color de acento"),
        ("pale", "Fondo suave de etiquetas"),
        ("bar_text", "Texto sobre las barras"),
        ("ink", "Texto general"),
    )

    def __init__(self, master):
        super().__init__(master)
        self.withdraw()
        self.title("Paleta de cotizaciones PDF")
        self.transient(master)
        current = quote_palette()
        self.values = {
            "primary": current.primary_hex, "accent": current.accent_hex,
            "pale": current.pale_hex, "bar_text": current.bar_text_hex, "ink": current.ink_hex,
        }
        self.preset_var = StringVar(value=matching_preset(self.values))
        self.swatches = {}
        self._build()
        self._refresh()
        self.bind("<Escape>", lambda _event: self.destroy())
        show_responsive_dialog(self, master, preferred_width=630)

    def _build(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 15))
        header.pack(fill="x")
        ttk.Label(header, text="Paleta de cotizaciones", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(header, text="Los colores se aplican en la vista previa y en los PDF nuevos", style="HeaderSub.TLabel").pack(anchor="w", pady=(3, 0))

        actions = ttk.Frame(self, style="Dialog.TFrame", padding=(20, 10, 20, 16))
        actions.pack(fill="x", side="bottom")
        ttk.Button(actions, text="Cancelar", style="Secondary.TButton", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Guardar paleta", style="Accent.TButton", command=self._save).pack(side="right", padx=(0, 8))
        scroll = ScrollableDialogContent(self, height=420, padding=20)
        scroll.pack(fill="both", expand=True)
        body = scroll.content
        preset_row = ttk.Frame(body, style="Dialog.TFrame")
        preset_row.pack(fill="x")
        ttk.Label(preset_row, text="Paleta rápida", style="Dialog.TLabel").pack(side="left")
        combo = ttk.Combobox(
            preset_row, textvariable=self.preset_var,
            values=tuple(QUOTE_PALETTE_PRESETS) + ("Personalizada",), state="readonly", width=24,
        )
        combo.pack(side="right")
        combo.bind("<<ComboboxSelected>>", self._preset_changed)

        colors_box = ttk.Frame(body, style="Card.TFrame", padding=14)
        colors_box.pack(fill="x", pady=(14, 0))
        for row, (key, label) in enumerate(self.KEYS):
            ttk.Label(colors_box, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=5)
            swatch = Canvas(colors_box, width=68, height=24, highlightthickness=1, highlightbackground="#AAB8C2")
            swatch.grid(row=row, column=1, padx=10, pady=5)
            swatch.bind("<Button-1>", lambda _event, name=key: self._choose(name))
            self.swatches[key] = swatch
            ttk.Button(colors_box, text="Cambiar", style="Secondary.TButton", command=lambda name=key: self._choose(name)).grid(row=row, column=2, pady=5)
        colors_box.columnconfigure(0, weight=1)

        ttk.Label(body, text="VISTA PREVIA", style="DialogSection.TLabel").pack(anchor="w", pady=(14, 5))
        self.preview = Canvas(body, height=82, highlightthickness=1, highlightbackground="#B8C8D3")
        self.preview.pack(fill="x")
        self.preview.bind("<Configure>", lambda _event: self._refresh())

    def _preset_changed(self, _event=None):
        preset = QUOTE_PALETTE_PRESETS.get(self.preset_var.get())
        if preset:
            self.values.update(preset)
            self._refresh()

    def _choose(self, key):
        _rgb, chosen = colorchooser.askcolor(self.values[key], title=dict(self.KEYS)[key], parent=self)
        if chosen:
            self.values[key] = chosen.upper()
            self.preset_var.set(matching_preset(self.values))
            self._refresh()

    def _refresh(self):
        for key, canvas in self.swatches.items():
            canvas.configure(background=self.values[key])
        self.preview.delete("all")
        width = max(480, self.preview.winfo_width())
        self.preview.create_rectangle(0, 0, width, 32, fill=self.values["primary"], outline="")
        self.preview.create_text(width / 2, 16, text="COTIZACIÓN / QUOTATION", fill=self.values["bar_text"], font=("Segoe UI", 9, "bold"))
        self.preview.create_rectangle(0, 32, 145, 82, fill=self.values["pale"], outline="")
        self.preview.create_text(12, 48, text="CLIENTE / CLIENT", fill=self.values["primary"], anchor="w", font=("Segoe UI", 8, "bold"))
        self.preview.create_text(160, 48, text="Texto general de la cotización", fill=self.values["ink"], anchor="w", font=("Segoe UI", 9))
        self.preview.create_line(12, 70, width - 12, 70, fill=self.values["accent"], width=2)

    def _save(self):
        SETTINGS.update({f"documents.quote_palette.{key}": value for key, value in self.values.items()})
        self.destroy()


class BrandingDialog(Toplevel):
    def __init__(self, master, on_restart):
        super().__init__(master)
        self.withdraw()
        self.title("Identidad corporativa · Acceso restringido")
        self.transient(master)
        self.on_restart = on_restart
        self.enabled_var = BooleanVar(value=SETTINGS.get("branding.profile", "grupo_itt") == "tresvizo")
        stored_name = SETTINGS.get("branding.tresvizo.name", "TresVizo")
        self.name_var = StringVar(value="TresVizo" if str(stored_name).casefold() == "tresvizo" else stored_name)
        self.website_var = StringVar(value=SETTINGS.get("branding.tresvizo.website", "https://www.tresvizo.com/"))
        self.phone_var = StringVar(value=SETTINGS.get("branding.tresvizo.phone", "614 100 2069"))
        self.signer_var = StringVar(value=SETTINGS.get("branding.tresvizo.signer", "ING. EDGAR TREVIZO"))
        self.pending_logo = Path(SETTINGS.get("branding.tresvizo.custom_logo", "") or TRESVIZO_LOGO)
        if not self.pending_logo.exists():
            self.pending_logo = TRESVIZO_LOGO
        self._preview_photo = None
        self._initial = self._snapshot()
        self._build()
        self._refresh_preview()
        self.bind("<Escape>", lambda _event: self.destroy())
        show_responsive_dialog(self, master, preferred_width=680)

    def _snapshot(self):
        return (
            bool(self.enabled_var.get()), self.name_var.get().strip(), self.website_var.get().strip(),
            self.phone_var.get().strip(), self.signer_var.get().strip(), str(self.pending_logo),
        )

    def _build(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 15))
        header.pack(fill="x")
        ttk.Label(header, text="Identidad corporativa", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(header, text="Los cambios se aplican en toda la aplicación después de reiniciar", style="HeaderSub.TLabel").pack(anchor="w", pady=(3, 0))

        actions = ttk.Frame(self, style="Dialog.TFrame", padding=(20, 10, 20, 16))
        actions.pack(fill="x", side="bottom")
        ttk.Button(actions, text="Cancelar", style="Secondary.TButton", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Guardar y reiniciar", style="Accent.TButton", command=self._save).pack(side="right", padx=(0, 8))
        scroll = ScrollableDialogContent(self, height=430, padding=20)
        scroll.pack(fill="both", expand=True)
        body = scroll.content
        ttk.Checkbutton(body, text="Activar perfil corporativo TresVizo", variable=self.enabled_var, style="Dialog.TCheckbutton").pack(anchor="w", pady=(0, 14))

        profile = ttk.Frame(body, style="Card.TFrame", padding=16)
        profile.pack(fill="both", expand=True)
        preview_row = ttk.Frame(profile, style="Card.TFrame")
        preview_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.logo_preview = ttk.Label(preview_row, style="Card.TLabel", width=18, anchor="center")
        self.logo_preview.pack(side="left")
        logo_actions = ttk.Frame(preview_row, style="Card.TFrame")
        logo_actions.pack(side="left", padx=16)
        ttk.Label(logo_actions, text="LOGO DEL PERFIL", style="Field.Card.TLabel").pack(anchor="w")
        self.logo_name_label = ttk.Label(logo_actions, style="Card.TLabel")
        self.logo_name_label.pack(anchor="w", pady=(3, 8))
        ttk.Button(logo_actions, text="Seleccionar otro logo", style="Secondary.TButton", command=self._choose_logo).pack(anchor="w")
        ttk.Button(logo_actions, text="Usar logo TresVizo incluido", style="Secondary.TButton", command=self._use_default_logo).pack(anchor="w", pady=(6, 0))

        fields = (
            ("Nombre de la empresa", self.name_var),
            ("Página web", self.website_var),
            ("Teléfono / referencia", self.phone_var),
            ("Firma predeterminada de cotizaciones", self.signer_var),
        )
        for row, (label, variable) in enumerate(fields, 1):
            ttk.Label(profile, text=label, style="Field.Card.TLabel").grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(profile, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Label(profile, text="La dirección se omite en los documentos del perfil TresVizo.", style="Hint.Card.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        profile.columnconfigure(1, weight=1)

    def _choose_logo(self):
        chosen = filedialog.askopenfilename(parent=self, title="Seleccionar logo corporativo", filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.webp")])
        if chosen:
            self.pending_logo = Path(chosen)
            self._refresh_preview()

    def _use_default_logo(self):
        self.pending_logo = TRESVIZO_LOGO
        self._refresh_preview()

    def _refresh_preview(self):
        self.logo_name_label.configure(text=self.pending_logo.name)
        try:
            with Image.open(self.pending_logo) as source:
                image = source.convert("RGBA")
                image.thumbnail((120, 105), Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(image)
            self.logo_preview.configure(image=self._preview_photo, text="")
        except Exception:
            self.logo_preview.configure(image="", text="No disponible")

    def _save(self):
        website = self.website_var.get().strip() or "https://www.tresvizo.com/"
        signer = self.signer_var.get().strip() or "ING. EDGAR TREVIZO"
        custom_logo = ""
        if self.pending_logo.resolve() != TRESVIZO_LOGO.resolve():
            suffix = self.pending_logo.suffix.lower() if self.pending_logo.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
            target = category_dir("config") / f"logo_corporativo_tresvizo{suffix}"
            if self.pending_logo.resolve() != target.resolve():
                shutil.copy2(self.pending_logo, target)
            custom_logo = str(target)
        SETTINGS.update({
            "branding.profile": "tresvizo" if self.enabled_var.get() else "grupo_itt",
            "branding.tresvizo.name": self.name_var.get().strip() or "TresVizo",
            "branding.tresvizo.website": website,
            "branding.tresvizo.phone": self.phone_var.get().strip() or "614 100 2069",
            "branding.tresvizo.signer": signer,
            "branding.tresvizo.custom_logo": custom_logo,
        })
        changed = self._snapshot() != self._initial or active_profile().key != ("tresvizo" if self.enabled_var.get() else "grupo_itt")
        self.destroy()
        if changed:
            messagebox.showinfo("Identidad actualizada", "La aplicación se reiniciará para aplicar el logo y los datos corporativos en todas las herramientas.", parent=self.master)
            self.master.destroy()
            self.on_restart()
