"""Application integration tests with hidden Tk windows and isolated synthetic data."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
import tifffile
from slice_review import Reviewer


class GuiTests(unittest.TestCase):
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
