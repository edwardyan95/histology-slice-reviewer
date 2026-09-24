"""Synthetic scanner fixtures with known pixels, sparse tiles and reordered VSI IDs."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
import tifffile
from scanner import prepare_scanner, EtsFile, read_vsi
from image_source import ImageSource
from create_previews import create_previews
from review_data import AnnotationStore
from export_ordered import export


def container(fields):
    blocks = []
    offset = 24
    for i, (kind, tag, value, index) in enumerate(fields):
        inline = isinstance(value, int)
        flag = kind | (0x40000000 if inline else 0) | (0x8000000 if index is not None else 0)
        data = b'' if inline else value
        length = 16 + (4 if index is not None else 0) + len(data)
        block = struct.pack('<IIII', flag, tag, offset + length if i+1 < len(fields) else 0, value if inline else len(data))
        if index is not None:
            block += struct.pack('<I', index)
        blocks.append(block + data)
        offset += length
    return struct.pack('<HHIQII', 24, 21321, 402, 24, len(fields), 0) + b''.join(blocks)


def field(tag, kind, value, index=None):
    return kind, tag, value, index


def volume(tag, fields, index=None):
    return field(tag, 0x10000001, container(fields), index)


def stack_metadata(stack_id, width, height, stack_type=0):
    return volume(2001, [
        field(2003, 8195, struct.pack('<I', 2)),
        volume(2002, [volume(2018, [field(20005, 12, 1),
            field(2053, 259, struct.pack('<4i', 0, 0, width, height)),
            field(2410, 8199, struct.pack('<3i', 0, 0, 0))])], 0),
        volume(2005, [field(2030, 8192, f'Scan {stack_id}'.encode('utf-16-le')),
            field(2074, 5, stack_type), field(2019, 260, struct.pack('<2d', .65, .65)),
            field(2020, 13, '10^-6m^1'.encode('utf-16-le'))]),
        volume(2007, [volume(2008, [field(2419, 8192, name.encode('utf-16-le')),
            field(2003, 268, struct.pack('<2d', 0, 65000)),
            *([field(2023, 5, 4)] if c == 0 else [])], c)
            for c, name in enumerate(('GFP', 'mCherry'))], 0)], stack_id)


def write_ets(path, pixels, sparse=True, mismatch=False):
    channels, height, width = pixels.shape
    tile_size = 32
    extra = [0] * 50
    extra[:10] = [int.from_bytes(b'ETS\0', 'little'), 196614, 4, 1, 1, 3, 100, tile_size, tile_size, 1]
    extra[27] = 7  # Stored background for absent tiles.
    extra[38] = 1
    extra[46:50] = [3, width, height, channels]
    body = bytearray(b'\0' * 64 + struct.pack('<50I', *extra))
    entries = []
    expected = pixels.copy()
    for c in range(channels):
        for ty in range((height+31)//32):
            for tx in range((width+31)//32):
                if sparse and (tx, ty) == (0, 0) and (not mismatch or c == 0):
                    expected[c, :32, :32] = 7
                    continue
                tile = np.zeros((32, 32), np.uint16)
                view = pixels[c, ty*32:(ty+1)*32, tx*32:(tx+1)*32]
                tile[:view.shape[0], :view.shape[1]] = view
                compressed = io.BytesIO()
                Image.fromarray(tile).save(compressed, format='JPEG2000', irreversible=False)
                payload = compressed.getvalue()
                entries.append(struct.pack('<5IQ2I', 0, tx, ty, c, 0, len(body), len(payload), 0))
                body.extend(payload)
    index_offset = len(body)
    body.extend(b''.join(entries))
    body[:48] = struct.pack('<4sIIIQIIQII', b'SIS\0', 64, 3, 4, 64, 200, 0, index_offset, len(entries), 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return expected


def scanner_fixture(folder):
    # Metadata order differs from alphabetical folder order, proving stack-ID mapping.
    definitions = [(10005, 95, 61), (10002, 80, 64)]
    vsi = folder / 'slide.vsi'
    vsi.write_bytes(b'II*\0\0\0\0\0' + container([volume(2000,
        [stack_metadata(1, 64, 64, 256), stack_metadata(10000, 64, 64, 1)] +
        [stack_metadata(*d) for d in definitions], 0)]))
    expected = []
    for i, (stack_id, width, height) in enumerate(definitions):
        pixels = (np.arange(2*height*width).reshape(2, height, width)*3 + i*1000).astype(np.uint16)
        expected.append(write_ets(folder / f'_slide_/stack{stack_id}/frame_t_0.ets', pixels))
    return expected


class ScannerTests(unittest.TestCase):
    def test_metadata_mapping_sparse_padding_channels_and_big_tiff_export(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            originals = scanner_fixture(folder)
            config = prepare_scanner(folder)
            self.assertEqual(prepare_scanner(folder), config)
            source = ImageSource(config)
            self.assertEqual(source.shape, (2, 2, 64, 95))
            self.assertEqual(source.channels, ['GFP', 'mCherry'])
            expected = np.zeros(source.shape, np.uint16)
            expected[0, :, 1:62, :] = originals[0]
            expected[1, :, :, 7:87] = originals[1]
            for i in range(2):
                for c in range(2):
                    np.testing.assert_array_equal(source.read_channel(i, c), expected[i, c])
            create_previews(config)
            self.assertTrue((config.parent / 'previews/01_C1.png').exists())
            store = AnnotationStore(folder, source.sources, folder, source.shape)
            store.set_annotation(0, 2, (80, 30), reviewed=True)
            store.set_annotation(1, 1, (20, 30), reviewed=True)
            store.save(0)
            snapshot = json.loads(store.path.read_text())
            store.close()
            output = export(config, folder / 'ordered.ome.tif', snapshot)
            with tifffile.TiffFile(output) as tif:
                self.assertTrue(tif.is_bigtiff and tif.is_ome)
                np.testing.assert_array_equal(tif.asarray(), np.stack((expected[1, :, :, ::-1], expected[0])))
                self.assertIn('mCherry', tif.ome_metadata)
                self.assertIn('0.65', tif.ome_metadata)
            self.assertEqual(json.loads((folder / 'ordered.ome_verification.json').read_text())['verified_pages'], 4)

    def test_missing_tissue_companion_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            scanner_fixture(folder)
            (folder / '_slide_/stack10005/frame_t_0.ets').unlink()
            with self.assertRaisesRegex(ValueError, 'expected one frame ETS'):
                prepare_scanner(folder)

    def test_changed_source_and_index_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            scanner_fixture(folder)
            config = prepare_scanner(folder)
            index = config.parent / 'source_index.json'
            index.write_text(index.read_text() + ' ')
            with self.assertRaisesRegex(ValueError, 'index was modified'):
                prepare_scanner(folder)

    def test_inconsistent_channel_masks_and_truncated_payload_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frame.ets'
            write_ets(path, np.ones((2, 64, 64), np.uint16), mismatch=True)
            with self.assertRaisesRegex(ValueError, 'inconsistent tile coverage'):
                EtsFile(path, 64, 64)
            path.write_bytes(path.read_bytes()[:-5])
            with self.assertRaisesRegex(ValueError, 'Truncated ETS'):
                EtsFile(path, 64, 64)
