"""Copy public runtime assets only, then verify the recorded distribution hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]

def verify():
    manifest = json.loads((ROOT / 'assets-manifest.json').read_text('utf-8'))
    failed = []
    for entry in manifest['resources']:
        file = (ROOT / 'assets' / entry['path']).resolve()
        if not file.is_relative_to((ROOT / 'assets').resolve()):
            raise ValueError('Resource path escapes assets')
        if not file.is_file() or file.stat().st_size != entry['size']:
            failed.append(entry['path'])
            continue
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != entry['sha256']:
            failed.append(entry['path'])
    if failed:
        raise SystemExit('Missing or changed resources: ' + ', '.join(failed))
    print('Verified', len(manifest['resources']), 'runtime assets')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-portable', type=Path)
    args = parser.parse_args()
    if args.from_portable:
        source = args.from_portable.resolve() / 'assets'
        if not source.is_dir():
            raise SystemExit('The selected folder has no assets directory')
        destination = ROOT / 'assets'
        if source.resolve() == destination.resolve():
            raise SystemExit('Source and destination must differ')
        print('Copying runtime assets only')
        shutil.copytree(source, destination, dirs_exist_ok=True)
    verify()

if __name__ == '__main__':
    main()
