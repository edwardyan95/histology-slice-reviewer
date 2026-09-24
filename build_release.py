"""Build and smoke-test a portable Windows release, with third-party license notices."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = '1.0.0'


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows executable on Windows.')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
                    '--windowed', '--name', 'HistologySliceReviewer', 'app.py'], cwd=ROOT, check=True)
    dist = ROOT / 'dist'
    # Discard only this build's intermediates before the one-file executable extracts its runtime.
    intermediate = (ROOT / 'build' / 'HistologySliceReviewer').resolve()
    if intermediate.parent != (ROOT / 'build').resolve():
        raise RuntimeError('Unexpected build directory.')
    if intermediate.exists():
        shutil.rmtree(intermediate)
    report_path = dist / 'executable-self-test.json'
    subprocess.run([str(dist / 'HistologySliceReviewer.exe'), '--self-test', str(report_path)], check=True, timeout=180)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('status') != 'PASS':
        raise RuntimeError(f'Executable self-test failed: {report}')
    package = dist / f'HistologySliceReviewer-v{VERSION}-Windows'
    package.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'README.md', package)
    shutil.copy2(ROOT / 'QUICK_START.txt', package)
    shutil.copy2(report_path, package)
    licenses = package / 'third_party_licenses'
    licenses.mkdir(exist_ok=True)
    versions = {'app': VERSION, 'python': platform.python_version(), 'platform': platform.platform()}
    for name in ('numpy', 'Pillow', 'tifffile', 'pyinstaller', 'pyinstaller-hooks-contrib'):
        distribution = importlib.metadata.distribution(name)
        versions[name] = distribution.version
        destination = licenses / name
        destination.mkdir(exist_ok=True)
        for index, file in enumerate(distribution.files or []):
            if '.dist-info/' in str(file).replace('\\', '/') and any(word in str(file).lower() for word in ('license', 'copying', 'notice')):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    shutil.copy2(source, destination / f'{index}_{source.name}')
    for source in [Path(sys.base_prefix) / 'LICENSE.txt', *list((Path(sys.base_prefix) / 'tcl').rglob('license.terms'))]:
        if source.exists():
            shutil.copy2(source, licenses / f'{source.parent.name}_{source.name}')
    (package / 'build_versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
    archive = dist / (package.name + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
        zipped.write(dist / 'HistologySliceReviewer.exe', package.name + '/HistologySliceReviewer.exe')
        for path in sorted(package.rglob('*')):
            if path.is_file():
                zipped.write(path, path.relative_to(dist))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist / 'SHA256SUMS.txt').write_text(f'{checksum}  {archive.name}\n', encoding='utf-8')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': checksum, 'self_test': 'PASS'}))


if __name__ == '__main__':
    main()
