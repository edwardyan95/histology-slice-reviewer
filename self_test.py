"""A synthetic end-to-end check for a packaged executable; never reads user scans."""
import json
import sys
from pathlib import Path
import tempfile
import traceback
import tkinter as tk
import numpy as np
import tifffile
from project import prepare_project
from create_previews import create_previews
from slice_review import Reviewer
from export_ordered import export


def run(report_path):
    try:
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = np.arange(32000, dtype=np.uint16).reshape(2, 2, 80, 100)
            path = folder / 'full_resolution_stack.tif'
            tifffile.imwrite(path, source, imagej=True, metadata={'axes': 'ZCYX'})
            config = prepare_project(path)
            create_previews(config)
            root = tk.Tk()
            root.withdraw()
            app = Reviewer(root, config, auto_gallery=False)
            app.future.result(timeout=20)
            app.check_loaded(app.load_generation, app.future)
            app.show_gallery()
            app.gallery.withdraw()
            app.gallery_vars[0].set('2')
            app.gallery_vars[1].set('1')
            assert app.save_gallery()
            app.store.set_annotation(0, 2, (80, 40), reviewed=True)
            app.store.set_annotation(1, 1, (20, 40), reviewed=True)
            app.store.save(0)
            snapshot = json.loads(app.store.path.read_text())
            try:
                result = export(config, folder / 'ordered.tif', snapshot)
            except Exception:
                app.executor.shutdown(wait=True)
                app.store.close()
                root.destroy()
                raise
            np.testing.assert_array_equal(tifffile.imread(result), np.stack((source[1, :, :, ::-1], source[0])))
            app.close_gallery()
            app.executor.shutdown(wait=True)
            app.close()
            report = {'status': 'PASS', 'version': '1.0.0', 'checks': ['Tk startup', 'folder setup', 'overview', 'save order', 'snapshot export', 'both-channel pixel equality']}
    except Exception as exc:
        report = {'status': 'FAIL', 'error': repr(exc), 'traceback': traceback.format_exc()}
        Path(report_path).write_text(json.dumps(report, indent=2), encoding='utf-8')
        if getattr(sys, 'frozen', False):
            return
        raise
    Path(report_path).write_text(json.dumps(report, indent=2), encoding='utf-8')
