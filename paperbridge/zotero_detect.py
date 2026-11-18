"""Read-only discovery. Library files and profile preferences are never edited."""
from __future__ import annotations
import configparser,os,pathlib

def discover():
    candidates=[]
    if os.name=='nt':
        import winreg
        for hive in [winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE]:
            for sub in [r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\zotero.exe',
                        r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\zotero.exe']:
                try:
                    with winreg.OpenKey(hive,sub) as key:candidates.append(pathlib.Path(winreg.QueryValue(key,None)))
                except OSError:pass
        for env in ['ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA']:
            if os.environ.get(env):candidates.append(pathlib.Path(os.environ[env])/'Zotero/zotero.exe')
        # App shortcut target can point at a user-selected installation drive.
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,r'Zotero\shell\open\command') as key:
                value=winreg.QueryValue(key,None)
                if value.startswith('"'):candidates.append(pathlib.Path(value.split('"')[1]))
        except OSError:pass
    base=pathlib.Path(os.environ.get('APPDATA',''))/'Zotero/Zotero'
    ini=configparser.ConfigParser()
    try:ini.read(base/'profiles.ini',encoding='utf-8')
    except (OSError,configparser.Error):pass
    profiles=[]
    for name in ini.sections():
        if not name.startswith('Profile') or not ini.has_option(name,'Path'):continue
        path=pathlib.Path(ini[name]['Path'])
        if ini[name].get('IsRelative','1')=='1':path=base/path
        if path.is_dir():profiles.append({'name':ini[name].get('Name',name),'path':str(path),
            'bridge_installed':(path/'extensions/paperbridge@local.app.xpi').exists()})
    return {'executable':next((str(p) for p in candidates if p.is_file()),None),'profiles':profiles}
