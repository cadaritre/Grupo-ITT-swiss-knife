from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import Canvas, Tk, messagebox
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    from tkinterdnd2 import TkinterDnD
    AppTkBase = TkinterDnD.Tk
except ImportError:  # The app still starts; report drop support shows as unavailable.
    AppTkBase = Tk

from .app_storage import APP_DATA_ROOT, ensure_app_folders
from .branding import active_profile
from .cloud_sync import flush_cloud_queue
from .settings_dialog import SettingsDialog
from .tool_registry import ToolSpec, get_tool, registered_tools, registry_errors


ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
TOOL_ICON_DIR = ROOT / "assets" / "tool_icons"


class CompanyApp(AppTkBase):
    def __init__(self):
        super().__init__()
        self.company = active_profile()
        self.title(self.company.app_name)
        self.geometry("1380x840")
        self.minsize(1020, 650)
        self.configure(background="#F4F9FC")
        self.logo_path = self.company.logo_path
        ensure_app_folders()
        threading.Thread(target=flush_cloud_queue, name="grupoitt-drive-sync", daemon=True).start()
        self._screens = {}
        self._active = None
        self._images = []
        self._configure_style()
        self._set_icon()
        self.show_home()

    def _configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 9), foreground="#263746")
        style.configure("App.TFrame", background="#F4F9FC")
        style.configure("Dialog.TFrame", background="#F4F9FC")
        style.configure("Card.TFrame", background="white")
        style.configure("Soft.TFrame", background="#EDF7FB")
        style.configure("Header.TFrame", background="#07356F")
        style.configure("StatusBar.TFrame", background="#EAF4F8")
        style.configure("TLabel", background="#F4F9FC", foreground="#263746", font=("Segoe UI", 9))
        style.configure("Dialog.TLabel", background="#F4F9FC", foreground="#263746")
        style.configure("Card.TLabel", background="white", foreground="#263746")
        style.configure("Soft.TLabel", background="#EDF7FB", foreground="#263746")
        style.configure("HeaderTitle.TLabel", background="#07356F", foreground="white", font=("Segoe UI Semibold", 17))
        style.configure("HeaderSub.TLabel", background="#07356F", foreground="#CFE1EE", font=("Segoe UI", 9))
        style.configure("HeaderWarning.TLabel", background="#07356F", foreground="#FFD166", font=("Segoe UI Semibold", 9))
        style.configure("HeaderOk.TLabel", background="#07356F", foreground="#88E0B3", font=("Segoe UI Semibold", 9))
        style.configure("StatusBar.TLabel", background="#EAF4F8", foreground="#526A7A", font=("Segoe UI", 8))
        style.configure("HeroTitle.TLabel", background="#F4F9FC", foreground="#173B5F", font=("Segoe UI Semibold", 26))
        style.configure("HeroSub.TLabel", background="#F4F9FC", foreground="#647989", font=("Segoe UI", 11))
        style.configure("Section.TLabel", background="white", foreground="#173B5F", font=("Segoe UI Semibold", 10))
        style.configure("Field.Card.TLabel", background="white", foreground="#526A7A", font=("Segoe UI Semibold", 8))
        style.configure("FieldWarning.Card.TLabel", background="white", foreground="#A46A00", font=("Segoe UI Semibold", 8))
        style.configure("FieldOk.Card.TLabel", background="white", foreground="#31835C", font=("Segoe UI Semibold", 8))
        style.configure("Hint.Card.TLabel", background="white", foreground="#718391", font=("Segoe UI", 8))
        style.configure("SoftSection.TLabel", background="#EDF7FB", foreground="#173B5F", font=("Segoe UI Semibold", 9))
        style.configure("SoftHint.TLabel", background="#EDF7FB", foreground="#718391", font=("Segoe UI", 8))
        style.configure("DialogSection.TLabel", background="#F4F9FC", foreground="#173B5F", font=("Segoe UI Semibold", 10))
        style.configure("Settings.TLabel", background="white", foreground="#263746")
        style.configure("SettingsHeading.TLabel", background="white", foreground="#173B5F", font=("Segoe UI Semibold", 9))
        style.configure(
            "Settings.TLabelframe", background="white", bordercolor="#C9DEE9",
            lightcolor="#C9DEE9", darkcolor="#C9DEE9", borderwidth=1, relief="solid",
        )
        style.configure(
            "Settings.TLabelframe.Label", background="white", foreground="#173B5F",
            font=("Segoe UI Semibold", 9), padding=(3, 0),
        )
        style.configure("Preview.TLabel", background="#DCE5EA", foreground="#607686", borderwidth=1, relief="solid")
        style.configure("Accent.TButton", background="#0B7FAB", foreground="white", borderwidth=0, padding=(14, 8), font=("Segoe UI Semibold", 9))
        style.map("Accent.TButton", background=[("active", "#096C91"), ("disabled", "#A8BAC4")])
        style.configure("Secondary.TButton", background="#E1F2F8", foreground="#173B5F", borderwidth=0, padding=(11, 7), font=("Segoe UI Semibold", 9))
        style.map("Secondary.TButton", background=[("active", "#CBE7F1"), ("pressed", "#B9DDEB")])
        style.configure("HeaderButton.TButton", background="#07356F", foreground="#DCEBF3", borderwidth=0, padding=(10, 7), font=("Segoe UI Semibold", 9))
        style.map("HeaderButton.TButton", background=[("active", "#0A477F")])
        style.configure("HeaderAccent.TButton", background="#12A0C8", foreground="white", borderwidth=0, padding=(14, 8), font=("Segoe UI Semibold", 9))
        style.map("HeaderAccent.TButton", background=[("active", "#0B8DB4")])
        style.configure("Tool.TButton", background="white", foreground="#173B5F", borderwidth=1, relief="solid", padding=(20, 16), font=("Segoe UI Semibold", 13), anchor="w")
        style.map("Tool.TButton", background=[("active", "#EAF3F8")])
        # Keep both entry styles on exactly the same font metrics. Replacing
        # them with different implicit fonts can offset the Windows caret at
        # non-integer display scaling (125%/150%).
        entry_font = ("Segoe UI", 10)
        style.configure("TEntry", font=entry_font, padding=7, fieldbackground="white", bordercolor="#C7D4DC", lightcolor="#C7D4DC", darkcolor="#C7D4DC")
        style.configure("Warning.TEntry", font=entry_font, padding=7, fieldbackground="#FFF8DF", bordercolor="#D8A52D", lightcolor="#D8A52D", darkcolor="#D8A52D")
        style.configure(
            "TCombobox", padding=6, fieldbackground="white", background="#E1F2F8",
            foreground="#263746", arrowcolor="#173B5F", bordercolor="#BBD2DE",
            lightcolor="#BBD2DE", darkcolor="#BBD2DE",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "white"), ("disabled", "#EDF4F7")],
            background=[("readonly", "#E1F2F8"), ("active", "#CBE7F1")],
            bordercolor=[("focus", "#0B7FAB")],
        )
        style.configure(
            "TSpinbox", padding=6, fieldbackground="white", background="#E1F2F8",
            foreground="#263746", arrowcolor="#173B5F", bordercolor="#BBD2DE",
            lightcolor="#BBD2DE", darkcolor="#BBD2DE",
        )
        style.map("TSpinbox", background=[("active", "#CBE7F1")], bordercolor=[("focus", "#0B7FAB")])
        style.configure("TCheckbutton", background="#F4F9FC", foreground="#263746", padding=2)
        style.map("TCheckbutton", background=[("active", "#F4F9FC"), ("selected", "#F4F9FC")])
        style.configure("Dialog.TCheckbutton", background="#F4F9FC", foreground="#263746", padding=2)
        style.map("Dialog.TCheckbutton", background=[("active", "#F4F9FC"), ("selected", "#F4F9FC")])
        style.configure("Settings.TCheckbutton", background="white", foreground="#263746", padding=2)
        style.map("Settings.TCheckbutton", background=[("active", "white"), ("selected", "white")])
        style.configure("Treeview", rowheight=31, background="white", fieldbackground="white", borderwidth=0, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#E1F2F8", foreground="#173B5F", font=("Segoe UI Semibold", 8), padding=6)
        style.map("Treeview", background=[("selected", "#CDE8F2")], foreground=[("selected", "#173B5F")])
        style.configure("TNotebook", background="white", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI Semibold", 9), background="#E1F2F8")
        style.map("TNotebook.Tab", background=[("selected", "white")], foreground=[("selected", "#0B7FAB")])
        style.configure("Horizontal.TProgressbar", background="#0B7FAB", troughcolor="#D9E4EA")

    def _set_icon(self):
        if not self.logo_path.exists():
            return
        try:
            with Image.open(self.logo_path) as source:
                icon = source.convert("RGBA")
                icon.thumbnail((128, 128), Image.Resampling.LANCZOS)
            self._window_icon = ImageTk.PhotoImage(icon)
            self.iconphoto(True, self._window_icon)
        except Exception:
            pass

    def _tool_icon(self, label, color, asset_name=None):
        if asset_name == "__triangulation__":
            image = Image.new("RGBA", (88, 88), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((2, 2, 86, 86), 18, fill="#B8662C")
            points = [(16, 66), (27, 25), (47, 15), (73, 29), (68, 69), (42, 76)]
            edges = ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0), (1, 5), (1, 3), (3, 5))
            for start, end in edges:
                draw.line((points[start], points[end]), fill="#FFE7C8", width=3)
            for x, y in points:
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill="white", outline="#7A3D1D", width=1)
            photo = ImageTk.PhotoImage(image)
            self._images.append(photo)
            return photo
        asset_path = TOOL_ICON_DIR / asset_name if asset_name else None
        if asset_path and asset_path.exists():
            try:
                with Image.open(asset_path) as source:
                    image = source.convert("RGBA")
                    image.thumbnail((88, 88), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                self._images.append(photo)
                return photo
            except Exception:
                pass
        image = Image.new("RGBA", (70, 70), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, 68, 68), 15, fill=color)
        try:
            font = ImageFont.truetype("arialbd.ttf", 21 if len(label) <= 3 else 16)
        except OSError:
            font = ImageFont.load_default()
        draw.text((35, 35), label, anchor="mm", font=font, fill="white")
        photo = ImageTk.PhotoImage(image)
        self._images.append(photo)
        return photo

    def _hide_active(self):
        if self._active is not None:
            self._active.pack_forget()

    def show_home(self):
        self._hide_active()
        if "home" not in self._screens:
            self._screens["home"] = self._build_home()
        self._active = self._screens["home"]
        self._active.pack(fill="both", expand=True)
        if hasattr(self, "_home_canvas"):
            self.bind("<MouseWheel>", self._scroll_home, add="+")

    def show_tool(self, tool_id: str):
        try:
            spec = get_tool(tool_id)
            if not spec.available:
                return
            if tool_id not in self._screens:
                self._screens[tool_id] = spec.create_screen(self, self.logo_path, self.show_home)
        except Exception as exc:
            messagebox.showerror("No se pudo abrir la herramienta", f"{tool_id}\n\n{exc}")
            return
        self._hide_active()
        self.unbind("<MouseWheel>")
        self._active = self._screens[tool_id]
        self._active.pack(fill="both", expand=True)

    # Compatibility helpers for shortcuts, tests and older integrations.
    def show_reports(self):
        self.show_tool("reports")

    def show_quotes(self):
        self.show_tool("quotes")

    def show_sketches(self):
        self.show_tool("sketches")

    def show_geospatial_converter(self):
        self.show_tool("geospatial_converter")

    def show_triangulation(self):
        self.show_tool("triangulation")

    def show_settings(self):
        if not any(isinstance(child, SettingsDialog) for child in self.winfo_children()):
            SettingsDialog(self, self.restart_app)

    def restart_app(self):
        environment = os.environ.copy()
        if getattr(sys, "frozen", False):
            command = [sys.executable]
            # A one-file PyInstaller process normally assumes that another
            # invocation of the same executable is a worker and reuses the
            # current _MEI extraction directory.  A restart must be an
            # independent instance because the old bootloader removes that
            # directory as soon as this process exits.
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            working_directory = Path(sys.executable).resolve().parent
        else:
            command = [sys.executable, str(ROOT / "main.py")]
            working_directory = ROOT
        subprocess.Popen(
            command,
            cwd=str(working_directory),
            env=environment,
            close_fds=True,
        )
        self.destroy()

    def _build_home(self):
        page = ttk.Frame(self, style="App.TFrame")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        canvas = self._home_canvas = Canvas(
            page, background="#F4F9FC", highlightthickness=0, borderwidth=0,
        )
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        content = ttk.Frame(canvas, style="App.TFrame", padding=24)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def content_changed(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            needed = content.winfo_reqheight() > canvas.winfo_height() + 2
            if needed:
                scrollbar.grid()
            else:
                scrollbar.grid_remove()
                canvas.yview_moveto(0)

        def canvas_changed(event):
            canvas.itemconfigure(content_window, width=max(1, event.width))
            self._layout_home_cards(max(1, event.width - 48))
            self.after_idle(content_changed)

        content.bind("<Configure>", content_changed)
        canvas.bind("<Configure>", canvas_changed)

        header = ttk.Frame(content, style="App.TFrame")
        header.pack(fill="x", pady=(4, 18))
        if self.logo_path.exists():
            try:
                with Image.open(self.logo_path) as source:
                    logo = source.convert("RGBA")
                    logo.thumbnail((95, 62), Image.Resampling.LANCZOS)
                self._home_logo = ImageTk.PhotoImage(logo)
                ttk.Label(header, image=self._home_logo, style="TLabel").pack(side="left", padx=(0, 22))
            except Exception:
                pass
        title = ttk.Frame(header, style="App.TFrame")
        title.pack(side="left", fill="x", expand=True)
        ttk.Label(title, text=self.company.app_name, style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(title, text="Elige qué documento o proceso quieres preparar", style="HeroSub.TLabel").pack(anchor="w", pady=(4, 0))
        website = ttk.Label(title, text=self.company.website_label, style="HeroSub.TLabel", cursor="hand2")
        website.pack(anchor="w", pady=(5, 0))
        website.bind("<Button-1>", lambda _event: webbrowser.open(self.company.website))

        tools = self._home_tools = ttk.Frame(content, style="App.TFrame")
        tools.pack(fill="x", expand=False)
        specs = registered_tools()
        self._home_cards = [self._tool_card(tools, 0, 0, spec) for spec in specs]
        self._home_columns = 0
        self._layout_home_cards(1200)
        footer = ttk.Frame(content, style="App.TFrame")
        footer.pack(fill="x", pady=(20, 0))
        ttk.Label(
            footer,
            text=f"Proyectos, respaldos, diccionarios y preferencias: {APP_DATA_ROOT}",
            style="HeroSub.TLabel",
        ).pack(side="left")
        footer_actions = ttk.Frame(footer, style="App.TFrame")
        footer_actions.pack(side="right")
        ttk.Button(
            footer_actions, text="Abrir carpeta de trabajo", style="Secondary.TButton",
            command=lambda: os.startfile(APP_DATA_ROOT),
        ).pack(side="left")
        ttk.Button(
            footer_actions, text="⚙ Ajustes", style="Secondary.TButton",
            command=self.show_settings,
        ).pack(side="left", padx=(8, 0))
        if registry_errors():
            ttk.Label(
                footer, text=f"⚠ {len(registry_errors())} manifiesto(s) no pudieron cargarse",
                style="HeroSub.TLabel", foreground="#A46A00",
            ).pack(side="right", padx=12)
        return page

    def _scroll_home(self, event):
        canvas = getattr(self, "_home_canvas", None)
        if canvas is None or not canvas.winfo_ismapped():
            return
        first, last = canvas.yview()
        if first > 0.0 or last < 1.0:
            canvas.yview_scroll(int(-event.delta / 120), "units")
            return "break"

    def _layout_home_cards(self, available_width: int):
        if not hasattr(self, "_home_cards"):
            return
        columns = 3 if available_width >= 1080 else 2 if available_width >= 700 else 1
        self._home_columns = columns
        for column in range(3):
            self._home_tools.columnconfigure(column, weight=1 if column < columns else 0, uniform="home_tools")
        rows = max(1, (len(self._home_cards) + columns - 1) // columns)
        for row in range(len(self._home_cards)):
            self._home_tools.rowconfigure(
                row, weight=0, minsize=0,
                uniform="home_tool_rows" if row < rows else "",
            )
        card_width = max(230, (available_width - (columns - 1) * 10) // columns)
        for index, (card, description) in enumerate(self._home_cards):
            card.grid_forget()
            card.grid(row=index // columns, column=index % columns, sticky="nsew", padx=5, pady=5)
            description.configure(wraplength=max(190, card_width - 42))
        self._home_tools.update_idletasks()
        if hasattr(self, "_home_canvas"):
            self._home_canvas.configure(scrollregion=self._home_canvas.bbox("all"))

    def _tool_card(self, parent, row: int, column: int, spec: ToolSpec):
        card = ttk.Frame(parent, style="Card.TFrame", padding=18)
        card.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
        card.columnconfigure(0, weight=1)
        icon = self._tool_icon(spec.icon_text, spec.icon_color, spec.icon_asset)
        ttk.Label(card, image=icon, style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, text=spec.title, style="Card.TLabel", font=("Segoe UI Semibold", 15), foreground="#173B5F").grid(row=1, column=0, sticky="w", pady=(11, 5))
        description = ttk.Label(
            card, text=spec.description, style="Card.TLabel", font=("Segoe UI", 10),
            foreground="#647989", wraplength=300, justify="left",
        )
        description.grid(row=2, column=0, sticky="new")
        command = (lambda selected=spec.tool_id: self.show_tool(selected)) if spec.available else None
        button = ttk.Button(card, text=spec.action_label, style="Accent.TButton", command=command)
        button.grid(row=3, column=0, sticky="w", pady=(13, 0))
        if not spec.available:
            button.state(["disabled"])
        return card, description


def run():
    CompanyApp().mainloop()
