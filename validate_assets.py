"""Check a portable resource tree against the included SHA256 manifest."""
from pathlib import Path
import argparse,hashlib,json

def main():
    parser=argparse.ArgumentParser();parser.add_argument('root',nargs='?',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args();root=args.root.resolve()
    manifest=json.loads((root/'资源清单.json').read_text('utf-8'))
    failed=[]
    for entry in manifest['resources']:
        path=root/'assets'/entry['path']
        if not path.is_file() or path.stat().st_size!=entry['size']:
            failed.append(entry['path']);continue
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        if digest!=entry['sha256']:failed.append(entry['path'])
    print('Resources:',len(manifest['resources']),'Failures:',len(failed))
    for path in failed:print(path)
    raise SystemExit(bool(failed))

if __name__=='__main__':main()
