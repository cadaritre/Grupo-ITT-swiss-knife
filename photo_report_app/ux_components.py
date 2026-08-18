from __future__ import annotations

from tkinter import Canvas, Toplevel
from tkinter import ttk


def show_responsive_dialog(
    window: Toplevel,
    parent,
    *,
    preferred_width: int = 640,
    preferred_height: int = 0,
    screen_margin: int = 44,
    grab: bool = True,
):
    """Size a finished dialog from its real requested content and center it.

    Fixed Tk geometries are unreliable under Windows display scaling. This
    helper measures the fully styled widgets, keeps the action row visible,
    limits the window to the usable screen and prevents shrinking below the
    measured content whenever the screen has enough room.
    """
    window.update_idletasks()
    requested_width = max(preferred_width, window.winfo_reqwidth() + 8)
    requested_height = max(preferred_height, window.winfo_reqheight() + 8)
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    max_width = max(420, screen_width - screen_margin * 2)
    max_height = max(360, screen_height - screen_margin * 2)
    width = min(requested_width, max_width)
    height = min(requested_height, max_height)

    try:
        parent_window = parent.winfo_toplevel()
        parent_window.update_idletasks()
        center_x = parent_window.winfo_rootx() + parent_window.winfo_width() // 2
        center_y = parent_window.winfo_rooty() + parent_window.winfo_height() // 2
    except Exception:
        center_x, center_y = screen_width // 2, screen_height // 2
    x = max(screen_margin, min(center_x - width // 2, screen_width - width - screen_margin))
    y = max(screen_margin // 2, min(center_y - height // 2, screen_height - height - screen_margin))
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.minsize(min(requested_width, max_width), min(requested_height, max_height))
    window.resizable(True, True)
    window.deiconify()
    window.lift()
    if grab:
        window.grab_set()
        def restore_parent_grab(event):
            if event.widget is not window:
                return
            try:
                if parent.winfo_exists() and parent.winfo_toplevel() is not window:
                    parent.grab_set()
            except Exception:
                pass
        window.bind("<Destroy>", restore_parent_grab, add="+")
    window.focus_force()


class ScrollableDialogContent(ttk.Frame):
    """Scrollable middle section for dialogs with a fixed header and footer."""

    def __init__(self, master, *, height: int = 420, padding: int = 20):
        super().__init__(master, style="Dialog.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = Canvas(
            self, height=height, background="#F4F9FC", highlightthickness=0,
            borderwidth=0,
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.content = ttk.Frame(self.canvas, style="Dialog.TFrame", padding=padding)
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._canvas_changed)
        self.canvas.bind("<MouseWheel>", self._mousewheel)
        self.content.bind("<MouseWheel>", self._mousewheel)

    def _content_changed(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.after_idle(self._update_scrollbar)

    def _canvas_changed(self, event):
        self.canvas.itemconfigure(self._window, width=max(1, event.width))
        self._update_scrollbar()

    def _update_scrollbar(self):
        if not self.winfo_exists():
            return
        needed = self.content.winfo_reqheight() > self.canvas.winfo_height() + 2
        if needed:
            self.scrollbar.grid()
        else:
            self.scrollbar.grid_remove()
            self.canvas.yview_moveto(0)

    def _mousewheel(self, event):
        if self.content.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ToolTip:
    def __init__(self, widget, text: str, delay: int = 450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._job = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _event=None):
        self.hide()
        self._job = self.widget.after(self.delay, self.show)

    def show(self):
        if self._window or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 7
        window = self._window = Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        ttk.Label(
            window, text=self.text, justify="left", wraplength=330,
            background="#173B5F", foreground="white", padding=(10, 7),
            font=("Segoe UI", 8), relief="solid", borderwidth=1,
        ).pack()

    def hide(self, _event=None):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._window:
            self._window.destroy()
            self._window = None


def attach_tooltip(widget, text: str):
    tooltip = ToolTip(widget, text)
    widget._grupoitt_tooltip = tooltip
    return widget


def help_badge(parent, text: str, style: str = "SoftHint.TLabel"):
    label = ttk.Label(parent, text=" ? ", style=style, cursor="question_arrow")
    attach_tooltip(label, text)
    return label


class ProgressStrip(ttk.Frame):
    def __init__(self, master, width: int = 145):
        super().__init__(master, style="Header.TFrame")
        self.label = ttk.Label(self, text="", style="HeaderSub.TLabel")
        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100, length=width)
        self.label.pack(anchor="e")
        self.bar.pack(fill="x", pady=(2, 0))
        self._visible = False
        self._hide_job = None

    def show(self, value: float = 0, text: str = "Preparando…"):
        if self._hide_job is not None:
            self.after_cancel(self._hide_job)
            self._hide_job = None
        if not self._visible:
            self.pack(side="right", padx=(8, 2))
            self._visible = True
        self.bar.stop()
        self.bar.configure(mode="determinate")
        self.bar["value"] = max(0, min(100, float(value)))
        self.label.configure(text=text)

    def update_progress(self, value: float, text: str | None = None):
        self.show(value, text if text is not None else self.label.cget("text"))

    def indeterminate(self, text: str):
        if not self._visible:
            self.pack(side="right", padx=(8, 2))
            self._visible = True
        self.label.configure(text=text)
        self.bar.configure(mode="indeterminate")
        self.bar.start(10)

    def finish(self, text: str = "Listo"):
        self.update_progress(100, text)
        self._hide_job = self.after(900, self.hide)

    def hide(self):
        if self._hide_job is not None:
            self.after_cancel(self._hide_job)
            self._hide_job = None
        self.bar.stop()
        if self._visible:
            self.pack_forget()
            self._visible = False
