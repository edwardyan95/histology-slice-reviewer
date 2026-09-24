"""Export a lossless stack in user-selected order, with anatomical right on image right."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import math
import shutil
from datetime import datetime, timezone
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import tifffile
from review_data import AnnotationStore, atomic_write
from image_source import ImageSource


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def make_plan(annotations, width):
    order = sorted(annotations, key=lambda a: (a['slice_index'] or 0, a['image_index_1based']))
    plan = []
    for output_index, item in enumerate(order, 1):
        if not item['reviewed'] or not item['slice_index'] or not item['right_point']:
            raise ValueError(f"Image {item['image_index_1based']} needs a reviewed slice number and right-side point.")
        x, y = item['right_point']['stack_x_px'], item['right_point']['stack_y_px']
        if abs(x - (width - 1) / 2) < width * .03:
            raise ValueError(f"Image {item['image_index_1based']}: point is too close to the image center to infer a horizontal side.")
        flip = x < (width - 1) / 2
        plan.append({'output_section_1based': output_index, 'slice_index': item['slice_index'],
                     'input_image_index_1based': item['image_index_1based'],
                     'source_export': item['source_export'], 'ets_relative_path': item['ets_relative_path'],
                     'source_sha256': item['source_sha256'], 'horizontal_flip': flip,
                     'input_right_x_px': x, 'input_right_y_px': y,
                     'output_right_x_px': width - 1 - x if flip else x, 'output_right_y_px': y})
    return plan


def export(config, output, annotation_snapshot=None):
    config = Path(config).resolve()
    settings = json.loads(config.read_text(encoding='utf-8'))
    source = ImageSource(config)
    input_path = source.input_path
    source_path = (config.parent / settings['source_index']).resolve()
    annotation_dir = (config.parent / settings['annotations_dir']).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f'Output already exists; choose a new filename: {output}')
    pending = output.with_name(output.stem + '.pending.tif')
    if pending.exists():
        raise FileExistsError(f'An unfinished export exists: {pending}')
    shape = source.shape
    use_ome = source.scanner or math.prod(shape) * 2 > 4_000_000_000
    if shutil.disk_usage(output.parent).free < math.prod(shape) * 2 + 32_000_000:
        raise OSError('There is not enough free space for the uncompressed output stack.')
    sources = json.loads(source_path.read_text(encoding='utf-8'))['sources']
    if annotation_snapshot is None:
        store = AnnotationStore(annotation_dir, sources, input_path, shape)
        try:
            annotations_bytes = store.path.read_bytes()
            annotations = json.loads(annotations_bytes)
        finally:
            store.close()
    else:
        annotations = annotation_snapshot
        annotations_bytes = (json.dumps(annotations, indent=2) + '\n').encode('utf-8')
    identity = {'sources': [(s['ets_relative_path'], s['source_sha256']) for s in sources], 'shape_zcyx': list(shape)}
    expected_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if annotations.get('dataset_id') != expected_id or len(annotations['annotations']) != shape[0]:
        raise ValueError('Annotations do not match the selected TIFF and source index.')
    plan = make_plan(annotations['annotations'], shape[-1])
    log = lambda message: print(message, flush=True) if sys.stdout is not None else None
    stem = output.with_suffix('')
    snapshot = Path(str(stem) + '_annotations_used.json')
    atomic_write(snapshot, annotations_bytes.decode('utf-8'))
    page_hashes = []
    thumbnails = []
    labels = [f"Slice {row['slice_index']} | source image {row['input_image_index_1based']} | {channel} | anatomical right on image right"
              for row in plan for channel in source.channels]

    def pages():
            for row in plan:
                previews = []
                for c in range(2):
                    index = row['input_image_index_1based'] - 1
                    raw = source.read_channel(index, c)
                    data = np.ascontiguousarray(raw[:, ::-1] if row['horizontal_flip'] else raw, dtype='<u2')
                    page_hashes.append(hashlib.sha256(memoryview(data).cast('B')).hexdigest())
                    preview = source.display_image(index, c, pixels=data)
                    preview.thumbnail((390, 279), Image.Resampling.LANCZOS)
                    previews.append(preview)
                    yield data
                thumbnails.append(Image.merge('RGB', (previews[1], previews[0], Image.new('L', previews[0].size))))
                log(f"Written {row['output_section_1based']}/{len(plan)}: slice {row['slice_index']}; flip={row['horizontal_flip']}")

    source.validate_files(full_hash=True, progress=log)
    metadata = {'axes': 'ZCYX', 'Name': settings.get('dataset_name', input_path.stem),
                'Channel': {'Name': source.channels},
                'Description': 'Annotated section order; anatomical right on image right. Separate raw uint16 channels. '
                               'Native image boundaries are center-padded, never resampled. See accompanying source index for each scan pixel calibration.'}
    if use_ome:
        metadata['MapAnnotation'] = {'Namespace': 'histology-slice-reviewer:source-calibration',
            'Value': {f"output_section_{i+1}": json.dumps({'slice_index': row['slice_index'],
                'source': sources[row['input_image_index_1based']-1]}) for i, row in enumerate(plan)}}
    tifffile.imwrite(pending, pages(), shape=shape, dtype=np.uint16, imagej=not use_ome,
                     ome=use_ome, bigtiff=use_ome,
                     photometric='minisblack', byteorder='<', compression=None,
                     metadata=metadata if use_ome else {'axes': 'ZCYX', 'mode': 'grayscale', 'unit': 'pixel',
                               'Labels': labels, 'Ranges': [2, 573, 0, 418],
                               'Info': 'Sorted by annotated slice index; ties preserve original scan order. '
                                       'Horizontal reflection only, with anatomical right on image right. '
                                       'C1 FITC, C2 Cy3. Pixel values, vertical orientation and resolution unchanged.'})
    with tifffile.TiffFile(pending) as check:
        expected_shape = shape if shape[0] > 1 else shape[1:]
        expected_axes = 'ZCYX' if shape[0] > 1 else 'CYX'
        if tuple(check.series[0].shape) != expected_shape or check.series[0].axes != expected_axes:
            raise AssertionError('Output dimensions or axes do not match.')
        if not use_ome and (check.imagej_metadata['Labels'] != labels or check.imagej_metadata['mode'] != 'grayscale'):
            raise AssertionError('Output channel metadata does not match.')
    # Independent TIFF decoder checks every original-resolution pixel against the planned permutation/reflection.
    with Image.open(pending) as independent:
        if independent.n_frames != len(page_hashes):
            raise AssertionError('Output page count does not match.')
        for index, expected in enumerate(page_hashes):
            independent.seek(index)
            array = np.asarray(independent, dtype='<u2')
            actual = hashlib.sha256(memoryview(np.ascontiguousarray(array)).cast('B')).hexdigest()
            if expected != actual:
                raise AssertionError(f'Output pixel verification failed on page {index + 1}.')
            log(f'Verified page {index + 1}/{len(page_hashes)}')
        independent.close()  # Close Pillow's secondary multipage TIFF handle before Windows rename.
    source.validate_files()
    pending.rename(output)
    atomic_write(Path(str(stem) + '_source_index.json'), source_path.read_text(encoding='utf-8'))
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=list(plan[0]))
    writer.writeheader()
    writer.writerows(plan)
    atomic_write(Path(str(stem) + '_manifest.csv'), buffer.getvalue())
    cellw, cellh, header = 410, 326, 68
    sheet = Image.new('RGB', (4 * cellw, header + math.ceil(len(plan) / 4) * cellh), '#edf1f5')
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 19)
        titlefont = ImageFont.truetype('C:/Windows/Fonts/seguisb.ttf', 24)
    except OSError:
        font = titlefont = ImageFont.load_default()
    draw.text((16, 10), 'Annotated order  ·  Anatomical right on image right', font=titlefont, fill='#173345')
    draw.text((16, 40), f'{source.channels[0]} green / {source.channels[1]} red preview; TIFF retains separate 16-bit channels. Yellow R = marked point.', font=font, fill='#37586b')
    for index, (row, preview) in enumerate(zip(plan, thumbnails)):
        x, y = (index % 4) * cellw + 10, (index // 4) * cellh + header
        sheet.paste(preview, (x, y + 30))
        repeat = ' · flipped' if row['horizontal_flip'] else ''
        draw.text((x, y + 3), f"Slice {row['slice_index']} · image {row['input_image_index_1based']}{repeat}", font=font, fill='#173345')
        rx = x + (row['output_right_x_px'] + .5) / shape[-1] * preview.width
        ry = y + 30 + (row['output_right_y_px'] + .5) / shape[-2] * preview.height
        draw.ellipse((rx - 5, ry - 5, rx + 5, ry + 5), outline='#ffd34e', width=2)
        draw.text((rx + 8, ry - 20), 'R', font=font, fill='#ffd34e')
    preview_path = Path(str(stem) + '_preview.png')
    sheet.save(preview_path)
    report = {'completed_utc': datetime.now(timezone.utc).isoformat(), 'input_path': str(input_path),
              'output_path': str(output), 'output_bytes': output.stat().st_size,
              'output_sha256': sha256(output), 'annotations_sha256': hashlib.sha256(annotations_bytes).hexdigest(),
              'annotations_snapshot': str(snapshot), 'source_index_sha256': sha256(source_path),
              'shape_zcyx': list(shape), 'channels': source.channels, 'dtype': 'uint16',
              'format': 'OME-BigTIFF' if use_ome else 'ImageJ TIFF',
              'anatomical_right_display': 'image right', 'horizontal_flips': sum(r['horizontal_flip'] for r in plan),
              'rotation_degrees': 0, 'resampling': False,
              'calibration': 'Per-scan native pixel size in source index and OME map annotation; no common physical scale assigned.' if source.scanner else 'pixel; physical scale unassigned',
              'duplicates': 'All scans kept; equal slice numbers retain original scan order.',
              'verified_pages': len(page_hashes), 'verification': 'Pillow decoded every output page; SHA-256 matches source page after the specified horizontal flip.',
              'page_pixel_sha256': page_hashes, 'exporter_version': '1.1.0', 'plan': plan}
    if Path(__file__).exists():
        report['export_script_sha256'] = sha256(__file__)
    atomic_write(Path(str(stem) + '_verification.json'), json.dumps(report, indent=2) + '\n')
    log(json.dumps({'output': str(output), 'verified_pages': len(page_hashes), 'flipped_images': report['horizontal_flips']}))
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.config, args.output)
