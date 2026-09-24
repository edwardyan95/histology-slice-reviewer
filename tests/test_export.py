import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import tifffile
from review_data import AnnotationStore
from export_ordered import export, make_plan


class ExportTests(unittest.TestCase):
    def test_sort_flip_both_channels_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            raw = np.arange(2 * 2 * 80 * 100, dtype=np.uint16).reshape(2, 2, 80, 100)
            tifffile.imwrite(directory / 'input.tif', raw, imagej=True, metadata={'axes': 'ZCYX'})
            sources = [{'section_index_1based': i + 1, 'matching_export': f'scan{i}.tif',
                        'ets_relative_path': f'stack{i}/frame.ets', 'source_sha256': str(i) * 64,
                        'padding_left': 0, 'padding_top': 0,
                        'tile_grid_width': 100, 'tile_grid_height': 80} for i in range(2)]
            (directory / 'sources.json').write_text(json.dumps({'sources': sources}))
            store = AnnotationStore(directory, sources, directory / 'input.tif', raw.shape)
            store.set_annotation(0, 2, (80, 40), reviewed=True)
            store.set_annotation(1, 1, (20, 40), reviewed=True)
            store.save(1)
            store.close()
            config = {'input_tiff': 'input.tif', 'source_index': 'sources.json', 'annotations_dir': '.'}
            (directory / 'config.json').write_text(json.dumps(config))
            export(directory / 'config.json', directory / 'ordered.tif')
            with tifffile.TiffFile(directory / 'ordered.tif') as result:
                expected = np.stack((raw[1, :, :, ::-1], raw[0]))
                np.testing.assert_array_equal(result.asarray(), expected)
                self.assertEqual(result.imagej_metadata['mode'], 'grayscale')
                self.assertEqual(result.series[0].axes, 'ZCYX')
            report = json.loads((directory / 'ordered_verification.json').read_text())
            self.assertEqual(report['verified_pages'], 4)
            self.assertEqual(report['plan'][0]['output_right_x_px'], 79)
            with self.assertRaises(FileExistsError):
                export(directory / 'config.json', directory / 'ordered.tif')

    def test_ties_preserved_and_ambiguous_point_rejected(self):
        items = [{'image_index_1based': i, 'slice_index': 4, 'right_point': {'stack_x_px': 10, 'stack_y_px': 10},
                  'reviewed': True, 'source_export': str(i), 'ets_relative_path': str(i), 'source_sha256': str(i)} for i in (3, 2)]
        self.assertEqual([p['input_image_index_1based'] for p in make_plan(items, 100)], [2, 3])
        items[0]['right_point']['stack_x_px'] = 49
        with self.assertRaises(ValueError):
            make_plan(items, 100)
