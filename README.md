# Histology Slice Reviewer

A local desktop app for numbering tissue sections, marking the anatomical right hemisphere, and exporting a full-resolution TIFF in your chosen order. The overview puts the sections together so you can compare them while choosing their slice numbers.

## Easiest start: Windows app

1. Open [Releases](https://github.com/edwardyan95/histology-slice-reviewer/releases/latest) and download **HistologySliceReviewer-v1.0.0-Windows.zip**.
2. Right-click the ZIP → **Extract All**, then double-click **HistologySliceReviewer.exe**. No Python installation is needed.
3. Choose the folder containing your prepared full-resolution TIFF. The app opens your existing annotations when present.

The portable executable is unsigned. Its ZIP checksum is included in `SHA256SUMS.txt` on the release page. The app works locally and does not upload scan data.

## Start from source on Windows

1. Download this repository using **Code → Download ZIP**, then **Extract All**. Put the extracted app on your local computer, separate from your scan folder.
2. Install **Python 3.12 or 3.13** from [python.org](https://www.python.org/downloads/windows/) if needed. Keep **Tcl/Tk**, **pip**, and the **Python launcher** enabled in the installer.
3. Double-click **Start Reviewer.cmd**. The first start installs the tested libraries into a private `.venv` folder; allow a few minutes and keep an internet connection available.
4. Choose your scan folder. If the app cannot identify a single `*full_resolution*stack.tif`, select the original two-channel TIFF when prompted.

Later starts use the same launcher and installed libraries. There are no account requirements or uploads from the app. On this workstation, the existing prepared full-resolution ETS stack can be selected directly, and its saved annotations will reopen.

## Review and export

1. In **All images**, enter a slice number under each tissue image and click **Save order**. Repeated numbers are allowed for repeat scans. The overview can scroll for larger datasets.
2. Click a thumbnail to open the detailed view. Click a point **inside the anatomical right hemisphere**, then **Save & next**. A yellow **R** marks your click.
3. Use **FITC** or **Cy3** to see one channel in grayscale. **Overlay** uses FITC green and Cy3 red. Wheel = zoom; right-drag = pan; **Fit** = entire image; **1:1** = native pixels.
4. Once every image has a number and a right-side point, click **Export ordered TIFF** and choose a new output filename. The default location is your input directory.

The export sorts by your slice numbers and horizontally flips images whose marked right hemisphere is on the left, so **anatomical right appears on image right**. Both channels receive the same flip. Repeat scans stay together, retaining their original order within a repeated slice number. There is no rotation, alignment, cropping, interpolation or intensity scaling. A point does not define a midline or determine up/down orientation. Points close to the image center are rejected as ambiguous.

The app checks every output page against the expected source pixels using an independent TIFF reader. The TIFF opens in grayscale mode with **C1 = FITC** and **C2 = Cy3**. R markers appear only in previews and annotations, never in exported TIFF pixels.

## What data can it open?

- A **prepared, uncompressed, 16-bit TIFF** with axes **ZCYX**, two separate grayscale channels per section and multiple sections. It supports the full-resolution ETS-derived stacks from the accompanying analysis workflow.
- The two channel names and display colors are FITC and Cy3. Default preview ranges are FITC 2–573 and Cy3 0–418; preview clipping does not change raw data.
- If the folder contains `*ETS_export_files/source_index.json`, the app uses it to retain original source identities, padding and existing annotations. Otherwise it creates a source index from the TIFF pixels on first open.
- Use **one dataset per folder**. The app rejects changed TIFFs or mismatched annotation identities rather than attaching annotations to a different dataset.
- Raw `.vsi` / `.ets` scanner files, low-resolution overview images, RGB TIFFs and arbitrary page stacks are **not imported directly**. Convert these to the prepared format first; this app does not claim to be a general slide-scanner converter.
- The ImageJ export supports **less than 4 GB of pixel data**. Keep enough free disk space for an additional uncompressed stack. Preview generation and export may take several minutes on network drives.

## Files saved next to the input TIFF

| File | Purpose |
| --- | --- |
| `slice_annotations.json` | Authoritative, resumable annotations linked to source identities |
| `slice_annotations.csv` | Table of slice numbers, marked points and review status |
| `.slice_review/` | Local configuration, generated index if needed, and overview previews |
| Your chosen `.tif` | Ordered, consistently oriented two-channel stack |
| `*_manifest.csv` | Output order, applied flips, source references and transformed right points |
| `*_annotations_used.json` | Exact annotation snapshot used for that export |
| `*_preview.png` | Display-only contact sheet of the ordered stack |
| `*_verification.json` | Pixel verification, checksums, dimensions and export provenance |

Edits in the detailed view save automatically after a short pause. Overview numbers save with **Save order**, when opening a thumbnail, or when closing that view. Close and reopen to resume. Only one reviewer can write to a dataset at once. Existing output TIFFs are never overwritten; use a new filename for a revised export. Changing annotations later does not change previously exported TIFFs.

Coordinates are zero-based integer pixels, x increasing right and y increasing down. `stack_x_px` / `stack_y_px` refer to the unchanged original TIFF. `source_x_px` / `source_y_px` subtract known source padding. The export manifest records coordinates after flipping. Physical scale is not assigned.

## Troubleshooting

- **Python not found:** install Python 3.12 or 3.13 with Tk and the launcher, then reopen Start Reviewer.cmd.
- **First setup cannot install libraries:** check internet access and rerun. The launcher retries unfinished setup and prints the error.
- **Network folder not found:** reconnect or map the drive using your normal Windows account. The app does not require administrator privileges.
- **Another reviewer is already open:** close the other window for that dataset. A leftover `.review.lock` file alone is harmless; the operating system releases the actual lock when the app exits.
- **Unsupported TIFF:** check that it is the original full-resolution two-channel ZCYX stack, not an RGB preview or a low-resolution scanner export.
- **Annotations do not match:** restore the original TIFF and source index. Keep the original `slice_annotations.json`; do not overwrite it with annotations from another dataset.
- **Interrupted export:** an incomplete `.pending.tif` may remain. Choose a new output filename and retry. Only the final TIFF with a completed verification report should be treated as a successful export.

## Command line and development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py
# Open a specific dataset:
.\.venv\Scripts\python app.py --input "D:\Histology\full_resolution_stack.tif"
# Existing advanced configuration:
.\.venv\Scripts\python app.py --config "D:\Histology\.slice_review\config.json"
# Run synthetic-data tests:
.\.venv\Scripts\python -m unittest discover -s tests -v
```

Keep producing code, export manifests, annotation snapshots and verification reports with analysis outputs. The public repository contains application code, instructions and synthetic tests only. Scan data, real annotations, local paths, credentials and generated previews are excluded.

For a reproducible portable executable, see [Build a Windows release](docs/BUILD.md).
