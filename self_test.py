"""A synthetic end-to-end check for a packaged executable; never reads user scans."""
import json
import io
import sys
from pathlib import Path
import tempfile
import shutil
import uuid
from contextlib import contextmanager
import traceback
import tkinter as tk
import numpy as np
import tifffile
from PIL import Image
from project import prepare_project
from create_previews import create_previews
from slice_review import Reviewer
from export_ordered import export


@contextmanager
def test_directory(report_path):
    # Some SMB servers cannot reopen Windows directories created with mode 0700
    # (as used by tempfile). Inherit the report directory's normal permissions.
    parent = Path(report_path).resolve().parent
    folder = parent / ('self-test-' + uuid.uuid4().hex)
    folder.mkdir()
    try:
        yield folder
    finally:
        if folder.resolve().parent != parent or not folder.name.startswith('self-test-'):
            raise RuntimeError('Unexpected self-test directory.')
        shutil.rmtree(folder)


def run(report_path):
    try:
        with test_directory(report_path) as name:
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
            jp2 = io.BytesIO()
            Image.fromarray(source[0, 0]).save(jp2, format='JPEG2000', irreversible=False)
            jp2.seek(0)
            with Image.open(jp2) as check:
                np.testing.assert_array_equal(np.asarray(check), source[0, 0])
            big = folder / 'test.ome.tif'
            tifffile.imwrite(big, source, ome=True, bigtiff=True, photometric='minisblack', metadata={'axes': 'ZCYX'})
            with Image.open(big) as check:
                check.seek(3)
                np.testing.assert_array_equal(np.asarray(check), source[1, 1])
                check.close()
            report = {'status': 'PASS', 'version': '1.1.0', 'checks': ['Tk startup', 'folder setup', 'overview', 'save order', 'snapshot export', 'both-channel pixel equality', 'uint16 JPEG2000 codec', 'OME-BigTIFF independent decode']}
    except Exception as exc:
        report = {'status': 'FAIL', 'error': repr(exc), 'traceback': traceback.format_exc()}
        Path(report_path).write_text(json.dumps(report, indent=2), encoding='utf-8')
        if getattr(sys, 'frozen', False):
            return
        raise
    Path(report_path).write_text(json.dumps(report, indent=2), encoding='utf-8')
