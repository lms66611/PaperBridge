"""Build the source release in a fresh repository-local staging directory."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from prepare_vendor import prepare

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('assets', nargs='?', type=Path, default=ROOT / 'assets')
    args = parser.parse_args()
    assets = args.assets.resolve()
    if not assets.is_dir():
        raise SystemExit('Prepare runtime assets first; see docs/ASSETS.md')
    prepare()
    subprocess.run([sys.executable, str(ROOT / 'tools/build_zotero.py')], check=True)
    stage = ROOT / 'build' / ('release-' + uuid.uuid4().hex[:10])
    stage.mkdir(parents=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
        '--name', 'PaperBridge', '--distpath', str(stage / 'dist'),
        '--workpath', str(stage / 'work'), '--specpath', str(stage),
        '--collect-all', 'tkinterdnd2', '--collect-all', 'babeldoc',
        '--collect-all', 'onnxruntime', '--collect-all', 'onnx',
        '--collect-all', 'pymupdf', '--collect-all', 'freetype',
        '--collect-submodules', 'keyring.backends', '--collect-all', 'certifi',
        '--hidden-import', 'pyzstd', '--hidden-import', 'rtree',
        '--hidden-import', 'reportlab.pdfbase._fontdata',
        '--add-data', str(ROOT / 'resources') + ';resources',
        '--paths', str(ROOT), str(ROOT / 'main.py')]
    subprocess.run(command, cwd=ROOT, check=True)
    bundle = stage / 'dist/PaperBridge'
    shutil.copytree(assets, bundle / 'assets')
    # Keep readable source alongside a frozen build for the local update layout.
    app = bundle / '_internal/paperbridge-app'
    app.mkdir(parents=True)
    shutil.copy2(ROOT / 'main.py', app / 'main.py')
    shutil.copytree(ROOT / 'paperbridge', app / 'paperbridge',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT / 'resources', app / 'resources')
    for backend in ('cpu', 'vulkan'):
        directory = bundle / 'assets/llama' / backend
        if directory.exists():
            for name in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
                dll = bundle / '_internal' / name
                if dll.is_file():
                    shutil.copy2(dll, directory / name)
    for name in ('zotero', 'licenses', 'docs', 'vendor-source'):
        shutil.copytree(ROOT / name, bundle / name)
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'assets-manifest.json'):
        shutil.copy2(ROOT / name, bundle / name)
    output = ROOT / 'dist/PaperBridge-Portable'
    if output.exists():
        raise SystemExit('dist/PaperBridge-Portable exists; preserve it before rebuilding. New build: ' + str(bundle))
    output.parent.mkdir(exist_ok=True)
    shutil.move(str(bundle), output)
    print(output)

if __name__ == '__main__':
    main()
