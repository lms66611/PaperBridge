"""Apply the documented portable-cache patch to BabelDOC before building."""
import importlib.util
from pathlib import Path

def prepare():
    spec=importlib.util.find_spec('babeldoc')
    if not spec or not spec.submodule_search_locations:
        raise RuntimeError('Install BabelDOC 0.6.4 first.')
    path=Path(next(iter(spec.submodule_search_locations)))/'const.py'
    text=path.read_text('utf-8')
    before='CACHE_FOLDER = Path.home() / ".cache" / "babeldoc"'
    after='CACHE_FOLDER = Path(os.environ["PAPERBRIDGE_BABELDOC_CACHE"]) if os.environ.get("PAPERBRIDGE_BABELDOC_CACHE") else Path.home() / ".cache" / "babeldoc"'
    if before not in text and after not in text:
        raise RuntimeError('Unexpected BabelDOC const.py; review the vendor patch.')
    path.write_text(text.replace(before,after),'utf-8')
    return path

if __name__=='__main__':print(prepare())
