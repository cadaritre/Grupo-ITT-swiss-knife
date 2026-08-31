from __future__ import annotations

import os
import queue
import re
import threading
from pathlib import Path
from tkinter import END, BooleanVar, Canvas, DoubleVar, IntVar, StringVar, Text, Toplevel, filedialog, messagebox, simpledialog
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk

from .app_storage import backup_editable, cache_dir, category_dir, managed_input_copy, preserve_artifact
from .branding import normalize_quote_signer, signer_for_new_quote
from .quotation_i18n import translate_standard_quote_values
from .quotation_models import CONCEPT_TEMPLATES, QuoteData, QuoteImage, QuoteItem, clone_template
from .quotation_pdf import generate_quote_pdf, money
from .quotation_templates import CustomConceptTemplateStore
from .spellcheck import BilingualSpellService, ReviewField, SpellReviewDialog
from .ux_components import attach_tooltip, show_responsive_dialog


QUOTE_LANGUAGE_LABELS = {"es": "Español", "en": "English"}
QUOTE_LANGUAGE_CODES = {label: code for code, label in QUOTE_LANGUAGE_LABELS.items()}
SPELLING_QUOTE_FIELDS = {
    "client_name", "contact", "location", "project_title", "delivery_time",
    "payment_terms", "prepared_by",
}


def _safe_filename_part(value: str, fallback: str = "SIN NOMBRE") -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value or "")
    clean = " ".join(clean.split()).strip(" .")
    return clean[:90] or fallback


def quote_export_stem(quote: QuoteData) -> str:
    folio = _safe_filename_part(quote.quote_number, "SIN FOLIO")
    title = _safe_filename_part(quote.project_title, "SERVICIOS")
    return f"COTIZACION {folio} - {title}"


def editable_json_path(pdf_path: str | Path) -> Path:
    pdf = Path(pdf_path)
    stem = pdf.stem if pdf.stem.upper().startswith("COTIZACION") else f"COTIZACION {pdf.stem}"
    return pdf.with_name(f"EDITABLE_{stem}.json")


class QuotationTool(ttk.Frame):
    def __init__(self, master, logo_path: Path, on_home):
        super().__init__(master, style="App.TFrame")
        self.logo_path = logo_path
        self.on_home = on_home
        self.quote = QuoteData(prepared_by=signer_for_new_quote(), items=[clone_template("Levantamiento topográfico")])
        self.json_path: Path | None = None
        self._editing_item: int | None = None
        self._editing_image: int | None = None
        self._loading = False
        self._spell_loading = False
        self._spell_service = None
        self._spell_results = queue.Queue()
        self._recommended_field_widgets = {}
        self._recommended_warning_state = {}
        self._recommended_pending_count = None
        self._preview_job = None
        self._item_commit_job = None
        self._preview_pdf = cache_dir("previews") / "grupoitt_quote_preview.pdf"
        self._preview_page = 0
        self._preview_pages = 0
        self._preview_photo = None
        self._preview_resize_job = None
        self._large_preview_window = None
        self.status_var = StringVar(value="Cotización nueva")
        self.template_store = CustomConceptTemplateStore()
        self._custom_template_choices = {}
        self.template_var = StringVar()
        self._make_vars()
        self._build()
        self._refresh_template_choices()
        self._load_quote_to_form()
        self._preview_job = self.after(350, self._run_scheduled_preview)

    def _make_vars(self):
        fields = ["quote_number", "quote_date", "client_name", "contact", "email", "phone", "location", "project_title", "currency", "delivery_time", "payment_terms", "bank", "clabe", "account", "prepared_by"]
        self.vars = {field: StringVar() for field in fields}
        self.vars["include_vat"] = BooleanVar(value=True)
        self.vars["vat_rate"] = DoubleVar(value=16.0)
        self.vars["advance_percent"] = DoubleVar(value=50.0)
        self.vars["validity_days"] = IntVar(value=15)
        for field, variable in self.vars.items():
            variable.trace_add("write", lambda *_, name=field: self._changed(name in SPELLING_QUOTE_FIELDS))
        self.language_var = StringVar(value=QUOTE_LANGUAGE_LABELS["es"])
        self.language_var.trace_add("write", self._language_changed)
        self.item_vars = {
            "title": StringVar(), "unit": StringVar(value="Lote"), "quantity": StringVar(value="1"),
            "unit_price": StringVar(value="0"),
        }
        for field, variable in self.item_vars.items():
            variable.trace_add("write", lambda *_, name=field: self._item_changed(name == "title"))
        self.image_caption_var = StringVar()
        self.image_caption_var.trace_add("write", lambda *_: self._image_changed())
        self.subitems_count_var = StringVar(value="0 alcances")
        self.item_save_status_var = StringVar(value="✓ Aplicado automático")

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, style="Header.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        toolbar = ttk.Frame(header, style="Header.TFrame", padding=(18, 10))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="‹ Herramientas", style="HeaderButton.TButton", command=self.on_home).pack(side="left")
        ttk.Label(toolbar, text="Cotizaciones", style="HeaderTitle.TLabel").pack(side="left", padx=16)
        ttk.Button(toolbar, text="Nueva", style="HeaderButton.TButton", command=self.new_quote).pack(side="left", padx=(10, 2))
        ttk.Button(toolbar, text="Abrir JSON", style="HeaderButton.TButton", command=self.open_json).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Guardar JSON", style="HeaderButton.TButton", command=self.save_json).pack(side="left", padx=2)
        ttk.Label(toolbar, text="Idioma", style="HeaderSub.TLabel").pack(side="left", padx=(9, 3))
        self.language_combo = ttk.Combobox(
            toolbar, textvariable=self.language_var, values=tuple(QUOTE_LANGUAGE_CODES),
            state="readonly", width=9,
        )
        self.language_combo.pack(side="left", padx=(0, 4))
        self.spell_button = ttk.Button(toolbar, text="Revisar ortografía", style="HeaderButton.TButton", command=self.start_spellcheck)
        self.spell_button.pack(side="left", padx=2)
        self.spelling_status_var = StringVar(value="⚠ Ortografía pendiente")
        self.spelling_status_label = ttk.Label(
            toolbar, textvariable=self.spelling_status_var, style="HeaderWarning.TLabel", cursor="hand2",
        )
        attach_tooltip(self.spelling_status_label, "Haz clic para revisar la ortografía. Cualquier cambio de texto vuelve a marcarla como pendiente.")
        self.spelling_status_label.bind("<Button-1>", lambda _event: self.start_spellcheck())
        ttk.Button(toolbar, text="Exportar PDF", style="HeaderAccent.TButton", command=self.export_pdf).pack(side="right")
        self.spelling_status_label.pack(side="right", padx=(7, 4))
        status_bar = ttk.Frame(header, style="StatusBar.TFrame", padding=(18, 5))
        status_bar.pack(fill="x")
        self.quote_status_label = ttk.Label(
            status_bar, textvariable=self.status_var, style="StatusBar.TLabel",
            anchor="w", justify="left",
        )
        self.quote_status_label.pack(fill="x")
        header.bind("<Configure>", self._header_resized)

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        editor = ttk.Frame(panes, style="Card.TFrame", padding=10)
        preview = ttk.Frame(panes, style="Card.TFrame", padding=12, width=430)
        panes.add(editor, weight=3)
        panes.add(preview, weight=1)
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(editor)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self._build_client_tab()
        self._build_items_tab()
        self._build_images_tab()
        self._build_terms_tab()
        self._build_preview(preview)

    def _header_resized(self, event):
        if hasattr(self, "quote_status_label"):
            self.quote_status_label.configure(wraplength=max(280, event.width - 36))
        compact = event.width < 1180
        if getattr(self, "_compact_header", None) != compact:
            self._compact_header = compact
            self.spell_button.configure(text="Ortografía" if compact else "Revisar ortografía")
            self._update_spelling_indicator()

    def _tab(self, title):
        frame = ttk.Frame(self.notebook, style="Card.TFrame", padding=18)
        self.notebook.add(frame, text=title)
        return frame

    def _label_entry(self, parent, row, col, label, variable, colspan=1, recommended_key=None):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=row, column=col, columnspan=colspan, sticky="ew", padx=6, pady=6)
        label_widget = ttk.Label(box, text=label, style="Field.Card.TLabel")
        label_widget.pack(anchor="w")
        entry = ttk.Entry(box, textvariable=variable)
        entry.pack(fill="x", pady=(4, 0))
        if recommended_key:
            self._recommended_field_widgets[recommended_key] = (entry, label_widget, label)
        return entry

    def _build_client_tab(self):
        tab = self._tab("Cliente y proyecto")
        self.client_tab = tab
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text="DATOS GENERALES", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=6, pady=(0, 8))
        self.recommended_summary_var = StringVar(value="")
        self.recommended_summary_label = ttk.Label(
            tab, textvariable=self.recommended_summary_var, style="FieldWarning.Card.TLabel",
        )
        self.recommended_summary_label.grid(row=0, column=1, sticky="e", padx=6, pady=(0, 8))
        self._label_entry(tab, 1, 0, "Folio de cotización", self.vars["quote_number"], recommended_key="quote_number")
        self._label_entry(tab, 1, 1, "Fecha (DD/MM/AAAA)", self.vars["quote_date"])
        self._label_entry(tab, 2, 0, "Nombre / Empresa", self.vars["client_name"], recommended_key="client_name")
        self._label_entry(tab, 2, 1, "Contacto", self.vars["contact"], recommended_key="contact")
        self._label_entry(tab, 3, 0, "Correo", self.vars["email"], recommended_key="email")
        self._label_entry(tab, 3, 1, "Teléfono", self.vars["phone"], recommended_key="phone")
        self._label_entry(tab, 4, 0, "Localización del servicio", self.vars["location"], 2, "location")
        self._label_entry(tab, 5, 0, "Título / concepto general", self.vars["project_title"], 2, "project_title")
        ttk.Label(tab, text="NOTAS GENERALES", style="Section.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=(18, 5))
        self.notes_text = Text(tab, height=7, wrap="word", font=("Segoe UI", 10), relief="solid", bd=1, padx=8, pady=7)
        self.notes_text.grid(row=7, column=0, columnspan=2, sticky="nsew", padx=6, pady=6)
        self.notes_text.bind("<KeyRelease>", lambda _: self._changed(True))
        tab.rowconfigure(7, weight=1)

    def _build_items_tab(self):
        tab = self._tab("Conceptos")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=2)
        tab.rowconfigure(3, weight=3)
        tools = ttk.Frame(tab, style="Card.TFrame")
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        template_row = ttk.Frame(tools, style="Card.TFrame")
        template_row.pack(fill="x")
        self.template_combo = ttk.Combobox(template_row, textvariable=self.template_var, state="readonly", width=29)
        self.template_combo.pack(side="left")
        ttk.Button(template_row, text="Insertar machote", style="Accent.TButton", command=self.insert_template).pack(side="left", padx=6)
        ttk.Button(template_row, text="Administrar machotes…", style="Secondary.TButton", command=self.manage_templates).pack(side="right")
        item_row = ttk.Frame(tools, style="Card.TFrame")
        item_row.pack(fill="x", pady=(6, 0))
        ttk.Button(item_row, text="+ Concepto vacío", style="Secondary.TButton", command=self.add_item).pack(side="left")
        ttk.Button(item_row, text="Guardar actual como machote", style="Secondary.TButton", command=self.save_selected_template).pack(side="left", padx=(6, 0))
        ttk.Button(item_row, text="Eliminar", style="Secondary.TButton", command=self.delete_item).pack(side="right")
        ttk.Button(item_row, text="Copiar", style="Secondary.TButton", command=self.duplicate_item).pack(side="right", padx=4)
        ttk.Button(item_row, text="↓", width=3, style="Secondary.TButton", command=lambda: self.move_item(1)).pack(side="right", padx=(4, 0))
        ttk.Button(item_row, text="↑", width=3, style="Secondary.TButton", command=lambda: self.move_item(-1)).pack(side="right")

        listbox = ttk.Frame(tab, style="Card.TFrame")
        listbox.grid(row=1, column=0, sticky="nsew")
        listbox.columnconfigure(0, weight=1)
        listbox.rowconfigure(0, weight=1)
        self.items_tree = ttk.Treeview(listbox, columns=("number", "title", "unit", "qty", "price", "amount"), show="headings", selectmode="browse", height=7)
        cols = [("number", "#", 38, False), ("title", "Concepto", 300, True), ("unit", "Unidad", 70, False), ("qty", "Cant.", 60, False), ("price", "P.U.", 105, False), ("amount", "Importe", 110, False)]
        for key, label, width, stretch in cols:
            self.items_tree.heading(key, text=label)
            self.items_tree.column(key, width=width, anchor="w" if key == "title" else "center", stretch=stretch)
        ybar = ttk.Scrollbar(listbox, orient="vertical", command=self.items_tree.yview)
        self.items_tree.configure(yscrollcommand=ybar.set)
        self.items_tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        self.items_tree.bind("<<TreeviewSelect>>", self._select_item)

        ttk.Label(tab, text="EDITAR CONCEPTO SELECCIONADO", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 5))
        edit = ttk.Frame(tab, style="Soft.TFrame", padding=12)
        edit.grid(row=3, column=0, sticky="nsew")
        edit.columnconfigure(0, weight=3)
        edit.columnconfigure(1, weight=2)
        edit.rowconfigure(1, weight=1)
        left = ttk.Frame(edit, style="Soft.TFrame")
        right = ttk.Frame(edit, style="Soft.TFrame")
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)
        ttk.Label(left, text="Título", style="SoftSection.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.item_vars["title"]).grid(row=1, column=0, sticky="ew", pady=(3, 7))
        ttk.Label(left, text="Descripción", style="SoftSection.TLabel").grid(row=2, column=0, sticky="w")
        self.item_description = Text(left, height=4, wrap="word", font=("Segoe UI", 9), relief="flat", padx=7, pady=6)
        self.item_description.grid(row=3, column=0, sticky="nsew", pady=(3, 0))
        self.item_description.bind("<KeyRelease>", lambda _: self._item_changed())
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        subheading = ttk.Frame(right, style="Soft.TFrame")
        subheading.grid(row=0, column=0, sticky="ew")
        ttk.Label(subheading, text="Subconceptos / alcances", style="SoftSection.TLabel").pack(side="left")
        ttk.Label(subheading, textvariable=self.subitems_count_var, style="SoftHint.TLabel").pack(side="right")
        subbox = ttk.Frame(right, style="Soft.TFrame")
        subbox.grid(row=1, column=0, sticky="nsew", pady=(3, 7))
        subbox.columnconfigure(0, weight=1)
        subbox.rowconfigure(0, weight=1)
        self.subitems_text = Text(
            subbox, height=6, wrap="word", font=("Segoe UI", 9), relief="solid", bd=1,
            padx=5, pady=4, background="#FFFFFF", foreground="#263746",
            insertbackground="#173B5F", selectbackground="#B9DDEB",
        )
        self.subitems_text.tag_configure("subitem_even", background="#FFFFFF", lmargin1=7, lmargin2=21, spacing1=4, spacing3=4)
        self.subitems_text.tag_configure("subitem_odd", background="#E7F2F7", lmargin1=7, lmargin2=21, spacing1=4, spacing3=4)
        subbar = ttk.Scrollbar(subbox, orient="vertical", command=self.subitems_text.yview)
        self.subitems_text.configure(yscrollcommand=subbar.set)
        self.subitems_text.grid(row=0, column=0, sticky="nsew")
        subbar.grid(row=0, column=1, sticky="ns")
        self.subitems_text.bind("<Return>", self._subitems_newline)
        self.subitems_text.bind("<KeyRelease>", self._subitems_edited)
        self.subitems_text.bind("<<Paste>>", lambda _event: self.after_idle(self._style_subitem_lines))
        ttk.Label(
            right,
            text="Cada renglón se exporta como un alcance independiente · Enter crea uno nuevo.",
            style="SoftHint.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 6))
        numbers = ttk.Frame(right, style="Soft.TFrame")
        numbers.grid(row=3, column=0, sticky="ew")
        for col in range(4):
            numbers.columnconfigure(col, weight=1)
        for col, (label, key) in enumerate([("Unidad", "unit"), ("Cantidad", "quantity"), ("Precio unit.", "unit_price")]):
            box = ttk.Frame(numbers, style="Soft.TFrame")
            box.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 4, 0))
            ttk.Label(box, text=label, style="SoftHint.TLabel").pack(anchor="w")
            ttk.Entry(box, textvariable=self.item_vars[key], width=10).pack(fill="x", pady=(2, 0))
        ttk.Label(
            numbers,
            textvariable=self.item_save_status_var,
            style="SoftHint.TLabel",
            anchor="center",
        ).grid(row=0, column=3, sticky="sew", padx=(7, 0), pady=(17, 7))

    def _build_images_tab(self):
        tab = self._tab("Imágenes")
        tab.columnconfigure(0, weight=2)
        tab.columnconfigure(1, weight=3)
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, text="IMÁGENES DE REFERENCIA", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        buttons = ttk.Frame(tab, style="Card.TFrame")
        buttons.grid(row=0, column=1, sticky="e")
        ttk.Button(buttons, text="+ Agregar imágenes", style="Accent.TButton", command=self.add_images).pack(side="left")
        ttk.Button(buttons, text="Eliminar", style="Secondary.TButton", command=self.delete_image).pack(side="left", padx=(6, 0))
        listing = ttk.Frame(tab, style="Card.TFrame")
        listing.grid(row=1, column=0, sticky="nsew", pady=(10, 0), padx=(0, 12))
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.images_tree = ttk.Treeview(listing, columns=("number", "file", "caption"), show="headings", selectmode="browse")
        for key, label, width in [("number", "#", 35), ("file", "Archivo", 160), ("caption", "Descripción", 190)]:
            self.images_tree.heading(key, text=label)
            self.images_tree.column(key, width=width, stretch=key != "number")
        self.images_tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(listing, orient="vertical", command=self.images_tree.yview)
        self.images_tree.configure(yscrollcommand=bar.set)
        bar.grid(row=0, column=1, sticky="ns")
        self.images_tree.bind("<<TreeviewSelect>>", self._select_image)
        detail = ttk.Frame(tab, style="Soft.TFrame", padding=14)
        detail.grid(row=1, column=1, sticky="nsew", pady=(10, 0))
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(0, weight=1)
        self.image_preview = ttk.Label(detail, text="Selecciona una imagen", style="Soft.TLabel", anchor="center")
        self.image_preview.grid(row=0, column=0, sticky="nsew")
        ttk.Label(detail, text="Descripción de la imagen", style="SoftSection.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 2))
        ttk.Entry(detail, textvariable=self.image_caption_var).grid(row=2, column=0, sticky="ew")

    def _build_terms_tab(self):
        tab = self._tab("Precios y condiciones")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text="IMPUESTOS Y CONDICIONES", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 8))
        vat = ttk.Frame(tab, style="Soft.TFrame", padding=14)
        vat.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        ttk.Checkbutton(vat, text="Agregar IVA", variable=self.vars["include_vat"]).pack(side="left")
        ttk.Label(vat, text="Tasa %", style="Soft.TLabel").pack(side="left", padx=(20, 5))
        ttk.Entry(vat, textvariable=self.vars["vat_rate"], width=8).pack(side="left")
        ttk.Label(vat, text="Anticipo %", style="Soft.TLabel").pack(side="left", padx=(20, 5))
        ttk.Entry(vat, textvariable=self.vars["advance_percent"], width=8).pack(side="left")
        ttk.Label(vat, text="Vigencia (días)", style="Soft.TLabel").pack(side="left", padx=(20, 5))
        ttk.Entry(vat, textvariable=self.vars["validity_days"], width=8).pack(side="left")
        self._label_entry(tab, 2, 0, "Moneda", self.vars["currency"])
        self._label_entry(tab, 2, 1, "Tiempo de entrega", self.vars["delivery_time"])
        self._label_entry(tab, 3, 0, "Formas de pago", self.vars["payment_terms"], 2)
        self._label_entry(tab, 4, 0, "Banco", self.vars["bank"])
        self._label_entry(tab, 4, 1, "CLABE", self.vars["clabe"])
        self._label_entry(tab, 5, 0, "Número de cuenta", self.vars["account"])
        self._label_entry(tab, 5, 1, "Elaboró / firma", self.vars["prepared_by"])

    def _build_preview(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        preview_heading = ttk.Frame(parent, style="Card.TFrame")
        preview_heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(preview_heading, text="VISTA PREVIA DEL PDF", style="Section.TLabel").pack(side="left")
        ttk.Button(
            preview_heading,
            text="⛶ Ampliar",
            style="Secondary.TButton",
            command=self.open_large_preview,
        ).pack(side="right")
        totals = ttk.Frame(parent, style="Soft.TFrame", padding=9)
        totals.grid(row=1, column=0, sticky="ew", pady=(8, 10))
        self.totals_label = ttk.Label(totals, text="", style="Soft.TLabel", justify="left")
        self.totals_label.pack(anchor="w")
        self.pdf_preview = ttk.Label(parent, text="Generando vista previa...", style="Preview.TLabel", anchor="center")
        self.pdf_preview.grid(row=2, column=0, sticky="nsew")
        self.pdf_preview.bind("<Configure>", self._preview_resized)
        self.pdf_preview.bind("<Double-1>", lambda _event: self.open_large_preview())
        self.pdf_preview.configure(cursor="hand2")
        nav = ttk.Frame(parent, style="Card.TFrame")
        nav.grid(row=3, column=0, sticky="ew", pady=(9, 0))
        ttk.Button(nav, text="‹ Anterior", style="Secondary.TButton", command=lambda: self.change_page(-1)).pack(side="left")
        self.page_label = ttk.Label(nav, text="Página 0 de 0", style="Hint.Card.TLabel")
        self.page_label.pack(side="left", expand=True)
        ttk.Button(nav, text="Siguiente ›", style="Secondary.TButton", command=lambda: self.change_page(1)).pack(side="right")
        ttk.Button(parent, text="Actualizar vista previa", style="Accent.TButton", command=self.refresh_preview).grid(row=4, column=0, sticky="ew", pady=(9, 0))

    def _changed(self, spelling_content=True):
        if self._loading:
            return
        if spelling_content:
            self._mark_spelling_dirty()
        self._refresh_recommended_warnings()
        self.status_var.set("Cambios sin guardar")
        self.schedule_preview()

    def _refresh_recommended_warnings(self):
        if not self._recommended_field_widgets:
            return
        values = {key: self.vars[key].get().strip() for key in self._recommended_field_widgets}
        contact_method_missing = not values.get("email") and not values.get("phone")
        missing = {
            "quote_number": not values.get("quote_number"),
            "client_name": not values.get("client_name"),
            "contact": not values.get("contact"),
            "email": contact_method_missing,
            "phone": contact_method_missing,
            "location": not values.get("location"),
            "project_title": not values.get("project_title"),
        }
        for key, (entry, label_widget, base_label) in self._recommended_field_widgets.items():
            pending = bool(missing.get(key))
            if pending and key in {"email", "phone"}:
                suffix = " · ⚠ correo o teléfono"
            else:
                suffix = " · ⚠ recomendado" if pending else ""
            visual_state = (pending, suffix)
            # Reapplying a ttk.Entry style on every character can leave the
            # Windows caret painted at its previous position under display scaling.
            if self._recommended_warning_state.get(key) != visual_state:
                entry.configure(style="Warning.TEntry" if pending else "TEntry")
                label_widget.configure(
                    text=base_label + suffix,
                    style="FieldWarning.Card.TLabel" if pending else "Field.Card.TLabel",
                )
                self._recommended_warning_state[key] = visual_state
        logical_pending = sum((
            missing["quote_number"], missing["client_name"], missing["contact"],
            contact_method_missing, missing["location"], missing["project_title"],
        ))
        if self._recommended_pending_count != logical_pending:
            if logical_pending:
                self.recommended_summary_var.set(f"⚠ {logical_pending} datos recomendados pendientes")
                self.recommended_summary_label.configure(style="FieldWarning.Card.TLabel")
                self.notebook.tab(self.client_tab, text=f"Cliente y proyecto  ⚠ {logical_pending}")
            else:
                self.recommended_summary_var.set("✓ Datos recomendados completos")
                self.recommended_summary_label.configure(style="FieldOk.Card.TLabel")
                self.notebook.tab(self.client_tab, text="Cliente y proyecto")
            self._recommended_pending_count = logical_pending

    def _item_changed(self, spelling_content=True):
        if not self._loading and self._editing_item is not None:
            if spelling_content:
                self._mark_spelling_dirty()
            self.item_save_status_var.set("Actualizando…")
            self.status_var.set("Actualizando concepto…")
            self._schedule_item_commit()
            self.schedule_preview()

    def _schedule_item_commit(self):
        """Update the data model after typing pauses, without rebuilding the PDF."""
        if self._item_commit_job:
            self.after_cancel(self._item_commit_job)
        self._item_commit_job = self.after(280, self._commit_item_debounced)

    def _cancel_item_commit(self):
        if self._item_commit_job:
            try:
                self.after_cancel(self._item_commit_job)
            except Exception:
                pass
            self._item_commit_job = None

    def _commit_item_debounced(self):
        self._item_commit_job = None
        if self._loading or self._editing_item is None:
            return
        index = self._editing_item
        self.commit_item(refresh=False)
        self._update_item_tree_row(index)
        self.item_save_status_var.set("✓ Aplicado automático")
        self.status_var.set("Concepto aplicado automáticamente")

    def _language_changed(self, *_args):
        if self._loading:
            return
        language = QUOTE_LANGUAGE_CODES.get(self.language_var.get(), "es")
        if language != self.quote.language:
            self._sync_quote()
            translate_standard_quote_values(self.quote, language)
            self.quote.language = language
            self._spell_service = None
            self._mark_spelling_dirty()
            self._load_quote_to_form()
            self.status_var.set(f"Idioma de la cotización: {QUOTE_LANGUAGE_LABELS[language]}")
            self.schedule_preview()

    def _mark_spelling_dirty(self):
        self.quote.spelling_checked = False
        self._update_spelling_indicator()

    def _update_spelling_indicator(self):
        if not hasattr(self, "spelling_status_label"):
            return
        compact = bool(getattr(self, "_compact_header", False))
        if self.quote.spelling_checked:
            self.spelling_status_var.set("✓" if compact else "✓ Ortografía revisada")
            self.spelling_status_label.configure(style="HeaderOk.TLabel")
        else:
            self.spelling_status_var.set("⚠" if compact else "⚠ Ortografía pendiente")
            self.spelling_status_label.configure(style="HeaderWarning.TLabel")

    def destroy(self):
        if self._large_preview_window is not None:
            try:
                if self._large_preview_window.winfo_exists():
                    self._large_preview_window.destroy()
            except Exception:
                pass
            self._large_preview_window = None
        for attribute in ("_preview_job", "_preview_resize_job", "_item_commit_job"):
            job = getattr(self, attribute, None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attribute, None)
        super().destroy()

    @staticmethod
    def _clean_subitem_line(line: str) -> str:
        return re.sub(r"^\s*(?:[•\-–—]|\d+[.)])\s*", "", line).strip()

    def _subitems_newline(self, _event=None):
        try:
            self.subitems_text.delete("sel.first", "sel.last")
        except Exception:
            pass
        self.subitems_text.insert("insert", "\n• ")
        self._style_subitem_lines()
        self._item_changed()
        return "break"

    def _subitems_edited(self, _event=None):
        self._style_subitem_lines()
        self._item_changed()

    def _style_subitem_lines(self):
        for tag in ("subitem_even", "subitem_odd"):
            self.subitems_text.tag_remove(tag, "1.0", END)
        raw_lines = self.subitems_text.get("1.0", "end-1c").splitlines()
        count = 0
        for line_number, raw in enumerate(raw_lines, 1):
            if self._clean_subitem_line(raw):
                tag = "subitem_even" if count % 2 == 0 else "subitem_odd"
                self.subitems_text.tag_add(tag, f"{line_number}.0", f"{line_number + 1}.0")
                count += 1
        self.subitems_count_var.set(f"{count} alcance" + ("" if count == 1 else "s"))

    def _image_changed(self):
        if not self._loading and self._editing_image is not None:
            self._mark_spelling_dirty()
            self.quote.images[self._editing_image].caption = self.image_caption_var.get()
            image = self.quote.images[self._editing_image]
            iid = str(self._editing_image)
            if self.images_tree.exists(iid):
                self.images_tree.item(iid, values=(self._editing_image + 1, Path(image.path).name, image.caption))
            self.schedule_preview()

    def schedule_preview(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(800, self._run_scheduled_preview)

    def _run_scheduled_preview(self):
        self._preview_job = None
        self.refresh_preview()

    def _float(self, value, default=0.0):
        try:
            return float(str(value).replace(",", "").strip())
        except ValueError:
            return default

    def _sync_quote(self):
        self.commit_item(refresh=False)
        for field in ["quote_number", "quote_date", "client_name", "contact", "email", "phone", "location", "project_title", "currency", "delivery_time", "payment_terms", "bank", "clabe", "account", "prepared_by"]:
            setattr(self.quote, field, self.vars[field].get().strip())
        self.quote.include_vat = bool(self.vars["include_vat"].get())
        self.quote.vat_rate = self._float(self.vars["vat_rate"].get(), 16)
        self.quote.advance_percent = self._float(self.vars["advance_percent"].get(), 50)
        self.quote.validity_days = int(self._float(self.vars["validity_days"].get(), 15))
        self.quote.notes = self.notes_text.get("1.0", "end-1c").strip()
        self.quote.language = QUOTE_LANGUAGE_CODES.get(self.language_var.get(), "es")

    def _load_quote_to_form(self):
        self._cancel_item_commit()
        self._loading = True
        try:
            for field in ["quote_number", "quote_date", "client_name", "contact", "email", "phone", "location", "project_title", "currency", "delivery_time", "payment_terms", "bank", "clabe", "account", "prepared_by"]:
                self.vars[field].set(getattr(self.quote, field))
            self.vars["include_vat"].set(self.quote.include_vat)
            self.vars["vat_rate"].set(self.quote.vat_rate)
            self.vars["advance_percent"].set(self.quote.advance_percent)
            self.vars["validity_days"].set(self.quote.validity_days)
            self.language_var.set(QUOTE_LANGUAGE_LABELS.get(self.quote.language, "Español"))
            self.notes_text.delete("1.0", END)
            self.notes_text.insert("1.0", self.quote.notes)
            self._refresh_items_tree(select=0 if self.quote.items else None)
            self._refresh_images_tree()
        finally:
            self._loading = False
        self._refresh_recommended_warnings()
        self._update_spelling_indicator()

    def _refresh_items_tree(self, select=None):
        self.items_tree.delete(*self.items_tree.get_children())
        for i, item in enumerate(self.quote.items):
            self.items_tree.insert("", END, iid=str(i), values=(i + 1, item.title, item.unit, f"{item.quantity:g}", money(item.unit_price), money(item.amount)))
        if select is not None and self.quote.items:
            select = max(0, min(select, len(self.quote.items) - 1))
            self.items_tree.selection_set(str(select))
            self.items_tree.focus(str(select))

    def _update_item_tree_row(self, index):
        if not (0 <= index < len(self.quote.items)):
            return
        iid = str(index)
        if not self.items_tree.exists(iid):
            return
        item = self.quote.items[index]
        self.items_tree.item(
            iid,
            values=(index + 1, item.title, item.unit, f"{item.quantity:g}", money(item.unit_price), money(item.amount)),
        )

    def _select_item(self, _event=None):
        selected = self.items_tree.selection()
        if not selected:
            return
        old = self._editing_item
        if old is not None and old != int(selected[0]):
            self.commit_item(refresh=False)
        self._editing_item = int(selected[0])
        item = self.quote.items[self._editing_item]
        self._loading = True
        try:
            for key in self.item_vars:
                value = getattr(item, key)
                self.item_vars[key].set(f"{value:g}" if isinstance(value, float) else value)
            self.item_description.delete("1.0", END)
            self.item_description.insert("1.0", item.description)
            self.subitems_text.delete("1.0", END)
            self.subitems_text.insert("1.0", "\n".join(f"• {subitem}" for subitem in item.subitems))
            self._style_subitem_lines()
        finally:
            self._loading = False

    def commit_item(self, refresh=True):
        self._cancel_item_commit()
        if self._editing_item is None or not (0 <= self._editing_item < len(self.quote.items)):
            return
        item = self.quote.items[self._editing_item]
        item.title = self.item_vars["title"].get().strip() or "Concepto sin título"
        item.unit = self.item_vars["unit"].get().strip() or "Lote"
        item.quantity = self._float(self.item_vars["quantity"].get(), 1)
        item.unit_price = self._float(self.item_vars["unit_price"].get())
        item.description = self.item_description.get("1.0", "end-1c").strip()
        item.subitems = [
            clean for line in self.subitems_text.get("1.0", "end-1c").splitlines()
            if (clean := self._clean_subitem_line(line))
        ]
        if refresh:
            self._update_item_tree_row(self._editing_item)
            self.item_save_status_var.set("✓ Aplicado automático")
            self.schedule_preview()

    def add_item(self):
        self.commit_item(refresh=False)
        self.quote.items.append(QuoteItem())
        self._mark_spelling_dirty()
        self._refresh_items_tree(select=len(self.quote.items) - 1)
        self.status_var.set("Concepto agregado")
        self.schedule_preview()

    def _refresh_template_choices(self, select_id=None):
        current = self.template_var.get()
        choices = list(CONCEPT_TEMPLATES)
        self._custom_template_choices = {}
        for template in self.template_store.templates:
            language = "ES" if template.language == "es" else "EN"
            label = f"★ {template.name} · {language}"
            self._custom_template_choices[label] = template.template_id
            choices.append(label)
        self.template_combo.configure(values=choices)
        selected_label = next(
            (label for label, template_id in self._custom_template_choices.items() if template_id == select_id),
            None,
        )
        if selected_label:
            self.template_var.set(selected_label)
        elif current in choices:
            self.template_var.set(current)
        elif choices:
            self.template_var.set(choices[0])

    def insert_template(self):
        self.commit_item(refresh=False)
        choice = self.template_var.get()
        template_id = self._custom_template_choices.get(choice)
        if template_id:
            record = self.template_store.by_id(template_id)
            if record is None:
                self.template_store.reload()
                self._refresh_template_choices()
                messagebox.showwarning("Machote no disponible", "El machote seleccionado ya no existe.")
                return
            item = record.clone_item()
            template_name = record.name
        elif choice in CONCEPT_TEMPLATES:
            item = clone_template(choice, self.quote.language)
            template_name = choice
        else:
            messagebox.showwarning("Selecciona un machote", "Selecciona un machote de la lista antes de insertarlo.")
            return
        self.quote.items.append(item)
        self._mark_spelling_dirty()
        self._refresh_items_tree(select=len(self.quote.items) - 1)
        self.status_var.set(f"Machote agregado: {template_name}")
        self.schedule_preview()

    def save_selected_template(self):
        if self._editing_item is None or not (0 <= self._editing_item < len(self.quote.items)):
            messagebox.showinfo("Guardar machote", "Selecciona primero el concepto que deseas reutilizar.")
            return
        self.commit_item(refresh=False)
        item = self.quote.items[self._editing_item]
        name = simpledialog.askstring(
            "Guardar machote personalizado",
            "Nombre para identificar este machote:",
            initialvalue=item.title[:80],
            parent=self,
        )
        if not name or not name.strip():
            return
        existing = self.template_store.find_name(name)
        existing_id = None
        if existing is not None:
            if not messagebox.askyesno(
                "Actualizar machote",
                f"Ya existe “{existing.name}”. ¿Reemplazarlo con la versión actual del concepto?",
                parent=self,
            ):
                return
            existing_id = existing.template_id
        try:
            saved = self.template_store.save_item(name, item, self.quote.language, existing_id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo guardar el machote", str(exc), parent=self)
            return
        self._refresh_template_choices(saved.template_id)
        self.status_var.set(f"Machote personalizado guardado: {saved.name}")

    def manage_templates(self):
        TemplateManagerDialog(self, self)

    def load_custom_template_into_quote(self, template_id):
        record = self.template_store.by_id(template_id)
        if record is None:
            raise ValueError("El machote seleccionado ya no existe.")
        item = record.clone_item()
        if self._editing_item is not None and 0 <= self._editing_item < len(self.quote.items):
            self.commit_item(refresh=False)
            self.quote.items[self._editing_item] = item
            index = self._editing_item
        else:
            self.quote.items.append(item)
            index = len(self.quote.items) - 1
        self._mark_spelling_dirty()
        self._editing_item = None
        self._refresh_items_tree(select=index)
        self._select_item()
        self.status_var.set(f"Machote cargado para edición: {record.name}")
        self.schedule_preview()

    def delete_item(self):
        selected = self.items_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        self.quote.items.pop(index)
        self._mark_spelling_dirty()
        self._editing_item = None
        self._refresh_items_tree(select=min(index, len(self.quote.items) - 1) if self.quote.items else None)
        self.schedule_preview()

    def duplicate_item(self):
        selected = self.items_tree.selection()
        if not selected:
            return
        self.commit_item(refresh=False)
        source = self.quote.items[int(selected[0])]
        copy = QuoteItem(source.title + " (copia)", source.description, list(source.subitems), source.unit, source.quantity, source.unit_price)
        self.quote.items.insert(int(selected[0]) + 1, copy)
        self._mark_spelling_dirty()
        self._refresh_items_tree(select=int(selected[0]) + 1)
        self.schedule_preview()

    def move_item(self, direction):
        selected = self.items_tree.selection()
        if not selected:
            return
        self.commit_item(refresh=False)
        old = int(selected[0])
        new = max(0, min(len(self.quote.items) - 1, old + direction))
        if old != new:
            self.quote.items.insert(new, self.quote.items.pop(old))
            self._editing_item = None
            self._refresh_items_tree(select=new)
            self.schedule_preview()

    def add_images(self):
        paths = filedialog.askopenfilenames(title="Agregar imágenes", filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.tif *.tiff *.webp")])
        for path in paths:
            managed_path = managed_input_copy(path, "quotes", "Recursos")
            if not any(Path(image.path) == managed_path for image in self.quote.images):
                self.quote.images.append(QuoteImage(str(managed_path)))
        self._refresh_images_tree(select=len(self.quote.images) - 1 if paths else None)
        self.schedule_preview()

    def _refresh_images_tree(self, select=None):
        self.images_tree.delete(*self.images_tree.get_children())
        for i, image in enumerate(self.quote.images):
            self.images_tree.insert("", END, iid=str(i), values=(i + 1, Path(image.path).name, image.caption))
        if select is not None and self.quote.images:
            select = max(0, min(select, len(self.quote.images) - 1))
            self.images_tree.selection_set(str(select))

    def _select_image(self, _event=None):
        selected = self.images_tree.selection()
        if not selected:
            return
        self._editing_image = int(selected[0])
        image = self.quote.images[self._editing_image]
        self._loading = True
        self.image_caption_var.set(image.caption)
        self._loading = False
        try:
            with Image.open(image.path) as source:
                photo = ImageOps.exif_transpose(source).convert("RGB")
                photo.thumbnail((430, 420), Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (430, 420), "#E8EFF4")
                canvas.paste(photo, ((430 - photo.width) // 2, (420 - photo.height) // 2))
            self._quote_image_photo = ImageTk.PhotoImage(canvas)
            self.image_preview.configure(image=self._quote_image_photo, text="")
        except Exception:
            self.image_preview.configure(image="", text="Vista previa no disponible")

    def delete_image(self):
        selected = self.images_tree.selection()
        if selected:
            self.quote.images.pop(int(selected[0]))
            self._editing_image = None
            self.image_caption_var.set("")
            self.image_preview.configure(image="", text="Selecciona una imagen")
            self._refresh_images_tree()
            self.schedule_preview()

    def new_quote(self):
        if not messagebox.askyesno("Nueva cotización", "¿Crear una cotización nueva? Los cambios no guardados se perderán."):
            return
        language = self.quote.language if self.quote.language in QUOTE_LANGUAGE_LABELS else "es"
        self.quote = QuoteData(
            prepared_by=signer_for_new_quote(), language=language,
            items=[clone_template("Levantamiento topográfico", language)],
        )
        translate_standard_quote_values(self.quote, language)
        self.json_path = None
        self._editing_item = None
        self._editing_image = None
        self._load_quote_to_form()
        self.status_var.set("Cotización nueva")
        self.refresh_preview()

    def save_json(self):
        self._sync_quote()
        path = self.json_path
        if not path:
            chosen = filedialog.asksaveasfilename(
                title="Guardar cotización editable", defaultextension=".json",
                filetypes=[("Cotización JSON", "*.json")],
                initialfile=f"EDITABLE_{quote_export_stem(self.quote)}.json",
                initialdir=str(category_dir("quotes")),
            )
            if not chosen:
                return
            path = Path(chosen)
        self.quote.save(path)
        preserve_artifact(path, "quotes")
        backup_editable(path, "Cotizaciones")
        self.json_path = path
        self.status_var.set(f"Guardado: {path.name}")

    def open_json(self):
        chosen = filedialog.askopenfilename(
            title="Abrir cotización", filetypes=[("Cotización JSON", "*.json")],
            initialdir=str(category_dir("quotes")),
        )
        if not chosen:
            return
        try:
            self.quote = QuoteData.load(chosen)
            self.quote.prepared_by = normalize_quote_signer(self.quote.prepared_by)
            translate_standard_quote_values(self.quote, self.quote.language)
            self.json_path = Path(chosen)
            self._editing_item = None
            self._editing_image = None
            self._load_quote_to_form()
            self.status_var.set(f"Abierto: {self.json_path.name}")
            self.refresh_preview()
        except Exception as exc:
            messagebox.showerror("No se pudo abrir", str(exc))

    def export_pdf(self):
        self._sync_quote()
        if not self.quote.items:
            messagebox.showwarning("Sin conceptos", "Agrega al menos un concepto antes de exportar.")
            return
        if not self.quote.spelling_checked:
            language_name = QUOTE_LANGUAGE_LABELS.get(self.quote.language, "Español")
            if not messagebox.askyesno(
                "⚠ Ortografía pendiente",
                f"Esta cotización no ha terminado su revisión ortográfica en {language_name}.\n\n"
                "¿Deseas exportar el PDF de todos modos?",
                icon="warning",
            ):
                self.status_var.set("Exportación detenida · revisa la ortografía")
                return
        initial = f"{quote_export_stem(self.quote)}.pdf"
        chosen = filedialog.asksaveasfilename(
            title="Exportar cotización PDF", defaultextension=".pdf", initialfile=initial,
            filetypes=[("Documento PDF", "*.pdf")], initialdir=str(category_dir("quotes")),
        )
        if not chosen:
            return
        try:
            output = generate_quote_pdf(self.quote, chosen, self.logo_path)
            json_output = editable_json_path(output)
            self.quote.save(json_output)
            preserve_artifact(output, "quotes")
            preserve_artifact(json_output, "quotes")
            backup_editable(json_output, "Cotizaciones")
            self.json_path = json_output
            self.status_var.set(f"PDF y editable creados: {output.name}")
            if messagebox.askyesno(
                "Cotización lista",
                f"Se crearon el PDF y su archivo editable:\n\n{output.name}\n{json_output.name}\n\n¿Deseas abrir el PDF?",
            ):
                os.startfile(output)
        except Exception as exc:
            messagebox.showerror("Error al exportar", str(exc))

    def _spelling_fields(self) -> list[ReviewField]:
        fields = []

        def quote_field(label, attribute):
            fields.append(ReviewField(label, str(getattr(self.quote, attribute) or ""), lambda value, name=attribute: setattr(self.quote, name, value)))

        for label, attribute in (
            ("Cliente / empresa", "client_name"), ("Contacto", "contact"),
            ("Localización del servicio", "location"), ("Concepto general", "project_title"),
            ("Notas generales", "notes"), ("Tiempo de entrega", "delivery_time"),
            ("Formas de pago", "payment_terms"), ("Elaboró / firma", "prepared_by"),
        ):
            quote_field(label, attribute)
        for number, item in enumerate(self.quote.items, 1):
            fields.append(ReviewField(f"Concepto {number} · título", item.title, lambda value, target=item: setattr(target, "title", value)))
            fields.append(ReviewField(f"Concepto {number} · descripción", item.description, lambda value, target=item: setattr(target, "description", value)))
            for subnumber, text in enumerate(item.subitems, 1):
                fields.append(ReviewField(
                    f"Concepto {number} · alcance {subnumber}", text,
                    lambda value, target=item, index=subnumber - 1: target.subitems.__setitem__(index, value),
                ))
        for number, image in enumerate(self.quote.images, 1):
            fields.append(ReviewField(
                f"Imagen {number} · descripción", image.caption,
                lambda value, target=image: setattr(target, "caption", value),
            ))
        return fields

    def start_spellcheck(self):
        if self._spell_loading:
            return
        self._sync_quote()
        fields = self._spelling_fields()
        self._spell_loading = True
        self.spell_button.state(["disabled"])
        language = self.quote.language if self.quote.language in QUOTE_LANGUAGE_LABELS else "es"
        language_name = QUOTE_LANGUAGE_LABELS[language]
        self.status_var.set(f"Cargando diccionario: {language_name}…")

        def worker():
            try:
                service = self._spell_service
                if service is None or service.language != language:
                    service = BilingualSpellService(language)
                documents, issues = service.scan(fields)
                self._spell_results.put((service, documents, issues, None))
            except Exception as exc:
                self._spell_results.put((None, None, None, exc))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll_spellcheck)

    def _poll_spellcheck(self):
        try:
            service, documents, issues, error = self._spell_results.get_nowait()
        except queue.Empty:
            if self.winfo_exists() and self._spell_loading:
                self.after(100, self._poll_spellcheck)
            return
        self._spell_loading = False
        self.spell_button.state(["!disabled"])
        if error:
            self.status_var.set("No se pudo iniciar el corrector")
            messagebox.showerror("Corrector ortográfico", str(error))
            return
        self._spell_service = service
        if not issues:
            self.quote.spelling_checked = True
            self._update_spelling_indicator()
            self.status_var.set(f"Ortografía revisada en {service.language_name} · sin palabras desconocidas")
            messagebox.showinfo("Ortografía", f"No se encontraron palabras desconocidas en {service.language_name}.")
            return
        selected_item = self._editing_item
        dialog = SpellReviewDialog(self, service, documents, issues)
        self.wait_window(dialog)
        for document in documents:
            document.apply_changes()
        self._editing_item = None
        self._editing_image = None
        self._load_quote_to_form()
        if selected_item is not None and self.quote.items:
            self._refresh_items_tree(select=min(selected_item, len(self.quote.items) - 1))
        self.quote.spelling_checked = bool(dialog.finished)
        self._update_spelling_indicator()
        self.schedule_preview()
        state = "completada" if dialog.finished else "interrumpida"
        self.status_var.set(
            f"Ortografía {state} · {dialog.corrected} correcciones · "
            f"{dialog.added} palabras agregadas · {dialog.omitted} omitidas"
        )

    def refresh_preview(self):
        if self._preview_job:
            try:
                self.after_cancel(self._preview_job)
            except Exception:
                pass
            self._preview_job = None
        try:
            self._sync_quote()
            generate_quote_pdf(self.quote, self._preview_pdf, self.logo_path)
            self._preview_page = min(self._preview_page, self._page_count() - 1)
            self._show_preview_page()
            iva = f"IVA: {money(self.quote.vat)}\n" if self.quote.include_vat else "IVA: No incluido\n"
            self.totals_label.configure(text=f"Subtotal: {money(self.quote.subtotal)}\n{iva}Total: {money(self.quote.total)}")
            large_preview = self._large_preview_window
            if large_preview is not None and large_preview.winfo_exists():
                large_preview.reload_document(keep_page=True)
            return True
        except Exception as exc:
            self.pdf_preview.configure(image="", text=f"No se pudo generar la vista previa:\n{exc}")
            return False

    def open_large_preview(self):
        current = self._large_preview_window
        if current is not None:
            try:
                if current.winfo_exists():
                    current.lift()
                    current.focus_force()
                    return
            except Exception:
                pass
        if not self.refresh_preview() or not self._preview_pdf.exists():
            messagebox.showerror("Vista previa no disponible", "No fue posible generar el PDF de vista previa.", parent=self)
            return
        self._large_preview_window = QuotePreviewDialog(self, self._preview_pdf)

    def _page_count(self):
        try:
            import pymupdf as fitz
            with fitz.open(self._preview_pdf) as doc:
                self._preview_pages = len(doc)
        except Exception:
            self._preview_pages = 0
        return self._preview_pages

    def _show_preview_page(self):
        try:
            import pymupdf as fitz
            with fitz.open(self._preview_pdf) as doc:
                self._preview_pages = len(doc)
                if not self._preview_pages:
                    return
                self._preview_page = max(0, min(self._preview_page, self._preview_pages - 1))
                page = doc[self._preview_page]
                pix = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                max_width = max(130, self.pdf_preview.winfo_width() - 10)
                max_height = max(220, self.pdf_preview.winfo_height() - 10)
                image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(image)
            self.pdf_preview.configure(image=self._preview_photo, text="")
            self.page_label.configure(text=f"Página {self._preview_page + 1} de {self._preview_pages}")
        except ImportError:
            self.pdf_preview.configure(image="", text="Instala PyMuPDF para ver la vista previa.\nLa exportación PDF sigue disponible.")

    def change_page(self, delta):
        if self._preview_pages:
            self._preview_page = max(0, min(self._preview_pages - 1, self._preview_page + delta))
            self._show_preview_page()

    def _preview_resized(self, _event=None):
        if not self._preview_pdf.exists() or not self._preview_pages:
            return
        if self._preview_resize_job:
            self.after_cancel(self._preview_resize_job)
        self._preview_resize_job = self.after(180, self._finish_preview_resize)

    def _finish_preview_resize(self):
        self._preview_resize_job = None
        if self.winfo_exists():
            self._show_preview_page()


class QuotePreviewDialog(Toplevel):
    def __init__(self, owner: QuotationTool, pdf_path: Path):
        super().__init__(owner)
        self.withdraw()
        self.owner = owner
        self.pdf_path = Path(pdf_path)
        self.title("Vista ampliada de la cotización")
        self.transient(owner.winfo_toplevel())
        self.page_index = owner._preview_page
        self.page_count = 0
        self.fit_mode = "page"
        self.custom_scale = 1.4
        self._last_scale = 1.0
        self._photo = None
        self._render_job = None
        self.page_var = StringVar(value="Página 0 de 0")
        self.zoom_var = StringVar(value="Ajustar página")
        self._build()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Left>", lambda _event: self.change_page(-1))
        self.bind("<Right>", lambda _event: self.change_page(1))
        self.bind("<Control-plus>", lambda _event: self.zoom(1))
        self.bind("<Control-minus>", lambda _event: self.zoom(-1))
        show_responsive_dialog(
            self, owner, preferred_width=1120, preferred_height=760, screen_margin=24, grab=False,
        )
        self.after(50, self._maximize)
        self.after(90, lambda: self.reload_document(keep_page=True))

    def _build(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(16, 10))
        header.pack(fill="x")
        ttk.Label(header, text="Vista ampliada", style="HeaderTitle.TLabel").pack(side="left", padx=(0, 18))
        self.previous_button = ttk.Button(
            header, text="‹ Anterior", style="HeaderButton.TButton", command=lambda: self.change_page(-1),
        )
        self.previous_button.pack(side="left")
        ttk.Label(header, textvariable=self.page_var, style="HeaderSub.TLabel", width=18, anchor="center").pack(side="left", padx=6)
        self.next_button = ttk.Button(
            header, text="Siguiente ›", style="HeaderButton.TButton", command=lambda: self.change_page(1),
        )
        self.next_button.pack(side="left")
        ttk.Button(header, text="−", width=3, style="HeaderButton.TButton", command=lambda: self.zoom(-1)).pack(side="right")
        ttk.Label(header, textvariable=self.zoom_var, style="HeaderSub.TLabel", width=17, anchor="center").pack(side="right", padx=4)
        ttk.Button(header, text="+", width=3, style="HeaderButton.TButton", command=lambda: self.zoom(1)).pack(side="right")
        ttk.Button(header, text="Ajustar ancho", style="HeaderButton.TButton", command=lambda: self.set_fit("width")).pack(side="right", padx=(8, 0))
        ttk.Button(header, text="Página completa", style="HeaderButton.TButton", command=lambda: self.set_fit("page")).pack(side="right", padx=(8, 0))

        viewer = ttk.Frame(self, style="Dialog.TFrame")
        viewer.pack(fill="both", expand=True)
        viewer.columnconfigure(0, weight=1)
        viewer.rowconfigure(0, weight=1)
        self.canvas = Canvas(
            viewer, background="#50616D", highlightthickness=0, borderwidth=0,
            xscrollincrement=20, yscrollincrement=20,
        )
        xbar = ttk.Scrollbar(viewer, orient="horizontal", command=self.canvas.xview)
        ybar = ttk.Scrollbar(viewer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<Configure>", self._resized)
        self.canvas.bind("<MouseWheel>", self._mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self._mousewheel)

        hint = ttk.Frame(self, style="StatusBar.TFrame", padding=(14, 5))
        hint.pack(fill="x", side="bottom")
        ttk.Label(
            hint,
            text="Doble clic en la miniatura para abrir · Ctrl + rueda cambia el zoom · Flechas cambian de página",
            style="StatusBar.TLabel",
        ).pack(side="left")
        ttk.Button(hint, text="Cerrar", style="Secondary.TButton", command=self.destroy).pack(side="right")

    def _maximize(self):
        try:
            self.state("zoomed")
        except Exception:
            width = self.winfo_screenwidth()
            height = self.winfo_screenheight()
            self.geometry(f"{width}x{height}+0+0")

    def reload_document(self, keep_page=True):
        try:
            import pymupdf as fitz
            with fitz.open(self.pdf_path) as document:
                self.page_count = len(document)
        except Exception as exc:
            self.page_count = 0
            self.canvas.delete("all")
            self.canvas.create_text(
                max(200, self.canvas.winfo_width() // 2), max(150, self.canvas.winfo_height() // 2),
                text=f"No se pudo abrir la vista previa:\n{exc}", fill="white", justify="center",
                font=("Segoe UI", 12),
            )
            return
        if not keep_page:
            self.page_index = 0
        self.page_index = max(0, min(self.page_index, max(0, self.page_count - 1)))
        self._update_navigation()
        self.schedule_render(20)

    def _update_navigation(self):
        self.page_var.set(f"Página {self.page_index + 1} de {self.page_count}" if self.page_count else "Página 0 de 0")
        self.previous_button.state(["disabled"] if self.page_index <= 0 else ["!disabled"])
        self.next_button.state(["disabled"] if self.page_index >= self.page_count - 1 else ["!disabled"])

    def change_page(self, delta):
        if not self.page_count:
            return
        target = max(0, min(self.page_count - 1, self.page_index + delta))
        if target == self.page_index:
            return
        self.page_index = target
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self._update_navigation()
        self.schedule_render(10)

    def set_fit(self, mode):
        self.fit_mode = mode
        self.zoom_var.set("Ajustar ancho" if mode == "width" else "Ajustar página")
        self.schedule_render(10)

    def zoom(self, direction):
        base = self._last_scale if self._last_scale > 0 else 1.0
        factor = 1.18 if direction > 0 else 1 / 1.18
        self.custom_scale = max(0.45, min(3.5, base * factor))
        self.fit_mode = "custom"
        self.zoom_var.set(f"{self.custom_scale * 100:.0f}%")
        self.schedule_render(10)

    def _mousewheel(self, event):
        if event.state & 0x0004:
            self.zoom(1 if event.delta > 0 else -1)
            return "break"
        units = int(-event.delta / 120)
        if event.state & 0x0001:
            self.canvas.xview_scroll(units, "units")
        else:
            self.canvas.yview_scroll(units, "units")
        return "break"

    def _resized(self, _event=None):
        if self.fit_mode in {"page", "width"}:
            self.schedule_render(160)

    def schedule_render(self, delay=80):
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(delay, self._render)

    def _render(self):
        self._render_job = None
        if not self.page_count or not self.winfo_exists():
            return
        try:
            import pymupdf as fitz
            with fitz.open(self.pdf_path) as document:
                page = document[self.page_index]
                page_width = max(1.0, float(page.rect.width))
                page_height = max(1.0, float(page.rect.height))
                available_width = max(240, self.canvas.winfo_width() - 54)
                available_height = max(260, self.canvas.winfo_height() - 44)
                if self.fit_mode == "page":
                    scale = min(available_width / page_width, available_height / page_height)
                elif self.fit_mode == "width":
                    scale = available_width / page_width
                else:
                    scale = self.custom_scale
                scale = max(0.35, min(3.5, scale))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            self._last_scale = scale
            if self.fit_mode == "custom":
                self.zoom_var.set(f"{scale * 100:.0f}%")
            self._photo = ImageTk.PhotoImage(image)
            canvas_width = max(self.canvas.winfo_width(), image.width + 48)
            canvas_height = max(self.canvas.winfo_height(), image.height + 48)
            x = max(24, canvas_width // 2)
            self.canvas.delete("all")
            self.canvas.create_rectangle(
                x - image.width // 2 - 2, 22, x + image.width // 2 + 2, image.height + 26,
                fill="#34434D", outline="",
            )
            self.canvas.create_image(x, 24, image=self._photo, anchor="n")
            self.canvas.configure(scrollregion=(0, 0, canvas_width, canvas_height))
        except Exception as exc:
            self.canvas.delete("all")
            self.canvas.create_text(
                max(200, self.canvas.winfo_width() // 2), max(150, self.canvas.winfo_height() // 2),
                text=f"No se pudo renderizar la página:\n{exc}", fill="white", justify="center",
                font=("Segoe UI", 12),
            )

    def destroy(self):
        if self._render_job:
            try:
                self.after_cancel(self._render_job)
            except Exception:
                pass
            self._render_job = None
        if getattr(self.owner, "_large_preview_window", None) is self:
            self.owner._large_preview_window = None
        super().destroy()


class TemplateManagerDialog(Toplevel):
    def __init__(self, master, tool: QuotationTool):
        super().__init__(master)
        self.withdraw()
        self.title("Machotes personalizados de cotización")
        self.transient(master.winfo_toplevel())
        self.tool = tool
        self.store = tool.template_store
        self._build()
        self._refresh()
        self.bind("<Escape>", lambda _event: self.destroy())
        show_responsive_dialog(self, master, preferred_width=880, preferred_height=530)
        if self.store.last_error:
            self.after_idle(lambda: messagebox.showwarning(
                "Colección recuperada",
                "El archivo principal de machotes no pudo leerse. Se cargó el respaldo disponible; revisa o vuelve a importar tus machotes.",
                parent=self,
            ))

    def _build(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 15))
        header.pack(fill="x")
        ttk.Label(header, text="Machotes personalizados", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Guarda conceptos editados, reutilízalos y lleva una colección respaldable entre computadoras",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        footer = ttk.Frame(self, style="Dialog.TFrame", padding=(18, 10, 18, 15))
        footer.pack(fill="x", side="bottom")
        ttk.Button(footer, text="Importar colección", style="Secondary.TButton", command=self._import).pack(side="left")
        ttk.Button(footer, text="Exportar", style="Secondary.TButton", command=self._export).pack(side="left", padx=(7, 0))
        ttk.Button(footer, text="Cerrar", style="Accent.TButton", command=self.destroy).pack(side="right")

        body = ttk.Frame(self, style="Dialog.TFrame", padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        ttk.Label(
            body,
            text=(
                "Los machotes incluidos con la aplicación permanecen intactos. Aquí se administran únicamente "
                "tus versiones personalizadas; cada cambio conserva un respaldo automático del archivo anterior."
            ),
            style="Dialog.TLabel",
            foreground="#657887",
            wraplength=820,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 12))

        table = ttk.Frame(body, style="Card.TFrame")
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table,
            columns=("name", "language", "title", "updated"),
            show="headings",
            selectmode="browse",
        )
        columns = (
            ("name", "Nombre", 210, True),
            ("language", "Idioma", 70, False),
            ("title", "Título del concepto", 330, True),
            ("updated", "Última edición", 145, False),
        )
        for key, label, width, stretch in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=stretch, anchor="w" if key != "language" else "center")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _event: self._load())

        actions = ttk.Frame(body, style="Dialog.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="Cargar para editar", style="Accent.TButton", command=self._load).pack(side="left")
        ttk.Button(actions, text="Actualizar con concepto actual", style="Secondary.TButton", command=self._overwrite).pack(side="left", padx=(7, 0))
        ttk.Button(actions, text="Renombrar", style="Secondary.TButton", command=self._rename).pack(side="right", padx=(7, 0))
        ttk.Button(actions, text="Eliminar", style="Secondary.TButton", command=self._delete).pack(side="right")

    def _selected_id(self):
        selected = self.tree.selection()
        return selected[0] if selected else None

    def _refresh(self, select_id=None):
        self.tree.delete(*self.tree.get_children())
        for template in self.store.templates:
            updated = template.updated_at.replace("T", " ")[:16]
            self.tree.insert(
                "", END, iid=template.template_id,
                values=(template.name, template.language.upper(), template.item.title, updated),
            )
        if select_id and self.tree.exists(select_id):
            self.tree.selection_set(select_id)
            self.tree.focus(select_id)

    def _load(self):
        template_id = self._selected_id()
        if not template_id:
            messagebox.showinfo("Selecciona un machote", "Selecciona el machote que deseas cargar.", parent=self)
            return
        if self.tool._editing_item is not None and not messagebox.askyesno(
            "Cargar machote",
            "¿Reemplazar el concepto seleccionado con este machote? El cambio actual seguirá disponible hasta guardar el JSON.",
            parent=self,
        ):
            return
        try:
            self.tool.load_custom_template_into_quote(template_id)
        except ValueError as exc:
            messagebox.showerror("No se pudo cargar", str(exc), parent=self)
            return
        self.destroy()

    def _overwrite(self):
        template_id = self._selected_id()
        record = self.store.by_id(template_id) if template_id else None
        index = self.tool._editing_item
        if record is None or index is None or not (0 <= index < len(self.tool.quote.items)):
            messagebox.showinfo(
                "Actualizar machote",
                "Selecciona un machote en esta ventana y un concepto en la cotización.",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "Actualizar machote",
            f"¿Guardar el concepto actualmente editado sobre “{record.name}”?",
            parent=self,
        ):
            return
        self.tool.commit_item(refresh=False)
        try:
            saved = self.store.save_item(record.name, self.tool.quote.items[index], self.tool.quote.language, record.template_id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo actualizar", str(exc), parent=self)
            return
        self.tool._refresh_template_choices(saved.template_id)
        self._refresh(saved.template_id)

    def _rename(self):
        template_id = self._selected_id()
        record = self.store.by_id(template_id) if template_id else None
        if record is None:
            messagebox.showinfo("Renombrar machote", "Selecciona un machote personalizado.", parent=self)
            return
        name = simpledialog.askstring("Renombrar machote", "Nuevo nombre:", initialvalue=record.name, parent=self)
        if not name or not name.strip() or name.strip() == record.name:
            return
        try:
            renamed = self.store.rename(record.template_id, name)
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo renombrar", str(exc), parent=self)
            return
        self.tool._refresh_template_choices(renamed.template_id)
        self._refresh(renamed.template_id)

    def _delete(self):
        template_id = self._selected_id()
        record = self.store.by_id(template_id) if template_id else None
        if record is None:
            messagebox.showinfo("Eliminar machote", "Selecciona un machote personalizado.", parent=self)
            return
        if not messagebox.askyesno(
            "Eliminar machote", f"¿Eliminar definitivamente “{record.name}”?", icon="warning", parent=self,
        ):
            return
        try:
            self.store.delete(record.template_id)
        except OSError as exc:
            messagebox.showerror("No se pudo eliminar", str(exc), parent=self)
            return
        self.tool._refresh_template_choices()
        self._refresh()

    def _import(self):
        chosen = filedialog.askopenfilename(
            parent=self,
            title="Importar machotes personalizados",
            filetypes=(("Machotes de cotización", "*.json"),),
            initialdir=str(self.store.path.parent),
        )
        if not chosen:
            return
        try:
            added, renamed = self.store.import_file(chosen)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("No se pudo importar", str(exc), parent=self)
            return
        self.tool._refresh_template_choices()
        self._refresh()
        messagebox.showinfo(
            "Importación terminada",
            f"Se agregaron {added} machote(s)." + (f" {renamed} nombre(s) se ajustaron para evitar duplicados." if renamed else ""),
            parent=self,
        )

    def _export(self):
        selected_id = self._selected_id()
        if not self.store.templates:
            messagebox.showinfo("Sin machotes", "Todavía no hay machotes personalizados para exportar.", parent=self)
            return
        selected_only = bool(selected_id) and messagebox.askyesno(
            "Exportar machotes",
            "¿Exportar solamente el machote seleccionado? Elige No para exportar toda la colección.",
            parent=self,
        )
        chosen = filedialog.asksaveasfilename(
            parent=self,
            title="Exportar machotes personalizados",
            defaultextension=".json",
            filetypes=(("Machotes de cotización", "*.json"),),
            initialfile="MACHOTES_COTIZACIONES_GRUPO_ITT.json",
            initialdir=str(self.store.path.parent),
        )
        if not chosen:
            return
        try:
            self.store.export_file(chosen, {selected_id} if selected_only else None)
        except OSError as exc:
            messagebox.showerror("No se pudo exportar", str(exc), parent=self)
            return
        messagebox.showinfo("Machotes exportados", f"Archivo creado:\n{chosen}", parent=self)
