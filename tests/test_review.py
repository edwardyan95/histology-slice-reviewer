import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review_data import AnnotationStore, ViewTransform


def sources():
    return [{'section_index_1based': i + 1, 'matching_export': f'scan{i}.tif',
             'ets_relative_path': f'stack{i}/frame.ets', 'source_sha256': str(i) * 64,
             'padding_left': 10, 'padding_top': 20,
             'tile_grid_width': 80, 'tile_grid_height': 60} for i in range(2)]


class ReviewTests(unittest.TestCase):
    def test_coordinates_survive_fit_zoom_pan(self):
        view = ViewTransform(7168, 5120)
        view.fit(923, 677)
        point = (4000.5, 3000.5)
        self.assertAlmostEqual(view.image_at(*view.canvas_at(*point))[0], point[0])
        anchor = view.image_at(320, 250)
        view.zoom(320, 250, 4)
        self.assertEqual(view.image_at(320, 250), anchor)
        view.ox += 117
        view.oy -= 240
        for a, b in zip(view.image_at(*view.canvas_at(*point)), point):
            self.assertAlmostEqual(a, b)

    def test_resume_duplicates_and_source_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory, sources(), 'test.tif', (2, 2, 100, 100))
            store.set_annotation(0, 3, (32.9, 45.2), reviewed=True)
            store.set_annotation(1, 3, (40, 50), reviewed=True)
            store.save(1)
            store.close()
            reopened = AnnotationStore(directory, sources(), 'test.tif', (2, 2, 100, 100))
            self.assertEqual(reopened.data['last_image_index'], 1)
            entry = reopened.data['annotations'][0]
            self.assertEqual(entry['right_point'], {'stack_x_px': 32, 'stack_y_px': 45,
                                                   'source_x_px': 22, 'source_y_px': 25})
            self.assertTrue(entry['reviewed'])
            self.assertEqual(reopened.data['annotations'][1]['slice_index'], 3)
            self.assertIn('32,45,22,25', reopened.csv_path.read_text())
            reopened.set_annotation(0, 4, (32, 45))
            self.assertFalse(reopened.data['annotations'][0]['reviewed'])
            reopened.close()

    def test_invalid_points_and_incomplete_review_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory, sources(), 'test.tif', (2, 2, 100, 100))
            for point in [(9, 30), (90, 30), (50, 19), (50, 80), (float('nan'), 30)]:
                with self.assertRaises(ValueError):
                    store.set_annotation(0, 1, point)
            for number, point in [(None, (30, 30)), (1, None), (0, (30, 30)), (True, (30, 30))]:
                with self.assertRaises(ValueError):
                    store.set_annotation(0, number, point, reviewed=True)
            store.close()

    def test_different_sources_rejected_and_lock_released(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory, sources(), 'test.tif', (2, 2, 100, 100))
            store.save(0)
            with self.assertRaises(RuntimeError):
                AnnotationStore(directory, sources(), 'test.tif', (2, 2, 100, 100))
            store.close()
            altered = sources()
            altered[0]['source_sha256'] = 'different'
            with self.assertRaises(ValueError):
                AnnotationStore(directory, altered, 'test.tif', (2, 2, 100, 100))
            reopened = AnnotationStore(directory, sources(), 'test.tif', (2, 2, 100, 100))
            reopened.close()


if __name__ == '__main__':
    unittest.main()
