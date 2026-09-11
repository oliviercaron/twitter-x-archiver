#!/usr/bin/env python3
"""Create a clean source distribution, portable between Windows and macOS."""
import argparse
import json
from pathlib import Path
import shutil
import zipfile
import build_extensions

ROOT=Path(__file__).resolve().parent
MODULES=('archive_server','archive_urls','archive','app_paths','categories','collect_zevent2026',
         'discussion_archive','export_results','export_service','install_native_host',
         'manual_archive','media','normalize','remove_post','server_control','session_store',
         'storage_manager','summaries','summary_index','runtime_check','build_extensions','build_exe','build_distribution')


def package(destination):
    dest=Path(destination)
    archive=dest.with_suffix('.zip')
    if dest.exists() or archive.exists():raise ValueError('Choisissez un nouveau dossier de distribution.')
    dest.mkdir(parents=True)
    for module in MODULES:shutil.copyfile(ROOT/(module+'.py'),dest/(module+'.py'))
    shutil.copyfile(ROOT/'requirements.txt',dest/'requirements.txt')
    shutil.copyfile(ROOT/'INSTALLATION_MULTINAVIGATEUR.md',dest/'INSTALLATION_MULTINAVIGATEUR.md')
    for name in ('README.md','LICENSE','.gitignore'):
        if (ROOT/name).is_file():shutil.copyfile(ROOT/name,dest/name)
    if (ROOT/'docs').is_dir():shutil.copytree(ROOT/'docs',dest/'docs')
    for directory in ('manual_ui','packaging','safari'):
        for p in (ROOT/directory).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.swift','.md','.yaml','.js','.css','.html','.plist','.command','.entitlements'):
                target=dest/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    # Only the code of the native bridge: existing machine paths are excluded.
    (dest/'native_host').mkdir()
    shutil.copyfile(ROOT/'native_host/host.py',dest/'native_host/host.py')
    shutil.copyfile(ROOT/'packaging/config.yaml',dest/'config.yaml')
    shutil.copyfile(ROOT/'packaging/streamers.json',dest/'packaging/streamers.json')
    (dest/'streamers.json').write_text('{"streamers":[]}\n',encoding='utf-8')
    for browser in build_extensions.TARGETS:
        folder=dest/('chrome-extension' if browser=='chrome' else browser+'-extension')
        build_extensions.build(browser,folder)
    for name in ('installer.command','demarrer.command'):
        shutil.copyfile(ROOT/'packaging'/name,dest/name);(dest/name).chmod(0o755)
    (dest/'INSTALLER.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\npy -3 -m venv .venv\r\nif errorlevel 1 exit /b 1\r\n".venv\\Scripts\\python.exe" -m pip install -r requirements.txt\r\nif errorlevel 1 exit /b 1\r\n".venv\\Scripts\\python.exe" install_native_host.py\r\npause\r\n',encoding='utf-8')
    (dest/'DEMARRER.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\n".venv\\Scripts\\python.exe" -c "import server_control,webbrowser; s=server_control.start(); webbrowser.open(\'http://127.0.0.1:18765\') if s.get(\'ok\') else print(\'Le service ne demarre pas.\')"\r\npause\r\n', encoding='utf-8')
    for name,original in (('INSTALL.cmd','INSTALLER.cmd'),('START.cmd','DEMARRER.cmd')):
        (dest/name).write_text('@echo off\r\ncall "%~dp0'+original+'"\r\n',encoding='utf-8')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(dest.rglob('*')):
            if p.is_file():z.write(p,Path(dest.name)/p.relative_to(dest))
    return archive


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args();print(package(args.output))

if __name__=='__main__':main()
