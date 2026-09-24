"""Application integration tests with hidden Tk windows and isolated synthetic data."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
import tifffile
from slice_review import Reviewer


class GuiTests(unittest.TestCase):
    def test_previews_avoid_native_decode_and_keep_coordinates_during_navigation(self):
        from project import prepare_project
        from create_previews import create_previews
        from export_ordered import export
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            raw = np.arange(32000, dtype=np.uint16).reshape(2, 2, 80, 100)
            path = folder / 'input.tif'
            tifffile.imwrite(path, raw, imagej=True, metadata={'axes': 'ZCYX'})
            config = prepare_project(path)
            create_previews(config)
            for i in range(2):
                for c, name in enumerate(('FITC', 'Cy3')):
                    Image.new('L', (25, 20), 30+i*60+c*20).save(config.parent / 'previews' / f'{i+1:02d}_{name}.png')
            root = tk.Tk()
            root.withdraw()
            gate, started = threading.Event(), threading.Event()
            reviewer = None
            try:
                with patch('image_source.ImageSource.display_image', side_effect=AssertionError('Navigation decoded native pixels')):
                    reviewer = Reviewer(root, config, auto_gallery=False)
                    reviewer.future.result(timeout=3)
                    reviewer.check_loaded(reviewer.load_generation, reviewer.future)
                    self.assertEqual(reviewer.images[0].size, (25, 20))
                    reviewer.view.fit(500, 400)
                    x, y = reviewer.view.canvas_at(80.5, 40.5)
                    reviewer.mark_point(SimpleNamespace(x=x, y=y))
                    self.assertEqual(reviewer.point, (80, 40))
                    reviewer.slice_number.set('2')
                    self.assertTrue(reviewer.save_current(reviewed=True))

                    def slow_native(index, cancel):
                        started.set()
                        if not gate.wait(5):
                            raise RuntimeError('Test native task timed out')
                        return [Image.new('L', (100, 80), 250)] * 2

                    with patch.object(reviewer, 'read_full_section', side_effect=slow_native):
                        old_generation = reviewer.load_generation
                        reviewer.load_full_resolution()
                        old_future = reviewer.detail_future
                        self.assertTrue(started.wait(2))
                        reviewer.load(1)
                        reviewer.future.result(timeout=2)
                        reviewer.check_loaded(reviewer.load_generation, reviewer.future)
                        self.assertEqual(reviewer.images[0].getpixel((0, 0)), 90)
                        gate.set()
                        old_future.result(timeout=2)
                        reviewer.check_full_resolution(old_generation, old_future)
                        self.assertFalse(reviewer.full_resolution)
                        self.assertEqual(reviewer.images[0].getpixel((0, 0)), 90)

                reviewer.view.fit(500, 400)
                x, y = reviewer.view.canvas_at(20.5, 40.5)
                reviewer.mark_point(SimpleNamespace(x=x, y=y))
                reviewer.slice_number.set('1')
                self.assertTrue(reviewer.save_current(reviewed=True))
                before = (reviewer.view.scale, reviewer.view.ox, reviewer.view.oy, reviewer.point)
                reviewer.load_full_resolution()
                reviewer.detail_future.result(timeout=3)
                reviewer.check_full_resolution(reviewer.load_generation, reviewer.detail_future)
                self.assertTrue(reviewer.full_resolution)
                self.assertEqual(reviewer.images[0].size, (100, 80))
                self.assertEqual(before, (reviewer.view.scale, reviewer.view.ox, reviewer.view.oy, reviewer.point))
                reviewer.render()
                snapshot = json.loads(reviewer.store.path.read_text())
                output = export(config, folder / 'ordered.tif', snapshot)
                np.testing.assert_array_equal(tifffile.imread(output), np.stack((raw[1, :, :, ::-1], raw[0])))
            finally:
                gate.set()
                if reviewer is not None:
                    reviewer.executor.shutdown(wait=True)
                    reviewer.detail_executor.shutdown(wait=True)
                    reviewer.close()
                else:
                    root.destroy()

    def test_overview_edits_save_together_and_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            sources = [{'section_index_1based': i + 1, 'matching_export': f'scan{i}.tif',
                        'ets_relative_path': f'stack{i}/frame.ets', 'source_sha256': str(i) * 64,
                        'padding_left': 0, 'padding_top': 0,
                        'tile_grid_width': 100, 'tile_grid_height': 80} for i in range(2)]
            (folder / 'sources.json').write_text(json.dumps({'sources': sources}))
            data = np.arange(32000, dtype=np.uint16).reshape(2, 2, 80, 100)
            tifffile.imwrite(folder / 'input.tif', data, imagej=True, metadata={'axes': 'ZCYX'})
            (folder / 'previews').mkdir()
            for i in range(2):
                for channel in ('FITC', 'Cy3'):
                    Image.new('L', (100, 80), 80 if channel == 'FITC' else 140).save(folder / 'previews' / f'{i+1:02d}_{channel}.png')
            (folder / 'config.json').write_text(json.dumps({'input_tiff': 'input.tif', 'source_index': 'sources.json', 'annotations_dir': 'annotations'}))
            root = tk.Tk()
            root.withdraw()
            errors = []
            root.report_callback_exception = lambda kind, value, tb: errors.append(str(value))
            reviewer = Reviewer(root, folder / 'config.json', auto_gallery=False)
            reviewer.future.result(timeout=5)
            reviewer.check_loaded(reviewer.load_generation, reviewer.future)
            reviewer.show_gallery()
            reviewer.gallery.withdraw()
            reviewer.gallery_vars[0].set('2')
            reviewer.gallery_vars[1].set('1')
            self.assertTrue(reviewer.save_gallery())
            reviewer.gallery_channel.set('Cy3')
            reviewer.render_gallery()
            reviewer.gallery_channel.set('Overlay')
            reviewer.render_gallery()
            reviewer.gallery_open_image(1)
            self.assertEqual(reviewer.index, 1)
            self.assertEqual(reviewer.slice_number.get(), '1')
            reviewer.point = (75, 40)
            self.assertTrue(reviewer.save_current(reviewed=True))
            reviewer.show_gallery()
            reviewer.gallery.withdraw()
            reviewer.gallery_vars[0].set('1')
            self.assertTrue(reviewer.save_gallery())
            reviewer.close_gallery()
            reviewer.executor.shutdown(wait=True)
            reviewer.close()
            saved = json.loads((folder / 'annotations/slice_annotations.json').read_text())
            self.assertEqual([a['slice_index'] for a in saved['annotations']], [1, 1])
            self.assertTrue(saved['annotations'][1]['reviewed'])
            self.assertEqual(saved['annotations'][1]['right_point']['stack_x_px'], 75)
            self.assertFalse(errors, errors)
