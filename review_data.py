"""Persistent, source-linked histology annotations. Coordinates use original pixels."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path, text):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class AnnotationStore:
    def __init__(self, directory, sources, image_path, shape):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.sources = sources
        self.shape = tuple(shape)
        self.path = self.directory / 'slice_annotations.json'
        self.csv_path = self.directory / 'slice_annotations.csv'
        self.lock = None
        self._acquire_lock()
        try:
            identity = {'sources': [(s['ets_relative_path'], s['source_sha256']) for s in sources],
                        'shape_zcyx': list(shape)}
            self.identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding='utf-8'))
                if self.data.get('schema_version') != 1 or self.data.get('dataset_id') != self.identity:
                    raise ValueError('The saved annotations belong to different images or a different file format.')
                if len(self.data['annotations']) != len(sources):
                    raise ValueError('Saved annotation count does not match these images.')
            else:
                self.data = {
                    'schema_version': 1, 'dataset_id': self.identity,
                    'created_utc': utc_now(), 'updated_utc': utc_now(),
                    'image_path': str(Path(image_path).resolve()), 'shape_zcyx': list(shape),
                    'coordinate_convention': 'Zero-based integer pixels; x increases right, y down. '
                        'Stack coordinates refer to the unchanged combined TIFF. Source coordinates '
                        'subtract the centering padding and refer to the original ETS tile grid. '
                        'The marked point is inside the anatomical right hemisphere; it is not a midline or direction vector.',
                    'last_image_index': 0,
                    'annotations': [self._empty(s) for s in sources],
                }
        except Exception:
            self.close()
            raise

    def _acquire_lock(self):
        self.lock = (self.directory / '.review.lock').open('a+b')
        self.lock.seek(0, 2)
        if self.lock.tell() == 0:
            self.lock.write(b'0')
            self.lock.flush()
        self.lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lock.close()
            self.lock = None
            raise RuntimeError('This annotation folder is already open in another reviewer window.') from exc

    @staticmethod
    def _empty(source):
        return {'image_index_1based': source['section_index_1based'],
                'source_export': source['matching_export'],
                'ets_relative_path': source['ets_relative_path'],
                'source_sha256': source['source_sha256'],
                'slice_index': None, 'right_point': None, 'reviewed': False, 'updated_utc': None}

    def set_annotation(self, index, slice_index, point, reviewed=False):
        if slice_index is not None and (type(slice_index) is not int or slice_index < 1):
            raise ValueError('Enter a whole slice number of 1 or higher.')
        s = self.sources[index]
        right_point = None
        if point is not None:
            x, y = point
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)):
                raise ValueError('The marked point must have finite coordinates.')
            x, y = int(math.floor(x)), int(math.floor(y))
            sx, sy = x - s['padding_left'], y - s['padding_top']
            if not (0 <= sx < s['tile_grid_width'] and 0 <= sy < s['tile_grid_height']):
                raise ValueError('Click inside the scan image, away from the added black border.')
            right_point = {'stack_x_px': x, 'stack_y_px': y,
                           'source_x_px': sx, 'source_y_px': sy}
        if reviewed and (slice_index is None or right_point is None):
            raise ValueError('Set a slice number and click the anatomical right hemisphere first.')
        entry = self.data['annotations'][index]
        changed = entry['slice_index'] != slice_index or entry['right_point'] != right_point
        entry.update(slice_index=slice_index, right_point=right_point,
                     reviewed=bool(reviewed or (entry['reviewed'] and not changed)), updated_utc=utc_now())

    def save(self, current_index):
        self.data['last_image_index'] = current_index
        self.data['updated_utc'] = utc_now()
        atomic_write(self.path, json.dumps(self.data, indent=2) + '\n')
        self.export_csv()

    def export_csv(self):
        output = io.StringIO(newline='')
        fields = ['image_index_1based', 'source_export', 'ets_relative_path', 'source_sha256',
                  'slice_index', 'reviewed', 'stack_x_px', 'stack_y_px',
                  'source_x_px', 'source_y_px', 'updated_utc']
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for entry in self.data['annotations']:
            row = {k: entry.get(k) for k in fields}
            row.update(entry.get('right_point') or {})
            writer.writerow(row)
        atomic_write(self.csv_path, output.getvalue())

    def close(self):
        if self.lock:
            self.lock.close()
            self.lock = None


class ViewTransform:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.scale, self.ox, self.oy = 1., 0., 0.

    def fit(self, canvas_width, canvas_height):
        self.scale = min(max(1, canvas_width - 32) / self.width,
                         max(1, canvas_height - 32) / self.height)
        self.ox = (canvas_width - self.width * self.scale) / 2
        self.oy = (canvas_height - self.height * self.scale) / 2

    def image_at(self, x, y):
        return ((x - self.ox) / self.scale, (y - self.oy) / self.scale)

    def canvas_at(self, x, y):
        return (self.ox + x * self.scale, self.oy + y * self.scale)

    def zoom(self, x, y, factor):
        ix, iy = self.image_at(x, y)
        self.scale = min(8., max(.015, self.scale * factor))
        self.ox, self.oy = x - ix * self.scale, y - iy * self.scale
