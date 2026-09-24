"""Open a prepared two-channel TIFF without workstation-specific configuration."""
from pathlib import Path
import hashlib
import json
import os
import numpy as np
import tifffile
from review_data import atomic_write


def inspect_stack(path):
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        if series.axes != 'ZCYX' or len(series.shape) != 4 or series.shape[1] != 2:
            raise ValueError('Choose a two-channel TIFF with ZCYX axes (sections, channels, height, width). '
                             'RGB pictures, scanner overview images and arbitrary TIFF page collections are not supported.')
        if series.dtype != np.dtype('uint16') or len(tif.pages) != series.shape[0] * 2:
            raise ValueError('The TIFF must have two separate 16-bit grayscale pages per section.')
        return tuple(series.shape)


def locate_stack(folder):
    folder = Path(folder)
    if any(p.suffix.lower() == '.vsi' for p in folder.iterdir()):
        return folder
    candidates = sorted(folder.glob('*full_resolution*stack.tif'))
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError('Select the original full-resolution two-channel TIFF in this folder.')


def prepare_project(image_path, progress=None):
    image_path = Path(image_path).resolve()
    if image_path.is_dir() or image_path.suffix.lower() == '.vsi':
        from scanner import prepare_scanner
        return prepare_scanner(image_path if image_path.is_dir() else image_path.parent, progress)
    shape = inspect_stack(image_path)
    folder = image_path.parent
    work = folder / '.slice_review'
    work.mkdir(exist_ok=True)
    config_path = work / 'config.json'
    signature = {'name': image_path.name, 'bytes': image_path.stat().st_size,
                 'mtime_ns': image_path.stat().st_mtime_ns}
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding='utf-8'))
        if config.get('input_signature') != signature:
            raise ValueError('This folder already has a reviewer project for a different or modified TIFF. '
                             'Use the original TIFF, or put the new dataset in a separate folder.')
        inspect_stack(work / config['input_tiff'])
        return config_path
    # Preserve original ETS identities so completed annotations from earlier versions reopen unchanged.
    indexes = sorted(folder.glob('*ETS_export_files/source_index.json'))
    if len(indexes) > 1:
        raise ValueError('More than one source index was found. Keep one prepared dataset in each folder.')
    if indexes:
        source_path = indexes[0]
        sources = json.loads(source_path.read_text(encoding='utf-8'))['sources']
        if len(sources) != shape[0]:
            raise ValueError('Source index does not match the number of TIFF sections.')
        for i, source in enumerate(sources):
            if source['section_index_1based'] != i + 1:
                raise ValueError('Source index must match TIFF section order.')
    else:
        if (folder / 'slice_annotations.json').exists():
            raise ValueError('Annotations already exist but their source index is missing. Restore the original source index before opening.')
        sources = []
        with tifffile.TiffFile(image_path) as tif:
            for i in range(shape[0]):
                digest = hashlib.sha256()
                for c in range(2):
                    pixels = np.ascontiguousarray(tif.pages[i * 2 + c].asarray(), dtype='<u2')
                    digest.update(memoryview(pixels).cast('B'))
                sources.append({'section_index_1based': i + 1,
                    'matching_export': f'{image_path.name} / section {i + 1}',
                    'ets_relative_path': f'{image_path.name}#section={i + 1}',
                    'source_sha256': digest.hexdigest(), 'identity_kind': 'two_channel_tiff_pixel_sha256',
                    'channels': ['FITC', 'Cy3'], 'padding_left': 0, 'padding_top': 0,
                    'tile_grid_width': shape[3], 'tile_grid_height': shape[2]})
                if progress:
                    progress(f'Indexing section {i + 1} of {shape[0]}…')
        source_path = work / 'source_index.json'
        atomic_write(source_path, json.dumps({'sources': sources}, indent=2) + '\n')
    config = {'input_tiff': os.path.relpath(image_path, work),
              'source_index': os.path.relpath(source_path, work), 'annotations_dir': '..',
              'preview_dir': 'previews', 'dataset_name': image_path.stem,
              'input_signature': signature}
    atomic_write(config_path, json.dumps(config, indent=2) + '\n')
    return config_path
