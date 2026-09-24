"""Make display-only gallery previews from the unchanged, full-resolution TIFF."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image
import tifffile
from image_source import ImageSource


def create_previews(config, progress=None, skip_existing=False):
    config = Path(config).resolve()
    settings = json.loads(config.read_text(encoding='utf-8'))
    output = config.parent / settings.get('preview_dir', 'previews')
    output.mkdir(parents=True, exist_ok=True)
    source = ImageSource(config)
    for i in range(source.shape[0]):
            for c in range(2):
                target = output / source.preview_filename(i, c)
                if skip_existing and target.exists():
                    continue
                im = source.display_image(i, c, preview=True)
                im.thumbnail((700, 500), Image.Resampling.LANCZOS)
                im.save(target)
            if progress:
                progress(f'Preparing overview: image {i + 1} of {source.shape[0]}…')
            elif sys.stdout is not None:
                print(f'Preview {i + 1} ready', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    create_previews(parser.parse_args().config)
