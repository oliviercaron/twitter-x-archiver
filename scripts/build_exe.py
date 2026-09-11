#!/usr/bin/env python3
"""Build the Windows desktop app and its browser extensions.

The output is a complete download folder. Keep app/, extensions/ and the
launchers together when distributing it; the executable needs its _internal
folder. User settings and archives are never included.
"""
import argparse
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from . import build_extensions
except ImportError:
    import build_extensions

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src' / 'twitter_x_archiver'
NAME = 'twitter-x-archiver'

HIDDEN = [
    'twscrape', 'twscrape.api', 'twscrape.models', 'twscrape.utils',
    'curl_cffi', 'curl_cffi.requests', 'imagehash', 'PIL.Image', 'pyarrow',
    'pyarrow.parquet', 'yaml', 'dotenv', 'httpx', 'aiosqlite',
    'twitter_x_archiver.native_host.host', 'twitter_x_archiver.install_native_host',
    'twitter_x_archiver.server_control', 'twitter_x_archiver.summary_index',
    'twitter_x_archiver.summaries', 'twitter_x_archiver.remove_post',
    'twitter_x_archiver.runtime_check', 'webbrowser', 'scipy.fftpack',
]
EXCLUDED = ['tkinter', 'matplotlib', 'scipy.tests', 'pytest', 'pyarrow.tests',
            'numpy.tests', 'PIL.ImageQt']


def write_launchers(target):
    """Put the three double-click shortcuts at the top of the download."""
    for name in ('INSTALL.cmd', 'START.cmd', 'UNINSTALL.cmd'):
        shutil.copyfile(ROOT / 'launchers' / 'windows' / name, target / name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'dist' / ('windows-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    parser.add_argument('--work-dir', type=Path,
                        default=ROOT / 'build' / ('windows-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    args = parser.parse_args(argv)
    destination = args.output.resolve()
    work = args.work_dir.resolve()
    if destination.exists():
        parser.error('Output already exists. Choose a new --output directory.')
    if sys.platform != 'win32':
        parser.error('Build the Windows executable using Python on Windows.')

    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir',
               '--name', NAME, '--console', '--paths', str(ROOT / 'src'),
               '--add-data', f'{PACKAGE / "ui"};ui',
               '--add-data', f'{PACKAGE / "defaults"};defaults',
               '--distpath', str(destination), '--workpath', str(work),
               '--specpath', str(work)]
    for module in HIDDEN:
        command += ['--hidden-import', module]
    for module in EXCLUDED:
        command += ['--exclude-module', module]
    command.append(str(ROOT / 'scripts' / 'run_app.py'))
    print('Building the app. This takes a few minutes.', flush=True)
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        return result.returncode

    (destination / NAME).rename(destination / 'app')
    for browser in build_extensions.TARGETS:
        build_extensions.build(browser, destination / 'extensions' / browser)
    write_launchers(destination)
    for name in ('README.md', 'LICENSE'):
        shutil.copyfile(ROOT / name, destination / name)
    if (ROOT / 'docs').is_dir():
        shutil.copytree(ROOT / 'docs', destination / 'docs')
    size = sum(p.stat().st_size for p in destination.rglob('*') if p.is_file())
    print(f'\n{destination}\n{size / 1e6:.0f} MB total. Distribute the entire folder.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
