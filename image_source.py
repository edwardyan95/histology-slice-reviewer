"""Shared TIFF/ETS image access; all returned full-resolution values are uint16."""
import json
from pathlib import Path
import numpy as np
from PIL import Image
import tifffile
from scanner import EtsFile, signature, digest_file


class ImageSource:
    def __init__(self, config):
        self.config_path = Path(config).resolve()
        self.settings = json.loads(self.config_path.read_text(encoding='utf-8'))
        resolve = lambda key: (self.config_path.parent / self.settings[key]).resolve()
        self.index_path = resolve('source_index')
        index = json.loads(self.index_path.read_text(encoding='utf-8'))
        self.sources = index['sources']
        self.vsi_hashes = index.get('vsi_sha256', {})
        self.scanner = self.settings.get('input_kind') == 'vsi'
        self.channels = self.settings.get('channels', ['FITC', 'Cy3'])
        self.input_path = resolve('input_dir' if self.scanner else 'input_tiff')
        if self.scanner:
            if digest_file(self.index_path) != self.settings.get('source_index_sha256'):
                raise ValueError('Scanner source index has changed.')
            self.shape = tuple(self.settings['shape_zcyx'])
            self.validate_files()
        else:
            with tifffile.TiffFile(self.input_path) as tif:
                shape = tuple(tif.series[0].shape)
                if tif.series[0].axes != 'ZCYX' or len(shape) != 4 or shape[1] != 2 or tif.series[0].dtype != np.dtype('uint16'):
                    raise ValueError('The TIFF must be a two-channel uint16 ZCYX stack.')
                self.shape = shape
        if self.shape[:2] != (len(self.sources), 2):
            raise ValueError('Image dimensions do not match the source index.')

    def validate_files(self, full_hash=False, progress=None):
        if self.scanner:
            for expected in self.settings['input_signatures']:
                path = self.input_path / expected['name']
                if signature(path, self.input_path) != expected:
                    raise ValueError(f'Scanner source was modified: {path.name}')
            if full_hash:
                for name, expected in self.vsi_hashes.items():
                    if digest_file(self.input_path / name) != expected:
                        raise ValueError('VSI metadata checksum changed.')
                for i, source in enumerate(self.sources):
                    if progress:
                        progress(f'Checking original scan {i+1} of {len(self.sources)}…')
                    if digest_file(self.input_path / source['ets_relative_path']) != source['source_sha256']:
                        raise ValueError('Scanner pixel file checksum changed.')

    def read_channel(self, index, channel, preview=False):
        if not self.scanner:
            with tifffile.TiffFile(self.input_path) as tif:
                return tif.pages[index * 2 + channel].asarray()
        source = self.sources[index]
        reader = EtsFile(self.input_path / source['ets_relative_path'], source['width'], source['height'], source['tile_origin'])
        level = 0
        if preview:
            choices = [lev for lev in reader.levels if max(source['width'], source['height']) / 2**lev >= 700]
            level = max(choices, default=0)
        pixels = reader.read_channel(channel, level)
        if level:
            # Place display-only pyramid pixels on a canvas with exactly the full-resolution aspect ratio.
            scale = 700 / max(self.shape[-2:])
            size = (max(1, round(self.shape[-1]*scale)), max(1, round(self.shape[-2]*scale)))
            canvas = Image.new('I;16', size, 0)
            im = Image.fromarray(pixels).resize((max(1, round(source['width']*scale)), max(1, round(source['height']*scale))), Image.Resampling.NEAREST)
            canvas.paste(im, (round(source['padding_left']*scale), round(source['padding_top']*scale)))
            return np.asarray(canvas)
        result = np.zeros(self.shape[-2:], np.uint16)
        x, y = source['padding_left'], source['padding_top']
        result[y:y+source['height'], x:x+source['width']] = pixels
        return result

    def display_range(self, index, channel):
        return self.sources[index].get('display_ranges', [[2, 573], [0, 418]])[channel]

    def display_image(self, index, channel, preview=False, pixels=None):
        low, high = self.display_range(index, channel)
        if pixels is None:
            pixels = self.read_channel(index, channel, preview=preview)
        lut = np.clip((np.arange(65536, dtype=np.float32)-low) * (255./(high-low)), 0, 255).astype(np.uint8)
        return Image.fromarray(lut[pixels])

    def preview_filename(self, index, channel):
        # Channel names are human labels, never filesystem path components for scanner imports.
        name = f'C{channel+1}' if self.scanner else self.channels[channel]
        return f'{index+1:02d}_{name}.png'
