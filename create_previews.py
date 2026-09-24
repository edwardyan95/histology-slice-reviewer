"""Make display-only gallery previews from the unchanged, full-resolution TIFF."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image
import tifffile


def create_previews(config, progress=None, skip_existing=False):
    config = Path(config).resolve()
    settings = json.loads(config.read_text(encoding='utf-8'))
    output = config.parent / settings.get('preview_dir', 'previews')
    output.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffFile(config.parent / settings['input_tiff']) as tif:
        for i in range(tif.series[0].shape[0]):
            for c, (name, low, high) in enumerate((('FITC', 2, 573), ('Cy3', 0, 418))):
                target = output / f'{i + 1:02d}_{name}.png'
                if skip_existing and target.exists():
                    continue
                raw = tif.pages[i * 2 + c].asarray()
                lut = np.clip((np.arange(65536, dtype=np.float32) - low) * (255. / (high - low)), 0, 255).astype(np.uint8)
                im = Image.fromarray(lut[raw])
                im.thumbnail((700, 500), Image.Resampling.LANCZOS)
                im.save(target)
            if progress:
                progress(f'Preparing overview: image {i + 1} of {tif.series[0].shape[0]}…')
            elif sys.stdout is not None:
                print(f'Preview {i + 1} ready', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    create_previews(parser.parse_args().config)
