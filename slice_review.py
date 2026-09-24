"""Native desktop review of full-resolution, two-channel histology images."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import numpy as np
from PIL import Image, ImageTk
import tifffile

from review_data import AnnotationStore, ViewTransform
from image_source import ImageSource


class Reviewer:
    def __init__(self, root, config, annotations_override=None, auto_gallery=True):
        self.root = root
        self.config_path = Path(config).resolve()
        self.config = json.loads(self.config_path.read_text(encoding='utf-8'))
        self.dataset_name = self.config.get('dataset_name', 'Histology project')
        resolve = lambda key: (self.config_path.parent / self.config[key]).resolve()
        self.image_source = ImageSource(self.config_path)
        self.image_path = self.image_source.input_path
        self.sources = self.image_source.sources
        self.channels = self.image_source.channels
        shape = self.image_source.shape
        self.height, self.width = shape[-2:]
        self.store = AnnotationStore(annotations_override or resolve('annotations_dir'),
                                     self.sources, self.image_path, shape)
        self.index = max(0, min(len(self.sources) - 1, self.store.data.get('last_image_index', 0)))
        self.view = ViewTransform(self.width, self.height)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.load_generation = 0
        self.images = None
        self.point = None
        self.pan_origin = None
        self.photo = None
        self.fit_mode = True
        self.loading_form = False
        self.dirty = False
        self.save_timer = None
        self.render_timer = None
        self.channel = tk.StringVar(value=self.channels[0])
        self.slice_number = tk.StringVar()
        self.brightness = tk.DoubleVar(value=1.)
        self.status = tk.StringVar(value='Choose a slice number, then click its anatomical right hemisphere.')
        self.point_text = tk.StringVar(value='No right-side point yet')
        self.progress = tk.StringVar()
        self.title_text = tk.StringVar()
        self.duplicate_text = tk.StringVar()
        self.zoom_text = tk.StringVar()
        self.gallery = None
        self.export_future = None
        self._build()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<Control-Return>', lambda e: self.save_next())
        self.root.bind('<Control-s>', lambda e: self.save_current())
        self.slice_number.trace_add('write', self.changed)
        self.load(self.index)
        if auto_gallery:
            self.root.after(250, self.show_gallery)

    def _build(self):
        self.root.title('Slice review — ' + self.dataset_name)
        self.root.geometry('1380x900')
        self.root.minsize(1040, 680)
        self.root.configure(bg='#edf1f5')
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), background='#edf1f5')
        style.configure('TButton', padding=(12, 7))
        style.configure('Heading.TLabel', font=('Segoe UI Semibold', 17))
        style.configure('Accent.TButton', background='#166c76', foreground='white', font=('Segoe UI Semibold', 11))
        style.map('Accent.TButton', background=[('active', '#125761')])
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill='both', expand=True)
        top = ttk.Frame(outer)
        top.pack(fill='x', pady=(0, 12))
        ttk.Label(top, text='Slice review', style='Heading.TLabel').pack(side='left')
        ttk.Button(top, text='All images · set slice order', command=self.show_gallery).pack(side='left', padx=24)
        self.export_button = ttk.Button(top, text='Export ordered TIFF', command=self.export_stack)
        self.export_button.pack(side='left')
        ttk.Label(top, textvariable=self.progress).pack(side='right')
        middle = ttk.Frame(outer)
        middle.pack(fill='both', expand=True)
        left = ttk.Frame(middle, width=190)
        left.pack(side='left', fill='y', padx=(0, 12))
        ttk.Label(left, text='SCANNED IMAGES', font=('Segoe UI Semibold', 10)).pack(anchor='w', pady=(0, 8))
        self.listbox = tk.Listbox(left, width=24, bg='white', fg='#243442',
                                 selectbackground='#166c76', selectforeground='white',
                                 font=('Segoe UI', 10), borderwidth=0, highlightthickness=0,
                                 activestyle='none', exportselection=False)
        self.listbox.pack(fill='both', expand=True)
        self.listbox.bind('<<ListboxSelect>>', self.select_list)
        ttk.Label(left, text='✓  reviewed\n•  draft\n\nSlice numbers may repeat\nfor scans of the same tissue.',
                  justify='left', foreground='#536574').pack(anchor='w', pady=(12, 0))
        center = ttk.Frame(middle)
        center.pack(side='left', fill='both', expand=True)
        ttk.Label(center, textvariable=self.title_text, font=('Segoe UI Semibold', 12)).pack(anchor='w', pady=(0, 8))
        toolbar = ttk.Frame(center)
        toolbar.pack(fill='x', pady=(0, 8))
        for channel in (*self.channels, 'Overlay'):
            ttk.Radiobutton(toolbar, text=channel, value=channel, variable=self.channel,
                            command=self.queue_render).pack(side='left', padx=(0, 12))
        ttk.Button(toolbar, text='Fit', command=self.fit).pack(side='right')
        ttk.Button(toolbar, text='1:1', command=self.native_zoom).pack(side='right', padx=6)
        self.canvas = tk.Canvas(center, bg='#10171e', highlightthickness=0, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', self.resized)
        self.canvas.bind('<Button-1>', self.mark_point)
        self.canvas.bind('<MouseWheel>', self.wheel)
        for button in (2, 3):
            self.canvas.bind(f'<ButtonPress-{button}>', self.pan_start)
            self.canvas.bind(f'<B{button}-Motion>', self.pan_move)
        lower = ttk.Frame(center)
        lower.pack(fill='x', pady=(8, 0))
        ttk.Label(lower, text='Click: anatomical right  ·  Wheel: zoom  ·  Right-drag: pan', foreground='#536574').pack(side='left')
        ttk.Label(lower, textvariable=self.zoom_text).pack(side='right')
        right = ttk.Frame(middle, width=250, padding=(16, 0, 0, 0))
        right.pack(side='right', fill='y')
        right.pack_propagate(False)
        ttk.Label(right, text='1   Set the slice number', font=('Segoe UI Semibold', 11)).pack(anchor='w', pady=(0, 8))
        self.entry = ttk.Spinbox(right, from_=1, to=99999, textvariable=self.slice_number, width=12,
                                 font=('Segoe UI', 19))
        self.entry.pack(fill='x')
        ttk.Label(right, text='Use your tissue order: 1, 2, 3…', foreground='#536574').pack(anchor='w', pady=(6, 0))
        ttk.Label(right, textvariable=self.duplicate_text, wraplength=224, foreground='#826015').pack(anchor='w', pady=(6, 18))
        ttk.Label(right, text='2   Mark anatomical right', font=('Segoe UI Semibold', 11)).pack(anchor='w', pady=(0, 8))
        ttk.Label(right, text='Click a point inside the right\nhemisphere of this tissue slice.', justify='left').pack(anchor='w')
        ttk.Label(right, textvariable=self.point_text, foreground='#166c76', wraplength=224).pack(anchor='w', pady=(10, 8))
        ttk.Button(right, text='Clear point', command=self.clear_point).pack(fill='x')
        ttk.Separator(right).pack(fill='x', pady=20)
        ttk.Label(right, text='View brightness', font=('Segoe UI Semibold', 10)).pack(anchor='w')
        ttk.Scale(right, from_=0.25, to=4., variable=self.brightness,
                  command=lambda _: self.queue_render()).pack(fill='x', pady=(6, 0))
        ttk.Button(right, text='Reset brightness', command=self.reset_brightness).pack(fill='x', pady=6)
        ttk.Label(right, text=f'{self.channels[0]} and {self.channels[1]} show one channel\nat a time in grayscale.\nOverlay: first green, second red.', foreground='#536574', justify='left').pack(anchor='w', pady=(4, 0))
        bottom = ttk.Frame(outer)
        bottom.pack(fill='x', pady=(14, 0))
        ttk.Button(bottom, text='Previous', command=lambda: self.navigate(-1)).pack(side='left')
        ttk.Button(bottom, text='Skip for now', command=lambda: self.navigate(1)).pack(side='left', padx=8)
        self.next_button = ttk.Button(bottom, text='Save & next →', style='Accent.TButton', command=self.save_next)
        self.next_button.pack(side='right')
        ttk.Button(bottom, text='Save draft', command=self.save_current).pack(side='right', padx=8)
        ttk.Label(outer, textvariable=self.status, foreground='#37586b', wraplength=1260).pack(anchor='w', pady=(10, 0))

    def short_name(self, index):
        return f"{index + 1:02d}"

    def show_gallery(self):
        if self.gallery is not None and self.gallery.winfo_exists():
            self.gallery.lift()
            return
        if self.dirty and not self.save_current():
            return
        preview_dir = self.config_path.parent / self.config.get('preview_dir', 'previews')
        if not all((preview_dir / self.image_source.preview_filename(i, 0)).exists() for i in range(len(self.sources))):
            self.status.set('The all-images previews are not ready. You can continue reviewing individual images.')
            return
        gallery = self.gallery = tk.Toplevel(self.root)
        gallery.title('All images — set slice order — ' + self.dataset_name)
        gallery.geometry('1320x940')
        gallery.minsize(1160, 820)
        gallery.configure(bg='#edf1f5')
        heading = ttk.Frame(gallery, padding=(16, 12))
        heading.pack(fill='x')
        ttk.Label(heading, text='Set the slice order together', style='Heading.TLabel').pack(side='left')
        self.gallery_channel = tk.StringVar(value=self.channels[0])
        for mode in (*self.channels, 'Overlay'):
            ttk.Radiobutton(heading, text=mode, value=mode, variable=self.gallery_channel,
                            command=self.render_gallery).pack(side='right', padx=8)
        ttk.Label(gallery, text='Enter each tissue slice number below its image. Click an image to review its right-side point. Repeated numbers are allowed.',
                  padding=(16, 0, 16, 8)).pack(anchor='w')
        container = ttk.Frame(gallery)
        container.pack(fill='both', expand=True)
        scroll_canvas = tk.Canvas(container, highlightthickness=0, bg='#edf1f5')
        scrollbar = ttk.Scrollbar(container, orient='vertical', command=scroll_canvas.yview)
        scrollbar.pack(side='right', fill='y')
        scroll_canvas.pack(side='left', fill='both', expand=True)
        scroll_canvas.configure(yscrollcommand=scrollbar.set)
        self.gallery_grid = ttk.Frame(scroll_canvas, padding=(12, 0))
        grid_window = scroll_canvas.create_window((0, 0), window=self.gallery_grid, anchor='nw')
        self.gallery_grid.bind('<Configure>', lambda event: scroll_canvas.configure(scrollregion=scroll_canvas.bbox('all')))
        scroll_canvas.bind('<Configure>', lambda event: scroll_canvas.itemconfigure(grid_window, width=event.width))
        self.gallery_vars, self.gallery_canvases, self.gallery_photos = [], [], []
        self.gallery_base = {}
        for i in range(len(self.sources)):
            card = ttk.Frame(self.gallery_grid, padding=5)
            card.grid(row=i // 4, column=i % 4, sticky='nsew')
            self.gallery_grid.columnconfigure(i % 4, weight=1, uniform='cards')
            self.gallery_grid.rowconfigure(i // 4, weight=1, minsize=180, uniform='rows')
            cv = tk.Canvas(card, background='#10171e', highlightthickness=0, height=145, width=220, cursor='hand2')
            cv.pack(fill='both', expand=True)
            cv.bind('<Button-1>', lambda event, index=i: self.gallery_open_image(index))
            cv.bind('<Configure>', lambda event: self.render_gallery())
            self.gallery_canvases.append(cv)
            controls = ttk.Frame(card)
            controls.pack(fill='x', pady=(4, 0))
            ttk.Label(controls, text=f'{i + 1:02d} · Scan {self.short_name(i)}').pack(side='left')
            value = self.store.data['annotations'][i]['slice_index']
            variable = tk.StringVar(value='' if value is None else str(value))
            self.gallery_vars.append(variable)
            ttk.Spinbox(controls, from_=1, to=99999, width=5, textvariable=variable,
                        font=('Segoe UI Semibold', 12)).pack(side='right')
            ttk.Label(controls, text='Slice ').pack(side='right')
            for c, mode in enumerate(self.channels):
                with Image.open(preview_dir / self.image_source.preview_filename(i, c)) as im:
                    self.gallery_base[(i, mode)] = im.copy()
        bottom = ttk.Frame(gallery, padding=(16, 10))
        bottom.pack(fill='x')
        self.gallery_status = tk.StringVar(value='Numbers are saved when you click Save order or open an individual image.')
        ttk.Label(bottom, textvariable=self.gallery_status, wraplength=850).pack(side='left')
        ttk.Button(bottom, text='Save order', style='Accent.TButton', command=self.save_gallery).pack(side='right')
        ttk.Button(bottom, text='Export ordered TIFF', command=self.export_stack).pack(side='right', padx=8)
        gallery.protocol('WM_DELETE_WINDOW', self.close_gallery)
        gallery.after(100, self.render_gallery)

    def render_gallery(self):
        if self.gallery is None or not self.gallery.winfo_exists():
            return
        photos = []
        mode = self.gallery_channel.get()
        for i, cv in enumerate(self.gallery_canvases):
            if mode == 'Overlay':
                g, r = self.gallery_base[(i, self.channels[0])], self.gallery_base[(i, self.channels[1])]
                im = Image.merge('RGB', (r, g, Image.new('L', r.size)))
            else:
                im = self.gallery_base[(i, mode)].copy()
            im.thumbnail((max(1, cv.winfo_width() - 8), max(1, cv.winfo_height() - 8)), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(im)
            photos.append(photo)
            cv.delete('all')
            ox, oy = (cv.winfo_width() - im.width) / 2, (cv.winfo_height() - im.height) / 2
            cv.create_image(ox, oy, image=photo, anchor='nw')
            point = self.store.data['annotations'][i]['right_point']
            if point:
                x = ox + (point['stack_x_px'] + .5) / self.width * im.width
                y = oy + (point['stack_y_px'] + .5) / self.height * im.height
                cv.create_oval(x - 5, y - 5, x + 5, y + 5, outline='#ffd34e', width=2)
                cv.create_text(x + 12, y - 10, text='R', fill='#ffd34e', font=('Segoe UI', 10, 'bold'))
        self.gallery_photos = photos

    def save_gallery(self):
        values = []
        try:
            for i, variable in enumerate(self.gallery_vars):
                value = variable.get().strip()
                if value and (not value.isascii() or not value.isdecimal() or int(value) < 1):
                    raise ValueError(f'Image {i + 1}: enter a whole slice number of 1 or higher.')
                values.append(int(value) if value else None)
            for i, value in enumerate(values):
                item = self.store.data['annotations'][i]
                if item['slice_index'] != value:
                    point = item['right_point']
                    coords = None if point is None else (point['stack_x_px'], point['stack_y_px'])
                    self.store.set_annotation(i, value, coords, reviewed=value is not None and point is not None)
            self.store.save(self.index)
            self.loading_form = True
            self.slice_number.set('' if values[self.index] is None else str(values[self.index]))
            self.loading_form = False
            self.refresh_list()
            self.update_duplicates()
            self.gallery_status.set('Order saved ✓  ' + str(self.store.directory))
            self.status.set('Order saved ✓  ' + str(self.store.directory))
            return True
        except Exception as exc:
            messagebox.showerror('Order not saved', str(exc), parent=self.gallery)
            return False

    def gallery_open_image(self, index):
        if self.save_gallery():
            self.gallery.destroy()
            self.gallery = None
            self.load(index)
            self.root.lift()

    def close_gallery(self):
        if self.save_gallery():
            self.gallery.destroy()
            self.gallery = None

    def refresh_list(self):
        self.listbox.delete(0, 'end')
        for i, item in enumerate(self.store.data['annotations']):
            mark = '✓' if item['reviewed'] else ('•' if item['slice_index'] or item['right_point'] else ' ')
            number = f"  → {item['slice_index']}" if item['slice_index'] else ''
            self.listbox.insert('end', f'{mark}  {i + 1:02d}   Scan {self.short_name(i)}{number}')
        self.listbox.selection_set(self.index)
        self.listbox.see(self.index)
        count = sum(bool(a['reviewed']) for a in self.store.data['annotations'])
        self.progress.set(f'{count} of {len(self.sources)} reviewed')

    def load(self, index):
        self.index = index
        self.loading_form = True
        item = self.store.data['annotations'][index]
        self.slice_number.set('' if item['slice_index'] is None else str(item['slice_index']))
        p = item['right_point']
        self.point = None if p is None else (p['stack_x_px'], p['stack_y_px'])
        self.loading_form = False
        self.dirty = False
        self.update_point_text()
        self.update_duplicates()
        self.refresh_list()
        self.next_button.configure(text='Save & finish ✓' if index == len(self.sources) - 1 else 'Save & next →')
        self.title_text.set(f'Image {index + 1} / {len(self.sources)}  ·  {self.sources[index]["matching_export"]}')
        self.images = None
        self.fit_mode = True
        self.canvas.delete('all')
        self.canvas.create_text(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2,
                                text='Loading full-resolution image…', fill='#d9e3e9', font=('Segoe UI', 14))
        self.load_generation += 1
        generation = self.load_generation
        if self.future:
            self.future.cancel()
        self.future = self.executor.submit(self.read_section, index)
        self.root.after(50, lambda: self.check_loaded(generation, self.future))

    def read_section(self, index):
        return [self.image_source.display_image(index, c) for c in range(2)]

    def check_loaded(self, generation, future):
        if generation != self.load_generation:
            return
        if not future.done():
            self.root.after(80, lambda: self.check_loaded(generation, future))
            return
        try:
            self.images = future.result()
            self.fit()
            self.status.set('Click inside the anatomical right hemisphere. Your edits save automatically.')
        except Exception as exc:
            self.status.set(f'Could not load image: {exc}')
            messagebox.showerror('Image could not be loaded', str(exc), parent=self.root)

    def queue_render(self, *args):
        if self.render_timer:
            self.root.after_cancel(self.render_timer)
        self.render_timer = self.root.after(25, self.render)

    def render(self):
        self.render_timer = None
        if self.images is None:
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        left = max(0, math.floor(-self.view.ox / self.view.scale))
        top = max(0, math.floor(-self.view.oy / self.view.scale))
        right = min(self.width, math.ceil((cw - self.view.ox) / self.view.scale))
        bottom = min(self.height, math.ceil((ch - self.view.oy) / self.view.scale))
        self.canvas.delete('all')
        if right > left and bottom > top:
            size = (max(1, round((right - left) * self.view.scale)), max(1, round((bottom - top) * self.view.scale)))
            gain = self.brightness.get()
            lut = [min(255, round(v * gain)) for v in range(256)]
            def crop(channel):
                return self.images[channel].crop((left, top, right, bottom)).resize(size, Image.Resampling.BILINEAR).point(lut)
            if self.channel.get() == 'Overlay':
                frame = Image.merge('RGB', (crop(1), crop(0), Image.new('L', size)))
            else:
                frame = crop(self.channels.index(self.channel.get()))
            self.photo = ImageTk.PhotoImage(frame)
            self.canvas.create_image(*self.view.canvas_at(left, top), image=self.photo, anchor='nw')
        self.draw_marker()
        self.zoom_text.set(f'{self.view.scale * 100:.0f}%')

    def draw_marker(self):
        self.canvas.delete('marker')
        if self.point is not None:
            x, y = self.view.canvas_at(self.point[0] + .5, self.point[1] + .5)
            self.canvas.create_oval(x - 12, y - 12, x + 12, y + 12, outline='#000000', width=5, tags='marker')
            self.canvas.create_oval(x - 12, y - 12, x + 12, y + 12, outline='#ffd34e', width=2, tags='marker')
            self.canvas.create_line(x - 18, y, x + 18, y, fill='#ffd34e', width=2, tags='marker')
            self.canvas.create_line(x, y - 18, x, y + 18, fill='#ffd34e', width=2, tags='marker')
            self.canvas.create_text(x + 22, y - 22, text='R', fill='#ffd34e', font=('Segoe UI', 20, 'bold'), tags='marker')

    def fit(self):
        self.fit_mode = True
        self.view.fit(self.canvas.winfo_width(), self.canvas.winfo_height())
        self.queue_render()

    def native_zoom(self):
        self.fit_mode = False
        self.view.zoom(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, 1. / self.view.scale)
        self.queue_render()

    def resized(self, event):
        if self.fit_mode:
            self.view.fit(event.width, event.height)
        self.queue_render()

    def wheel(self, event):
        if self.images is not None:
            self.fit_mode = False
            self.view.zoom(event.x, event.y, 1.25 if event.delta > 0 else 0.8)
            self.queue_render()

    def pan_start(self, event):
        self.pan_origin = (event.x, event.y, self.view.ox, self.view.oy)

    def pan_move(self, event):
        if self.pan_origin and self.images is not None:
            self.fit_mode = False
            x, y, ox, oy = self.pan_origin
            self.view.ox, self.view.oy = ox + event.x - x, oy + event.y - y
            self.queue_render()

    def reset_brightness(self):
        self.brightness.set(1.)
        self.queue_render()

    def mark_point(self, event):
        if self.images is None:
            return
        x, y = map(math.floor, self.view.image_at(event.x, event.y))
        s = self.sources[self.index]
        if not (s['padding_left'] <= x < s['padding_left'] + s['tile_grid_width'] and
                s['padding_top'] <= y < s['padding_top'] + s['tile_grid_height']):
            self.status.set('Click inside the scan image, away from the added black border.')
            return
        self.point = (x, y)
        self.update_point_text()
        self.draw_marker()
        self.changed()

    def clear_point(self):
        self.point = None
        self.update_point_text()
        self.draw_marker()
        self.changed()

    def update_point_text(self):
        self.point_text.set('No right-side point yet' if self.point is None else
                            f'Right hemisphere marked\nx {self.point[0]:,}  ·  y {self.point[1]:,} px')

    def update_duplicates(self):
        number = self.slice_number.get().strip()
        matches = [str(i + 1) for i, a in enumerate(self.store.data['annotations'])
                   if i != self.index and str(a['slice_index']) == number]
        self.duplicate_text.set('Also assigned to image ' + ', '.join(matches) + '. Repeated numbers are allowed.' if matches else '')

    def changed(self, *args):
        if self.loading_form:
            return
        self.dirty = True
        self.update_duplicates()
        if self.save_timer:
            self.root.after_cancel(self.save_timer)
        self.save_timer = self.root.after(600, self.autosave)

    def autosave(self):
        self.save_timer = None
        self.save_current(quiet=True)

    def save_current(self, reviewed=False, quiet=False):
        if self.save_timer:
            self.root.after_cancel(self.save_timer)
            self.save_timer = None
        value = self.slice_number.get().strip()
        try:
            if value and (not value.isascii() or not value.isdecimal() or int(value) < 1):
                raise ValueError('Enter a whole slice number of 1 or higher.')
            number = int(value) if value else None
            self.store.set_annotation(self.index, number, self.point, reviewed=reviewed)
            self.store.save(self.index)
            self.dirty = False
            self.refresh_list()
            self.status.set('Saved ✓  ' + str(self.store.directory))
            return True
        except Exception as exc:
            self.status.set(f'Not saved: {exc}')
            if not quiet:
                messagebox.showerror('Annotation not saved', str(exc), parent=self.root)
            return False

    def select_list(self, event):
        selection = self.listbox.curselection()
        if selection and selection[0] != self.index:
            target = selection[0]
            if self.save_current():
                self.load(target)
            else:
                self.refresh_list()

    def navigate(self, direction):
        target = max(0, min(len(self.sources) - 1, self.index + direction))
        if target != self.index and self.save_current():
            self.load(target)

    def save_next(self):
        if not self.save_current(reviewed=True):
            return
        if self.index < len(self.sources) - 1:
            self.load(self.index + 1)
        else:
            remaining = sum(not a['reviewed'] for a in self.store.data['annotations'])
            messagebox.showinfo('Review saved',
                (f'Your review is saved. {remaining} image(s) still need review.' if remaining else f'All {len(self.sources)} images are reviewed and saved.')
                + f'\n\nAnnotations:\n{self.store.directory}', parent=self.root)

    def export_stack(self):
        if self.export_future is not None and not self.export_future.done():
            return
        if self.gallery is not None and self.gallery.winfo_exists() and not self.save_gallery():
            return
        if not self.save_current():
            return
        from export_ordered import export, make_plan
        import copy
        snapshot = copy.deepcopy(self.store.data)
        try:
            make_plan(snapshot['annotations'], self.width)
        except ValueError as exc:
            messagebox.showerror('Review incomplete', str(exc), parent=self.root)
            return
        filename = filedialog.asksaveasfilename(parent=self.root, title='Export with anatomical right on image right',
            initialdir=self.store.directory, initialfile=self.image_path.stem + '_ordered_right_on_right' + ('.ome.tif' if self.image_source.scanner else '.tif'),
            defaultextension='.tif', filetypes=[('TIFF stack', '*.tif')], confirmoverwrite=False)
        if not filename:
            return
        self.export_button.configure(state='disabled')
        self.status.set('Exporting and verifying every pixel… Review edits will apply to the next export.')
        self.export_future = self.executor.submit(export, self.config_path, Path(filename), snapshot)
        self.root.after(200, self.check_export)

    def check_export(self):
        if not self.export_future.done():
            self.root.after(200, self.check_export)
            return
        self.export_button.configure(state='normal')
        try:
            output = self.export_future.result()
            self.status.set('Export verified and saved: ' + str(output))
            messagebox.showinfo('Export complete', 'Both channels were saved and every output page verified.\n\n' + str(output), parent=self.root)
        except Exception as exc:
            self.status.set('Export failed: ' + str(exc))
            messagebox.showerror('Export failed', str(exc), parent=self.root)

    def close(self):
        if self.export_future is not None and not self.export_future.done():
            messagebox.showinfo('Export in progress', 'Please wait for the verified export to finish before closing.', parent=self.root)
            return
        if self.gallery is not None and self.gallery.winfo_exists():
            if not self.save_gallery():
                return
            self.gallery.destroy()
            self.gallery = None
        if self.dirty and not self.save_current():
            return
        # Persist navigation without creating scientific labels for untouched images.
        try:
            self.store.save(self.index)
        except Exception as exc:
            messagebox.showerror('Could not save progress', str(exc), parent=self.root)
            return
        self.load_generation += 1
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.store.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    parser.add_argument('--annotations-dir', type=Path, help='Override output folder (also used for isolated QA).')
    args = parser.parse_args()
    root = tk.Tk()
    try:
        Reviewer(root, args.config, args.annotations_dir)
    except Exception as exc:
        messagebox.showerror('Slice review could not start', str(exc), parent=root)
        root.destroy()
        raise
    root.mainloop()


if __name__ == '__main__':
    main()
