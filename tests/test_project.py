import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import tifffile
from project import prepare_project, inspect_stack, locate_stack
from review_data import AnnotationStore
from create_previews import create_previews
from export_ordered import export


class ProjectTests(unittest.TestCase):
    def test_new_project_resume_and_export_snapshot_while_reviewing(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = folder / 'full_resolution_stack.tif'
            pixels = np.arange(32000, dtype=np.uint16).reshape(2, 2, 80, 100)
            tifffile.imwrite(path, pixels, imagej=True, metadata={'axes': 'ZCYX'})
            self.assertEqual(locate_stack(folder), path)
            config_path = prepare_project(path)
            self.assertEqual(prepare_project(path), config_path)
            config = json.loads(config_path.read_text())
            source_path = config_path.parent / config['source_index']
            sources = json.loads(source_path.read_text())['sources']
            expected = hashlib.sha256(pixels[0].tobytes()).hexdigest()
            self.assertEqual(sources[0]['source_sha256'], expected)
            create_previews(config_path, skip_existing=True)
            store = AnnotationStore(folder, sources, path, pixels.shape)
            store.set_annotation(0, 2, (80, 40), reviewed=True)
            store.set_annotation(1, 1, (20, 40), reviewed=True)
            store.save(0)
            try:
                snapshot = json.loads(store.path.read_text())
                result = export(config_path, folder / 'ordered.tif', snapshot)
                np.testing.assert_array_equal(tifffile.imread(result), np.stack((pixels[1, :, :, ::-1], pixels[0])))
                self.assertEqual(snapshot['annotations'][0]['slice_index'], 2)
            finally:
                store.close()
            with path.open('ab') as stream:
                stream.write(b'changed')
            with self.assertRaisesRegex(ValueError, 'modified TIFF'):
                prepare_project(path)

    def test_original_ets_source_identity_is_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = folder / 'full_resolution_stack.tif'
            tifffile.imwrite(path, np.zeros((2, 2, 80, 100), np.uint16), imagej=True, metadata={'axes': 'ZCYX'})
            source_folder = folder / 'example_ETS_export_files'
            source_folder.mkdir()
            sources = [{'section_index_1based': i+1, 'matching_export': f'scan{i}', 'ets_relative_path': f'stack{i}/frame.ets',
                        'source_sha256': str(i)*64, 'padding_left': 0, 'padding_top': 0,
                        'tile_grid_width': 100, 'tile_grid_height': 80} for i in range(2)]
            (source_folder / 'source_index.json').write_text(json.dumps({'sources': sources}))
            store = AnnotationStore(folder, sources, path, (2, 2, 80, 100))
            store.set_annotation(0, 7, (80, 40), reviewed=True)
            store.save(0)
            store.close()
            before = (folder / 'slice_annotations.json').read_bytes()
            config_path = prepare_project(path)
            self.assertIn('ETS_export_files', json.loads(config_path.read_text())['source_index'])
            self.assertEqual((folder / 'slice_annotations.json').read_bytes(), before)

    def test_rgb_tiff_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'rgb.tif'
            tifffile.imwrite(path, np.zeros((80, 100, 3), np.uint8), photometric='rgb')
            with self.assertRaisesRegex(ValueError, 'two-channel TIFF'):
                inspect_stack(path)
