"""Rebuild the installable XPI from the readable plugin sources."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def build():
    folder = ROOT / 'zotero'
    target = folder / 'PaperBridge.xpi'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(folder.rglob('*')):
            if source.is_file() and source != target and '__pycache__' not in source.parts:
                archive.write(source, source.relative_to(folder))
    return target

if __name__ == '__main__':
    print(build())
