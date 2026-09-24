"""User-facing startup, with input selection and automatic preview preparation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from project import locate_stack, prepare_project
from create_previews import create_previews
from slice_review import Reviewer


def main():
    parser = argparse.ArgumentParser(description='Review, number, orient and export two-channel histology TIFF stacks.')
    parser.add_argument('--input', type=Path, help='Scanner VSI directory, VSI file, or prepared two-channel TIFF.')
    parser.add_argument('--config', type=Path, help='Existing reviewer config (advanced).')
    parser.add_argument('--version', action='version', version='Histology Slice Reviewer 1.1.1')
    parser.add_argument('--self-test', type=Path, help='Run a synthetic smoke test and write its JSON report here.')
    args = parser.parse_args()
    if args.self_test:
        from self_test import run
        run(args.self_test)
        return
    root = tk.Tk()
    root.withdraw()
    config = args.config
    image_path = args.input
    if not config:
        if image_path is None:
            chosen = filedialog.askdirectory(title='Choose your scanner folder (VSI + companion folder), or TIFF folder', parent=root)
            if not chosen:
                root.destroy()
                return
            image_path = Path(chosen)
        if image_path.is_dir():
            try:
                image_path = locate_stack(image_path)
            except ValueError:
                chosen = filedialog.askopenfilename(title='Select a scanner VSI or full-resolution two-channel TIFF',
                    initialdir=image_path, filetypes=[('Scanner / TIFF images', '*.vsi *.tif *.tiff')], parent=root)
                if not chosen:
                    root.destroy()
                    return
                image_path = Path(chosen)
    root.title('Opening histology project')
    root.geometry('560x150')
    status = tk.StringVar(value='Checking your scan and preparing the overview…')
    box = ttk.Frame(root, padding=24)
    box.pack(fill='both', expand=True)
    ttk.Label(box, textvariable=status, wraplength=500).pack(anchor='w', pady=(0, 16))
    bar = ttk.Progressbar(box, mode='indeterminate')
    bar.pack(fill='x')
    bar.start()
    root.deiconify()
    updates = queue.Queue()
    executor = ThreadPoolExecutor(max_workers=1)
    def prepare():
        path = Path(config).resolve() if config else prepare_project(image_path, progress=updates.put)
        create_previews(path, progress=updates.put, skip_existing=True)
        return path
    future = executor.submit(prepare)
    def poll():
        while not updates.empty():
            status.set(updates.get_nowait())
        if not future.done():
            root.after(100, poll)
            return
        try:
            path = future.result()
            bar.stop()
            box.destroy()
            Reviewer(root, path)
        except Exception as exc:
            messagebox.showerror('Could not open the project', str(exc), parent=root)
            root.destroy()
        finally:
            executor.shutdown(wait=False)
    root.protocol('WM_DELETE_WINDOW', lambda: status.set('Preparing files. This window will be ready shortly.'))
    root.after(100, poll)
    root.mainloop()


if __name__ == '__main__':
    main()
