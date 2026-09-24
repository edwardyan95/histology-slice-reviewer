# Build a Windows release

Use Python 3.12 on Windows with Tk. From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-build.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python build_release.py
```

The build runs a synthetic end-to-end test of the actual executable before producing `dist/HistologySliceReviewer-v1.1.1-Windows.zip` and `dist/SHA256SUMS.txt`. The archive includes an executable and its runtime folder, instructions, dependency version record, self-test result and bundled third-party license notices. Extract the entire archive; keep the runtime folder with the executable. Version 1.1 uses a folder-based bundle so startup does not need to extract another runtime into the system temporary directory.

Use `python build_release.py --output-root "D:\ReviewerBuild"` to put build intermediates and release artifacts on another drive. The archive contains no experimental images, annotations, local configuration or credentials.

The executable is unsigned and does not modify Windows security settings. Building does not publish a release automatically. Keep the release tag, source commit and checksum together for reproducibility.
