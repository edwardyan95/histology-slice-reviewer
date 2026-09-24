# Direct scanner import

Select the directory containing one or more `.vsi` files and their matching
`_<vsi filename without extension>_` folders. Keep scanner filenames unchanged.
All compatible tissue scans across those slides appear in one overview; you
assign the anatomical slice order yourself. Annotations and exports are stored
in the selected directory. No intermediate full-resolution TIFF is needed.

## Supported layout

The reader supports little-endian cellSens VSI metadata with two-dimensional,
two-channel uint16 grayscale tissue images stored as JPEG2000 ETS tiles. It
checks VSI stack type, channel dimension, channel names, native image boundary,
tile origin, and ETS native dimensions. Only ordinary tissue stacks (type 0)
are selected. Slide label, overview, masks, focus maps, and blob files are excluded.
The stack ID in the VSI identifies its exact `stack<ID>/frame_*.ets` file.
Multiple or missing frame files, orphaned frame files, extra Z/time dimensions,
nonzero tile origins, and inconsistent channel coverage are rejected with an
explanation. This is a focused importer, not a universal VSI converter.

Only stored tiles are decoded. Absent tiles use the background value stored in
the ETS header; both channels must have the same tile coverage. Partial edge
tiles are trimmed to the native image boundary declared by the VSI, and smaller
images are center-padded with zeros to a common canvas. Tissue pixels are never
resampled. Pyramid levels are used only for fast overview previews. Detailed
review and export read level 0. Display ranges come from the scanner and affect
previews only, not exported uint16 values.

## Export and provenance

Scanner projects export as **OME-BigTIFF**, including datasets above 4 GB.
Use Fiji's Bio-Formats importer to open the OME stack; choose grayscale if you
want to view one channel at a time. Files are uncompressed, so allow space for
`sections × 2 channels × canvas width × canvas height × 2 bytes` plus reports.

VSI calibration sometimes varies slightly between scans. Each exact native
pixel size and unit is retained in the exported source index and in an OME map
annotation for its output section. No averaged common scale or Z spacing is
invented. The exported TIFF does not set a global micrometre-per-pixel scale.
The source index also records native boundaries, center padding, source hashes,
display ranges, and excluded scanner records. Annotation coordinates refer to
the padded full-resolution canvas; source coordinates subtract its padding.

Original VSI/ETS signatures are checked on reopening. Full SHA-256 hashes are
checked before export, and every exported page is independently decoded by
Pillow and compared to the expected sorted/reflected pixels. Keep the source
index, annotation snapshot, manifest, and verification report with each export.

## Format reference and tests

The format interpretation was checked against the public
[OME Bio-Formats CellSensReader](https://github.com/ome/bioformats/blob/develop/components/formats-gpl/src/loci/formats/in/CellSensReader.java).
The Python implementation is specific to the validated layout above; no Java
runtime or Bio-Formats package is distributed with the app.

Synthetic tests generate small VSI metadata trees and lossless JPEG2000 ETS
tiles with independently known pixel arrays. They check ID-based mapping when
directory and metadata order differ, sparse backgrounds, trimmed edges, padding,
channel separation, missing/truncated input rejection, and sorted/flipped
OME-BigTIFF export verified against the expected pixels. No experimental data
is included in tests or releases.
