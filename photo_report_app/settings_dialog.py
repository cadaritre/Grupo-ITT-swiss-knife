from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from tkinter import BooleanVar, Canvas, IntVar, StringVar, Toplevel, colorchooser, filedialog, messagebox
from tkinter import ttk

from PIL import Image, ImageTk

from .app_storage import SETTINGS, category_dir
from .branding import TRESVIZO_LOGO, active_profile
from .quote_theme import QUOTE_PALETTE_PRESETS, matching_preset, quote_palette
from .ux_components import ScrollableDialogContent, show_responsive_dialog


PASSWORD_SHA256 = "ed58e0ec3525a7b51713121580d6acba34a1452b3580c6c1d013498e89a270e1"
MAP_LAYERS = ("Calles - OpenStreetMap", "Topográfico - OpenTopoMap", "Base neutra")


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
        SETTINGS.update({
            "reports.include_map": bool(self.include_map_var.get()),
            "reports.open_pdf": bool(self.open_pdf_var.get()),
            "sketches.map_layer": self.map_layer_var.get() if self.map_layer_var.get() in MAP_LAYERS else MAP_LAYERS[0],
            "geospatial.map_layer": self.map_layer_var.get() if self.map_layer_var.get() in MAP_LAYERS else MAP_LAYERS[0],
            "geospatial.utm_zone": zone,
            "geospatial.hemisphere": self.hemisphere_var.get() if self.hemisphere_var.get() in {"N", "S"} else "N",
        })
        self.destroy()

    def _unlock(self):
        digest = hashlib.sha256(self.password_var.get().encode("utf-8")).hexdigest()
        if digest != PASSWORD_SHA256:
            messagebox.showerror("Acceso denegado", "La contraseña no es correcta.", parent=self)
            self.password_var.set("")
            return
        self.password_var.set("")
        BrandingDialog(self, self.on_restart)


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
