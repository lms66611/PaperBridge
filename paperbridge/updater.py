"""Install verified local release bundles after the portable app exits."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import tempfile
import time
import zipfile

from . import __version__
from .storage import atomic_json


def version_key(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+(?:\.\d+){2}',value):
        raise ValueError('更新包版本号无效')
    return tuple(map(int,value.split('.')))


def allowed(name):
    path=pathlib.PurePosixPath(name)
    return (len(path.parts)>=3 and path.parts[:2]==('_internal','paperbridge-app')
            and path.suffix=='.py') or name in ('README.md','zotero/PaperBridge.xpi')


def inspect_bundle(package,current=__version__):
    """Validate an entire bundle before showing it as available."""
    package=pathlib.Path(package)
    with zipfile.ZipFile(package) as archive:
        infos=archive.infolist()
        names=[info.filename for info in infos]
        if len(names)!=len(set(names)) or 'manifest.json' not in names:
            raise ValueError('更新包目录重复或缺少清单')
        if any(info.is_dir() or (info.external_attr>>16)&0o170000==0o120000 for info in infos):
            raise ValueError('更新包包含不支持的目录或链接')
        manifest=json.loads(archive.read('manifest.json'))
        version=manifest.get('version')
        if version_key(version)<=version_key(current):return None
        files=manifest.get('files')
        if not isinstance(files,dict) or not files or set(names)!={'manifest.json',*files}:
            raise ValueError('更新包文件与清单不一致')
        if sum(info.file_size for info in infos)>512*1024*1024:
            raise ValueError('更新包超过 512 MB')
        for info in infos:
            if info.filename=='manifest.json':continue
            name=info.filename
            if not allowed(name) or pathlib.PurePosixPath(name).is_absolute() or '..' in pathlib.PurePosixPath(name).parts:
                raise ValueError('更新包包含不允许修改的文件：'+name)
            expected=files[name]
            if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):
                raise ValueError('更新包校验值无效：'+name)
            digest=hashlib.sha256()
            with archive.open(info) as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            if digest.hexdigest()!=expected:raise ValueError('更新包校验失败：'+name)
        return {'version':version,'package':package,'files':files}


def find_update(root,current=__version__):
    folder=pathlib.Path(root)/'updates'
    available=[];errors=[]
    for package in folder.glob('PaperBridge-*-update.zip'):
        try:
            found=inspect_bundle(package,current)
            if found:available.append(found)
        except (OSError,ValueError,KeyError,zipfile.BadZipFile) as exc:
            errors.append(f'{package.name}: {exc}')
    return max(available,key=lambda item:version_key(item['version']),default=None),errors


def wait_for_exit(pid,timeout=120):
    if os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        kernel.OpenProcess.restype=ctypes.c_void_p
        handle=kernel.OpenProcess(0x00100000,False,int(pid))
        if not handle:return
        try:
            if kernel.WaitForSingleObject(ctypes.c_void_p(handle),int(timeout*1000))!=0:
                raise TimeoutError('等待 PaperBridge 退出超时')
        finally:kernel.CloseHandle(ctypes.c_void_p(handle))
    else:
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:os.kill(int(pid),0)
            except ProcessLookupError:return
            time.sleep(.2)
        raise TimeoutError('等待 PaperBridge 退出超时')


def apply_bundle(root,package,expected_version):
    root=pathlib.Path(root).resolve();package=pathlib.Path(package).resolve()
    updates=(root/'updates').resolve()
    if package.parent!=updates:raise ValueError('只安装便携目录 updates 文件夹中的更新包')
    bundle=inspect_bundle(package)
    if not bundle or bundle['version']!=expected_version:raise ValueError('更新包版本已变化，请重新检查')
    stage=pathlib.Path(tempfile.mkdtemp(prefix='.stage-',dir=updates))
    backup=updates/'backups'/expected_version
    replaced=[]
    try:
        with zipfile.ZipFile(package) as archive:
            for name in bundle['files']:
                destination=stage/pathlib.PurePosixPath(name)
                destination.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(name) as source,destination.open('wb') as target:
                    shutil.copyfileobj(source,target)
        backup.mkdir(parents=True,exist_ok=True)
        for name in bundle['files']:
            destination=root/pathlib.PurePosixPath(name)
            old=backup/pathlib.PurePosixPath(name)
            destination.parent.mkdir(parents=True,exist_ok=True)
            old.parent.mkdir(parents=True,exist_ok=True)
            existed=destination.exists()
            if existed:shutil.copy2(destination,old)
            replaced.append((destination,old,existed))
            os.replace(stage/pathlib.PurePosixPath(name),destination)
        atomic_json(updates/'last-update.json',{'status':'installed','version':expected_version})
    except Exception:
        for destination,old,existed in reversed(replaced):
            if existed:shutil.copy2(old,destination)
            else:destination.unlink(missing_ok=True)
        raise
    finally:shutil.rmtree(stage,ignore_errors=True)
