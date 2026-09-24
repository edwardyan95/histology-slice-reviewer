# Build a Windows release

Use Python 3.12 on Windows with Tk. From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-build.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python build_release.py
```

The build runs a synthetic end-to-end test of the actual executable before producing `dist/HistologySliceReviewer-v1.0.0-Windows.zip` and `dist/SHA256SUMS.txt`. The archive includes an executable, instructions, dependency version record, self-test result and bundled third-party license notices. It contains no experimental images, annotations, local configuration or credentials.

The executable is unsigned and does not modify Windows security settings. Building does not publish a release automatically. Keep the release tag, source commit and checksum together for reproducibility.
