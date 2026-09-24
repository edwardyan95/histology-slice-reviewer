"""Read a supported cellSens VSI directory and its uint16 JPEG2000 ETS planes.

Format reference: OME Bio-Formats CellSensReader (see docs/SCANNER_FORMAT.md).
Files are matched by VSI stack ID, never by position in a directory listing.
"""
import hashlib
import io
import json
import math
from pathlib import Path
import struct

import numpy as np
from PIL import Image

from review_data import atomic_write


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def signature(path, root):
    stat = path.stat()
    return {'name': path.relative_to(root).as_posix(), 'bytes': stat.st_size,
            'mtime_ns': stat.st_mtime_ns}


def read_vsi(path):
    """Return stack records from bounded little-endian VSI metadata containers."""
    raw = Path(path).read_bytes()
    if raw[:4] != b'II*\0':
        raise ValueError('Supported VSI files must use little-endian classic TIFF headers.')
    stacks = {}
    visited = set()

    def unpack(fmt, at):
        if at < 0 or at + struct.calcsize(fmt) > len(raw):
            raise ValueError('Truncated VSI metadata.')
        return struct.unpack_from(fmt, raw, at)

    def walk(base, trail=()):
        if base in visited or len(trail) > 40:
            raise ValueError('Cyclic or excessively nested VSI metadata.')
        visited.add(base)
        size, magic, version, offset, flags, reserved = unpack('<HHIQII', base)
        if size != 24 or magic != 21321:
            raise ValueError('Unsupported VSI metadata container.')
        count = flags & 0xfffffff
        if count > len(raw) // 16:
            raise ValueError('Invalid VSI tag count.')
        at = base + offset
        fields = set()
        for _ in range(count):
            if at in fields:
                raise ValueError('Cyclic VSI metadata fields.')
            fields.add(at)
            field, tag, next_field, size = unpack('<IIII', at)
            if tag & 0x80000000:
                break  # Scanner container terminator.
            data = at + 16
            index = None
            if field & 0x8000000:
                index, = unpack('<I', data)
                data += 4
            kind = field & 0xffffff
            path = trail + ((tag, index),)
            inline = bool(field & 0x40000000)
            extended = bool(field & 0x10000000)
            if extended and kind in (0, 1, 2):
                walk(data, path)
            else:
                supported_value = kind in (5, 6, 12, 13, 14, 10, 256, 257, 258, 259, 260, 261, 262, 263, 267, 268, 274, 275, 276, 277, 279, 280, 8192, 8195, 8199)
                # Opaque arrays/masks and IFD references have their own storage rules.
                # Only dereference the scalar/vector fields used by this importer.
                if not inline and not extended and supported_value and data + size > len(raw):
                    raise ValueError('Truncated VSI scalar/vector field.')
                payload = raw[data:data+size] if not inline else b''
                value = None
                if inline:
                    value = size
                elif kind in (13, 8192):
                    value = payload.decode('utf-16-le').rstrip('\0')
                elif kind in (5, 6, 14, 256, 257, 258, 259, 267, 274, 275, 276, 277, 8195, 8199):
                    if size % 4 == 0:
                        value = list(struct.unpack('<' + 'i' * (size // 4), payload))
                elif kind in (10, 260, 261, 262, 263, 268, 279, 280):
                    if size % 8 == 0:
                        value = list(struct.unpack('<' + 'd' * (size // 8), payload))
                elif kind == 12 and size == 1:
                    value = payload[0]
                ids = [v for t, v in path if t == 2001]
                if ids and ids[-1] is not None:
                    stack = stacks.setdefault(ids[-1], {'stack_id': ids[-1], 'channels_by_id': {}})
                    parents = [t for t, v in path[:-1]]
                    if trail and trail[-1][0] == 2018 and 2002 in parents:
                        key = {20005: 'external', 2053: 'boundary', 2410: 'tile_origin'}.get(tag)
                        if key:
                            if key in stack and stack[key] != value:
                                raise ValueError('Multiple image frames per VSI stack are not supported.')
                            stack[key] = value
                    if trail and trail[-1][0] == 2005 and len(trail) >= 2 and trail[-2][0] == 2001:
                        key = {2030: 'name', 2074: 'stack_type', 2019: 'pixel_size', 2020: 'pixel_unit'}.get(tag)
                        if key:
                            stack[key] = value
                    if trail and trail[-1][0] == 2001 and tag == 2003:
                        stack['dimensions'] = value
                    if len(trail) >= 2 and trail[-1][0] == 2008 and trail[-2][0] == 2007:
                        channel = stack['channels_by_id'].setdefault(trail[-1][1], {})
                        key = {2419: 'name', 2023: 'meaning', 2003: 'display_range'}.get(tag)
                        if key:
                            channel[key] = value
            if not next_field:
                break
            at = base + next_field

    walk(8)
    return list(stacks.values())


class EtsFile:
    """Bounded sparse tile reader. Each method opens its own file handle."""
    def __init__(self, path, width, height, origin=(0, 0), channels=2):
        self.path = Path(path)
        self.width, self.height = int(width), int(height)
        self.origin = tuple(origin[:2])
        self.channels = channels
        file_size = self.path.stat().st_size
        with self.path.open('rb') as stream:
            header = stream.read(48)
            if len(header) != 48:
                raise ValueError('Truncated ETS header.')
            magic, size, version, nd, extra_at, extra_size, _, index_at, count, _ = struct.unpack('<4sIIIQIIQII', header)
            if magic != b'SIS\0' or size != 64 or nd != 4 or extra_size < 200:
                raise ValueError('Unsupported ETS layout: expected X, Y, channel and pyramid dimensions.')
            if extra_at + extra_size > file_size or index_at + count * 36 > file_size or count < 1:
                raise ValueError('Truncated ETS header or tile index.')
            stream.seek(extra_at)
            extra = stream.read(extra_size)
            values = struct.unpack('<50I', extra[:200])
            if extra[:4] != b'ETS\0' or values[2:6] != (4, 1, 1, 3) or values[9] != 1 or values[38] != 1:
                raise ValueError('Supported tissue ETS files use separate uint16 grayscale channels and JPEG2000 tiles.')
            self.tw, self.th = values[7:9]
            if not 0 < self.tw <= 8192 or not 0 < self.th <= 8192:
                raise ValueError('Invalid ETS tile size.')
            if values[46] != 3 or tuple(values[47:50]) != (width, height, channels):
                raise ValueError('VSI dimensions/channels do not match the ETS native image header.')
            if min(width, height) <= 0 or self.origin != (0, 0):
                raise ValueError('Nonzero ETS tile origins are not supported by this importer yet.')
            self.background, = struct.unpack_from('<H', extra, 108)
            self.entries = {}
            stream.seek(index_at)
            for _ in range(count):
                _, x, y, c, level, offset, length, _ = struct.unpack('<5IQ2I', stream.read(36))
                key = (level, c, x, y)
                if key in self.entries or c >= channels or level > 16 or length < 1 or offset + length > index_at or offset < extra_at + extra_size:
                    raise ValueError('Invalid, duplicate, or truncated ETS tile.')
                if x * self.tw >= math.ceil(width / 2**level) or y * self.th >= math.ceil(height / 2**level):
                    raise ValueError('ETS tile coordinates exceed the VSI image boundary.')
                self.entries[key] = (offset, length)
        self.levels = sorted({key[0] for key in self.entries})
        if 0 not in self.levels:
            raise ValueError('ETS file has no full-resolution tiles.')
        for level in self.levels:
            masks = [{(x, y) for lev, chan, x, y in self.entries if lev == level and chan == c} for c in range(channels)]
            if not masks[0] or any(mask != masks[0] for mask in masks[1:]):
                raise ValueError('ETS channels have missing or inconsistent tile coverage.')

    def read_channel(self, channel, level=0):
        if channel not in range(self.channels) or level not in self.levels:
            raise ValueError('Requested ETS channel or pyramid level is unavailable.')
        height, width = math.ceil(self.height / 2**level), math.ceil(self.width / 2**level)
        output = np.full((height, width), self.background, dtype=np.uint16)
        with self.path.open('rb') as stream:
            for (lev, c, tx, ty), (offset, length) in self.entries.items():
                if lev != level or c != channel:
                    continue
                stream.seek(offset)
                with Image.open(io.BytesIO(stream.read(length))) as image:
                    tile = np.asarray(image)
                if tile.dtype != np.uint16 or tile.shape != (self.th, self.tw):
                    raise ValueError('Decoded ETS tile has unexpected dimensions or bit depth.')
                x, y = tx * self.tw, ty * self.th
                w, h = min(self.tw, width-x), min(self.th, height-y)
                output[y:y+h, x:x+w] = tile[:h, :w]
        return output


def prepare_scanner(folder, progress=None):
    folder = Path(folder).resolve()
    vsis = sorted(p for p in folder.iterdir() if p.suffix.lower() == '.vsi')
    if not vsis:
        raise ValueError('No VSI files found. Select the folder containing the VSI and its companion folder.')
    sources, excluded, all_files = [], [], list(vsis)
    for vsi in vsis:
        if progress:
            progress(f'Reading scanner metadata: {vsi.name}')
        companion = folder / ('_' + vsi.stem + '_')
        known_files = set()
        for record in read_vsi(vsi):
            kind = record.get('stack_type')
            if kind != 0:
                excluded.append({'vsi': vsi.name, 'stack_id': record['stack_id'], 'name': record.get('name'), 'stack_type': kind})
                if record.get('external'):
                    known_files.update((companion / f"stack{record['stack_id']}").glob('frame_*.ets'))
                continue
            if not record.get('external'):
                raise ValueError(f"{vsi.name}: tissue stack {record['stack_id']} has no external ETS pixels.")
            candidates = list((companion / f"stack{record['stack_id']}").glob('frame_*.ets'))
            if len(candidates) != 1:
                raise ValueError(f"{vsi.name}: expected one frame ETS file for stack{record['stack_id']}; found {len(candidates)}. Keep the entire scanner folder together.")
            ets = candidates[0]
            known_files.add(ets)
            channels = record.pop('channels_by_id')
            if sorted(channels) != [0, 1] or channels[0].get('meaning') != 4 or any(c.get('meaning', 4) != 4 for c in channels.values()) or record.get('dimensions') != [2]:
                raise ValueError('Only two-channel tissue scans with no extra Z/time dimensions are currently supported.')
            names = [channels[i].get('name') for i in range(2)]
            if not all(isinstance(n, str) and n.strip() for n in names) or len(set(names)) != 2 or 'Overlay' in names:
                raise ValueError('VSI channel names must be present and distinct.')
            boundary = record.get('boundary', [])
            if len(boundary) != 4 or boundary[:2] != [0, 0]:
                raise ValueError('Unsupported VSI image boundary.')
            width, height = boundary[2:]
            reader = EtsFile(ets, width, height, record.get('tile_origin', [0, 0]))
            record.update({'section_index_1based': len(sources)+1, 'vsi_file': vsi.name,
                           'matching_export': f"{vsi.stem} / {record.get('name', ets.parent.name)}",
                           'ets_relative_path': ets.relative_to(folder).as_posix(),
                           'channels': names, 'width': width, 'height': height,
                           'tile_grid_width': width, 'tile_grid_height': height,
                           'display_ranges': [channels[i].get('display_range', [0, 65535]) for i in range(2)],
                           'ets_background': reader.background, 'levels': reader.levels,
                           'source_signature': signature(ets, folder)})
            sources.append(record)
            all_files.append(ets)
        orphans = set(companion.glob('*/frame_*.ets')) - known_files
        if orphans:
            raise ValueError('ETS files without matching VSI stack metadata were found. Use an intact scanner export.')
    if not sources:
        raise ValueError('No supported full-resolution tissue scans found in the VSI metadata.')
    if any(source['channels'] != sources[0]['channels'] for source in sources):
        raise ValueError('All slides in one reviewer project must have the same channels in the same order.')
    width = max(s['width'] for s in sources)
    height = max(s['height'] for s in sources)
    shape = [len(sources), 2, height, width]
    work = folder / '.slice_review'
    config_path = work / 'config.json'
    signatures = [signature(path, folder) for path in all_files]
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding='utf-8'))
        if config.get('input_kind') != 'vsi' or config.get('input_signatures') != signatures:
            raise ValueError('This folder has an existing reviewer project with different or modified source files. Restore the original dataset or use a separate folder.')
        if digest_file(work / config['source_index']) != config.get('source_index_sha256'):
            raise ValueError('The saved scanner source index was modified. Restore it before reopening annotations.')
        return config_path
    if (folder / 'slice_annotations.json').exists():
        raise ValueError('Annotations exist but the original project index is missing. Restore .slice_review before importing.')
    for i, source in enumerate(sources):
        if progress:
            progress(f'Identifying full-resolution scan {i+1} of {len(sources)}…')
        source['source_sha256'] = digest_file(folder / source['ets_relative_path'])
        source['padding_left'] = (width - source['width']) // 2
        source['padding_top'] = (height - source['height']) // 2
        ranges = source['display_ranges']
        if any(len(r) != 2 or not all(math.isfinite(v) for v in r) or r[1] <= r[0] for r in ranges):
            raise ValueError('Invalid scanner channel display limits.')
    if signatures != [signature(path, folder) for path in all_files]:
        raise ValueError('Scanner files changed during import. Wait until scanning is finished, then reopen.')
    work.mkdir(exist_ok=True)
    index_path = work / 'source_index.json'
    atomic_write(index_path, json.dumps({'sources': sources, 'excluded': excluded,
                 'vsi_sha256': {v.name: digest_file(v) for v in vsis}, 'shape_zcyx': shape}, indent=2) + '\n')
    config = {'input_kind': 'vsi', 'input_dir': '..', 'source_index': 'source_index.json',
              'annotations_dir': '..', 'preview_dir': 'previews', 'dataset_name': folder.name,
              'shape_zcyx': shape, 'channels': sources[0]['channels'],
              'input_signatures': signatures, 'source_index_sha256': digest_file(index_path)}
    atomic_write(config_path, json.dumps(config, indent=2) + '\n')
    return config_path
