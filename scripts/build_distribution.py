#!/usr/bin/env python3
"""Create a source download with the same layout as the repository."""
import argparse
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TREES = ('src', 'scripts', 'launchers', 'extension', 'platforms', 'docs', 'tests')
ROOT_FILES = ('README.md', 'LICENSE', 'pyproject.toml', '.gitignore', '.gitattributes')
EXCLUDED_DIRS = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
                 '.venv', 'node_modules', '.DS_Store'}
EXCLUDED_SUFFIXES = {'.pyc', '.pyo', '.db', '.tmp', '.partial', '.bak'}


def package(destination):
    """Copy source and documentation, leaving builds and local data behind."""
    destination = Path(destination)
    archive = destination.with_suffix('.zip')
    if destination.exists() or archive.exists():
        raise ValueError('Output already exists. Choose a new distribution directory.')
    destination.mkdir(parents=True)
    for name in ROOT_FILES:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, destination / name)
    for name in TREES:
        source = ROOT / name
        if not source.is_dir():
            continue
        for item in sorted(source.rglob('*')):
            relative = item.relative_to(ROOT)
            if (not item.is_file() or item.is_symlink()
                    or any(part in EXCLUDED_DIRS or part.endswith('.egg-info') for part in relative.parts)
                    or item.suffix.lower() in EXCLUDED_SUFFIXES):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            if target.suffix in ('.command', '.sh'):
                target.chmod(0o755)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(destination.rglob('*')):
            if item.is_file():
                bundle.write(item, Path(destination.name) / item.relative_to(destination))
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    print(package(args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
