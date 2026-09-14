"""Interfaz Tkinter en español y acceso por línea de comandos."""
from __future__ import annotations
import csv
import json
import os
from pathlib import Path
import queue
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from dataclasses import asdict
from .app_storage import category_dir
from .ppk_core import Settings, ROOT, ICHI, PPKError, Cancelled, autodetect, process, rinex_info, parse_mrk, gps_datetime


class PPKTool(ttk.Frame):
    """PPK de fotografías DJI integrado como pantalla de la aplicación."""

    def __init__(self, master, logo_path: Path, on_home):
        super().__init__(master, style='App.TFrame')
        self.logo_path = logo_path
        self.on_home = on_home
        self.events=queue.Queue(); self.cancel=threading.Event(); self.busy=False
        self.last_result=None; self.nav=[]
        self.vars={key:tk.StringVar() for key in ('photos','rover','base','mrk','output','base_lat','base_lon','base_h','antenna_height','antenna_type','time_offset','max_gap','max_fb_h','max_fb_v')}
        self.base_mode=tk.StringVar(value='header'); self.iono=tk.StringVar(value='auto'); self.lever=tk.StringVar(value='auto')
        self.provisional=tk.BooleanVar(value=False)
        self.status=tk.StringVar(value='Selecciona la carpeta del vuelo para detectar sus archivos.')
        self._build()
        self.apply_settings(Settings(output=str(category_dir('ppk'))))
        self.after(100,self.drain)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, style='Header.TFrame', padding=(22, 12))
        top.grid(row=0, column=0, sticky='ew')
        ttk.Button(top, text='‹ Herramientas', style='HeaderButton.TButton', command=self.close).pack(side='left')
        ttk.Label(top, text='PPK Dron', style='HeaderTitle.TLabel').pack(side='left', padx=18)
        ttk.Label(
            top, text='RINEX + MRK → fotografías georreferenciadas con control de calidad',
            style='HeaderSub.TLabel',
        ).pack(side='left')

        toolbar = ttk.Frame(self, style='Card.TFrame', padding=(18, 9))
        toolbar.grid(row=1, column=0, sticky='ew')
        ttk.Button(toolbar, text='Abrir proyecto', style='Secondary.TButton', command=self.open_project).pack(side='left')
        ttk.Button(toolbar, text='Guardar proyecto', style='Secondary.TButton', command=self.save_project).pack(side='left', padx=7)
        ttk.Button(
            toolbar, text='Guía de uso', style='Secondary.TButton', command=lambda: os.startfile(ROOT / 'LEEME.txt'),
        ).pack(side='right')

        self.book = ttk.Notebook(self)
        self.book.grid(row=2, column=0, sticky='nsew', padx=18, pady=(14, 10))

        def scroll_page():
            page = ttk.Frame(self.book, style='App.TFrame')
            canvas = tk.Canvas(page, bg='#F4F9FC', highlightthickness=0, height=450)
            bar = ttk.Scrollbar(page, orient='vertical', command=canvas.yview)
            canvas.configure(yscrollcommand=bar.set)
            bar.pack(side='right', fill='y')
            canvas.pack(side='left', fill='both', expand=True)
            body = ttk.Frame(canvas, style='App.TFrame', padding=(10, 14, 16, 18))
            item = canvas.create_window((0, 0), window=body, anchor='nw')
            canvas.bind('<Configure>', lambda event: canvas.itemconfigure(item, width=event.width))
            body.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
            canvas.bind('<MouseWheel>', lambda event: canvas.yview_scroll(int(-event.delta / 120), 'units'))
            return page, body

        files_page, files = scroll_page()
        opts_page, opts = scroll_page()
        results = ttk.Frame(self.book, style='App.TFrame', padding=12)
        self.book.add(files_page, text='  1 · Archivos  ')
        self.book.add(opts_page, text='  2 · Base y calidad  ')
        self.book.add(results, text='  3 · Resultados  ')

        flight = ttk.LabelFrame(
            files, text='  VUELO Y DETECCIÓN AUTOMÁTICA  ', style='Settings.TLabelframe', padding=16,
        )
        flight.pack(fill='x')
        flight.columnconfigure(1, weight=1)
        self.path_row(flight, 0, 'Carpeta de fotografías', 'photos', True)
        ttk.Button(
            flight, text='Detectar archivos del vuelo', style='Accent.TButton', command=self.detect,
        ).grid(row=1, column=1, sticky='w', pady=(2, 6))
        ttk.Label(
            flight, text='Busca automáticamente el RINEX del dron, el archivo MRK y la carpeta Archivos GNSS.',
            style='Hint.Card.TLabel', wraplength=860,
        ).grid(row=2, column=0, columnspan=3, sticky='w', pady=(5, 0))

        inputs = ttk.LabelFrame(
            files, text='  ARCHIVOS GNSS  ', style='Settings.TLabelframe', padding=16,
        )
        inputs.pack(fill='x', pady=12)
        inputs.columnconfigure(1, weight=1)
        self.path_row(inputs, 0, 'Observaciones del dron', 'rover')
        self.path_row(inputs, 1, 'Observaciones de la base', 'base')
        self.path_row(inputs, 2, 'Disparos de cámara · MRK', 'mrk')
        ttk.Label(inputs, text='Navegación satelital', style='Field.Card.TLabel').grid(
            row=3, column=0, sticky='nw', padx=(0, 14), pady=8,
        )
        self.navlist = tk.Listbox(
            inputs, height=4, background='white', foreground='#263746', font=('Segoe UI', 9),
            selectmode='extended', relief='flat', highlightthickness=1,
            highlightbackground='#C7D4DC', selectbackground='#CDE8F2', selectforeground='#173B5F',
        )
        self.navlist.grid(row=3, column=1, sticky='ew', pady=8)
        navbuttons = ttk.Frame(inputs, style='Card.TFrame')
        navbuttons.grid(row=3, column=2, sticky='n', padx=(8, 0), pady=8)
        ttk.Button(navbuttons, text='Seleccionar…', style='Secondary.TButton', command=self.select_nav).pack(fill='x')
        ttk.Button(navbuttons, text='Quitar', style='Secondary.TButton', command=self.remove_nav).pack(fill='x', pady=(6, 0))

        output = ttk.LabelFrame(
            files, text='  DESTINO DE RESULTADOS  ', style='Settings.TLabelframe', padding=16,
        )
        output.pack(fill='x')
        output.columnconfigure(1, weight=1)
        self.path_row(output, 0, 'Carpeta de resultados', 'output', True)
        ttk.Label(
            output,
            text='Cada ejecución crea una carpeta independiente. Los originales permanecen intactos y las copias JPG no se recomprimen.',
            style='Hint.Card.TLabel', wraplength=900,
        ).grid(row=1, column=0, columnspan=3, sticky='w', pady=(6, 0))

        basebox = ttk.LabelFrame(
            opts, text='  COORDENADAS DE LA ESTACIÓN BASE  ', style='Settings.TLabelframe', padding=16,
        )
        basebox.pack(fill='x')
        for text, value in [
            ('Usar coordenadas aproximadas del encabezado RINEX', 'header'),
            ('ICHI · coordenadas publicadas por INEGI en 2026', 'ichi'),
            ('Capturar manualmente las coordenadas de la placa', 'manual'),
        ]:
            ttk.Radiobutton(
                basebox, text=text, variable=self.base_mode, value=value, style='Settings.TRadiobutton',
            ).pack(anchor='w', pady=2)
        coord = ttk.Frame(basebox, style='Card.TFrame')
        coord.pack(fill='x', pady=(12, 0))
        for i, (label, key) in enumerate([
            ('Latitud °', 'base_lat'), ('Longitud ° · oeste negativa', 'base_lon'),
            ('Altura elipsoidal m', 'base_h'),
        ]):
            coord.columnconfigure(i, weight=1)
            ttk.Label(coord, text=label, style='Field.Card.TLabel').grid(row=0, column=i, sticky='w', padx=(0, 15))
            ttk.Entry(coord, textvariable=self.vars[key]).grid(row=1, column=i, sticky='ew', padx=(0, 15), pady=(4, 0))
        antenna = ttk.Frame(basebox, style='Card.TFrame')
        antenna.pack(fill='x', pady=(12, 0))
        ttk.Label(antenna, text='Altura de antena (m)', style='Field.Card.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Entry(antenna, textvariable=self.vars['antenna_height'], width=14).grid(row=1, column=0, sticky='w', pady=(4, 0))
        ttk.Label(antenna, text='Tipo de antena', style='Field.Card.TLabel').grid(row=0, column=1, sticky='w', padx=(18, 0))
        ttk.Entry(antenna, textvariable=self.vars['antenna_type'], width=32).grid(row=1, column=1, sticky='w', padx=(18, 0), pady=(4, 0))
        ttk.Label(
            basebox,
            text='Deja la altura vacía para usar el RINEX. ICHI fija 0.188 m y usa ITRF2008 época 2010.0. Las alturas de salida son elipsoidales.',
            style='Hint.Card.TLabel', wraplength=900,
        ).pack(anchor='w', pady=(12, 0))

        quality = ttk.LabelFrame(
            opts, text='  MODELOS DE CÁLCULO  ', style='Settings.TLabelframe', padding=16,
        )
        quality.pack(fill='x', pady=12)
        model_grid = ttk.Frame(quality, style='Card.TFrame')
        model_grid.pack(fill='x')
        model_grid.columnconfigure(2, weight=1)
        model_rows = [
            ('Modelo ionosférico', self.iono, ('auto', 'brdc', 'est-stec'), 'auto selecciona EST-STEC para bases distantes.'),
            ('Antena → cámara', self.lever, ('auto', 'mrk', 'antenna'), 'auto usa offsets MRK o el modelo nominal del Phantom 4 RTK.'),
        ]
        for row, (label, variable, values, hint) in enumerate(model_rows):
            ttk.Label(model_grid, text=label, style='Field.Card.TLabel').grid(row=row, column=0, sticky='w', pady=5)
            ttk.Combobox(model_grid, textvariable=variable, values=values, state='readonly', width=16).grid(
                row=row, column=1, sticky='w', padx=12, pady=5,
            )
            ttk.Label(model_grid, text=hint, style='Hint.Card.TLabel').grid(row=row, column=2, sticky='w', pady=5)
        ttk.Separator(quality).pack(fill='x', pady=12)
        ttk.Checkbutton(
            quality,
            text='Permitir la exportación de fotografías PROVISIONALES',
            variable=self.provisional,
            style='Settings.TCheckbutton',
        ).pack(anchor='w')
        ttk.Label(
            quality,
            text='Incluye FLOAT, desacuerdos entre soluciones o compensaciones aproximadas. Se guardan aparte con RtkFlag=0.',
            style='Hint.Card.TLabel', foreground='#A46A00', wraplength=900,
        ).pack(anchor='w', pady=(4, 0))

        limits_box = ttk.LabelFrame(
            opts, text='  TOLERANCIAS DE CONTROL  ', style='Settings.TLabelframe', padding=16,
        )
        limits_box.pack(fill='x')
        limits = ttk.Frame(limits_box, style='Card.TFrame')
        limits.pack(fill='x')
        for i, (label, key) in enumerate([
            ('UTC de la cámara (h)', 'time_offset'), ('Hueco máximo rover (s)', 'max_gap'),
            ('Acuerdo horizontal (m)', 'max_fb_h'), ('Acuerdo vertical (m)', 'max_fb_v'),
        ]):
            limits.columnconfigure(i, weight=1)
            ttk.Label(limits, text=label, style='Field.Card.TLabel').grid(row=0, column=i, sticky='w', padx=(0, 18))
            ttk.Entry(limits, textvariable=self.vars[key], width=16).grid(
                row=1, column=i, sticky='ew', padx=(0, 18), pady=(4, 0),
            )

        summary_card = ttk.Frame(results, style='Soft.TFrame', padding=12)
        summary_card.pack(fill='x', pady=(0, 10))
        self.summary = ttk.Label(
            summary_card, text='Aquí se mostrarán la revisión de archivos y el resumen del procesamiento.',
            style='Soft.TLabel', wraplength=950,
        )
        self.summary.pack(fill='x')

        table_card = ttk.Frame(results, style='Card.TFrame', padding=12)
        table_card.pack(fill='both', expand=True)
        ttk.Label(table_card, text='CALIDAD POR FOTOGRAFÍA', style='Section.TLabel').pack(anchor='w', pady=(0, 8))
        cols = ('foto', 'estado', 'h', 'dh', 'dv')
        tableframe = ttk.Frame(table_card, style='Card.TFrame')
        tableframe.pack(fill='both', expand=True)
        self.table = ttk.Treeview(tableframe, columns=cols, show='headings', height=5)
        for col, title, width in [
            ('foto', 'Fotografía', 220), ('estado', 'Calidad', 230), ('h', 'h elipsoidal (m)', 130),
            ('dh', 'Δ H adelante/atrás', 155), ('dv', 'Δ V adelante/atrás', 155),
        ]:
            self.table.heading(col, text=title)
            self.table.column(col, width=width, anchor='w' if col in ('foto', 'estado') else 'e', stretch=True)
        self.table.tag_configure('provisional', foreground='#9A4D00')
        self.table.tag_configure('fix', foreground='#087548')
        scroll = ttk.Scrollbar(tableframe, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        ttk.Label(table_card, text='REGISTRO DEL PROCESO', style='Section.TLabel').pack(anchor='w', pady=(12, 6))
        self.logbox = ScrolledText(
            table_card, height=5, font=('Consolas', 9), background='#142C3B', foreground='#D4E7EE',
            insertbackground='white', relief='flat', padx=9, pady=7, state='disabled',
        )
        self.logbox.pack(fill='x')

        bottom = ttk.Frame(self, style='Card.TFrame', padding=(18, 10, 18, 12))
        bottom.grid(row=3, column=0, sticky='ew')
        self.status_label = ttk.Label(bottom, textvariable=self.status, style='Hint.Card.TLabel', wraplength=900)
        self.status_label.pack(anchor='w', fill='x', pady=(0, 7))
        self.progress = ttk.Progressbar(bottom, mode='determinate', value=0)
        self.progress.pack(fill='x', pady=(0, 9))
        buttons = ttk.Frame(bottom, style='Card.TFrame')
        buttons.pack(fill='x')
        self.inspect_btn = ttk.Button(
            buttons, text='Revisar archivos', style='Secondary.TButton', command=self.inspect,
        )
        self.inspect_btn.pack(side='left')
        self.run_btn = ttk.Button(
            buttons, text='Procesar y exportar fotos', style='Accent.TButton', command=self.start,
        )
        self.run_btn.pack(side='left', padx=8)
        self.cancel_btn = ttk.Button(
            buttons, text='Cancelar', style='Secondary.TButton', command=self.cancel.set, state='disabled',
        )
        self.cancel_btn.pack(side='left')
        self.open_btn = ttk.Button(
            buttons, text='Abrir resultados', style='Secondary.TButton', command=self.open_results, state='disabled',
        )
        self.open_btn.pack(side='right')
        bottom.bind('<Configure>', lambda event: self.status_label.configure(wraplength=max(360, event.width - 42)))

    def path_row(self,parent,row,label,key,directory=False):
        ttk.Label(parent, text=label, style='Field.Card.TLabel').grid(
            row=row, column=0, sticky='w', padx=(0, 14), pady=8,
        )
        ttk.Entry(parent, textvariable=self.vars[key]).grid(row=row, column=1, sticky='ew', pady=8)
        ttk.Button(
            parent, text='Seleccionar…', style='Secondary.TButton', command=lambda: self.browse(key, directory),
        ).grid(row=row, column=2, padx=(8, 0), pady=8)

    def browse(self,key,directory):
        if self.busy: return
        initial=self.vars[key].get() or self.vars['photos'].get() or str(category_dir('ppk'))
        if not Path(initial).is_dir(): initial=str(Path(initial).parent)
        value=filedialog.askdirectory(initialdir=initial) if directory else filedialog.askopenfilename(initialdir=initial)
        if value:
            self.vars[key].set(value)
            if key=='photos': self.detect()

    def select_nav(self):
        if self.busy: return
        paths=filedialog.askopenfilenames(title='Seleccionar navegación GPS / GLONASS / Galileo / BeiDou',initialdir=self.vars['photos'].get())
        if paths:
            self.nav=list(paths); self.refresh_nav()

    def remove_nav(self):
        if self.busy: return
        selected=set(self.navlist.curselection()); self.nav=[v for i,v in enumerate(self.nav) if i not in selected]; self.refresh_nav()

    def refresh_nav(self):
        self.navlist.delete(0,'end')
        for p in self.nav: self.navlist.insert('end',p)

    def apply_settings(self,s):
        values=asdict(s)
        for key,var in self.vars.items(): var.set('' if values[key] is None else str(values[key]))
        self.base_mode.set(s.base_mode); self.iono.set(s.iono); self.lever.set(s.lever); self.provisional.set(s.export_provisional)
        self.nav=list(s.nav or []); self.refresh_nav()
        if s.base_mode=='ichi':
            for key,v in zip(('base_lat','base_lon','base_h'),ICHI): self.vars[key].set(f'{v:.10f}')

    def settings(self):
        v={k:var.get().strip() for k,var in self.vars.items()}
        for key in ('base_lat','base_lon','base_h','time_offset','max_gap','max_fb_h','max_fb_v'):
            try: v[key]=float(v[key].replace(',','.'))
            except ValueError: raise PPKError(f'Valor numérico inválido en {key}.')
        try: v['antenna_height']=float(v['antenna_height'].replace(',','.')) if v['antenna_height'] else None
        except ValueError: raise PPKError('Altura de antena inválida.')
        return Settings(**v,nav=self.nav.copy(),base_mode=self.base_mode.get(),iono=self.iono.get(),lever=self.lever.get(),export_provisional=self.provisional.get())

    def detect(self):
        if self.busy: return
        try:
            s=autodetect(self.vars['photos'].get()); s.output=str(category_dir('ppk')); self.apply_settings(s)
            self.status.set('Archivos detectados. Revisa la base y el criterio de exportación antes de procesar.')
        except Exception as exc: messagebox.showerror('Detección',str(exc))

    def open_project(self):
        if self.busy: return
        path=filedialog.askopenfilename(filetypes=[('Proyecto PPK','*.json')],initialdir=category_dir('ppk'))
        if path:
            try: self.apply_settings(Settings(**json.loads(Path(path).read_text(encoding='utf-8'))))
            except Exception as exc: messagebox.showerror('Proyecto',str(exc))

    def save_project(self):
        try: s=self.settings()
        except Exception as exc: messagebox.showerror('Proyecto',str(exc)); return
        path=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('Proyecto PPK','*.json')],initialdir=category_dir('ppk'))
        if path: Path(path).write_text(json.dumps(asdict(s),indent=2,ensure_ascii=False),encoding='utf-8')

    def log(self,text): self.events.put(('log',text))

    def inspect(self):
        if self.busy: return
        try: s=self.settings()
        except Exception as exc: messagebox.showerror('Revisión',str(exc)); return
        def work():
            try:
                rover,base=rinex_info(s.rover),rinex_info(s.base); events=parse_mrk(s.mrk)
                overlap=max(rover['start'],base['start'])<min(rover['end'],base['end'])
                text=f'Base {base["marker"]} cada {base["interval"]:g} s. Dron cada {rover["interval"]:g} s. {len(events)} disparos. Coincidencia temporal: {"SÍ" if overlap else "NO"}.'
                self.log(text)
                self.log(f'Rover GPST: {gps_datetime(rover["start"])} → {gps_datetime(rover["end"])}')
                self.log(f'Base GPST: {gps_datetime(base["start"])} → {gps_datetime(base["end"])}')
                self.events.put(('inspected',text))
            except Exception as exc: self.events.put(('error',str(exc)))
        self.set_busy(True); threading.Thread(target=work,daemon=True).start()

    def start(self):
        if self.busy: return
        try: s=self.settings()
        except Exception as exc: messagebox.showerror('Datos',str(exc)); return
        self.cancel.clear(); self.set_busy(True)
        self.table.delete(*self.table.get_children())
        self.status.set('Procesando. Puedes cancelar; se conservarán los originales.')
        def worker():
            try: self.events.put(('done',process(s,self.log,self.cancel)))
            except Cancelled as exc: self.events.put(('cancelled',str(exc)))
            except Exception as exc:
                self.log(traceback.format_exc()); self.events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def set_busy(self,value):
        self.busy=value
        self.run_btn.configure(state='disabled' if value else 'normal'); self.inspect_btn.configure(state='disabled' if value else 'normal')
        self.cancel_btn.configure(state='normal' if value else 'disabled')
        self.book.select(2)
        if value:
            self.progress.configure(mode='indeterminate')
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode='determinate', value=0)

    def drain(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='log':
                    self.logbox.configure(state='normal'); self.logbox.insert('end',data+'\n'); self.logbox.see('end'); self.logbox.configure(state='disabled')
                    self.status.set(data.splitlines()[-1][:180])
                elif kind=='done':
                    self.set_busy(False); self.last_result=Path(data['resultado']); self.open_btn.configure(state='normal')
                    summary=f'Exportadas: {data["fotos_exportadas"]} · '+', '.join(f'{k}: {v}' for k,v in data['conteos'].items())
                    self.summary.configure(text=summary); self.status.set('Completado. Abre INFORME.txt para revisar las limitaciones y el uso de las coordenadas.')
                    with (self.last_result/'coordenadas_y_calidad.csv').open(encoding='utf-8-sig',newline='') as f:
                        for row in csv.DictReader(f):
                            def num(key): return f'{float(row[key]):.3f}' if row.get(key) else '—'
                            self.table.insert('','end',values=(row['foto'],row['estado'],num('altura_elipsoidal_m'),num('diferencia_fb_horizontal_m'),num('diferencia_fb_vertical_m')),tags=('fix' if row['estado']=='FIX_CONCORDANTE' else 'provisional',))
                elif kind=='inspected': self.set_busy(False); self.summary.configure(text=data); self.status.set('Revisión de tiempos terminada. El cálculo comprobará la asociación de cada fotografía.')
                elif kind in ('error','cancelled'):
                    self.set_busy(False); self.status.set(data)
                    if kind=='error': messagebox.showerror('No se completó el procesamiento',data)
        except queue.Empty: pass
        self.after(100,self.drain)

    def open_results(self):
        if self.last_result: os.startfile(self.last_result)

    def close(self):
        if self.busy:
            self.cancel.set(); self.status.set('Cancelando antes de cerrar…')
            self.after(200,self.close)
        else: self.on_home()
