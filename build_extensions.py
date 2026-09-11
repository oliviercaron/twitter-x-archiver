#!/usr/bin/env python3
"""Build browser-specific extensions from the shared sources, without user data."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
FIREFOX_ID = 'archive-x@local.extension'
SAFARI_APP_ID = 'org.archivex.app'
TARGETS = ('chrome', 'firefox', 'safari')


def manifest_for(source, target):
    if target not in TARGETS:
        raise ValueError('Unknown browser target')
    result = copy.deepcopy(source)
    # Host permissions include the exact pages where our content scripts run.
    for host in ('https://www.x.com/*', 'https://www.twitter.com/*'):
        if host not in result['host_permissions']:
            result['host_permissions'].append(host)
    if target != 'chrome':
        result.pop('key', None)
        result['background'] = {'scripts': ['archive_config.js', 'background.js']}
    if target == 'firefox':
        result['browser_specific_settings'] = {'gecko': {
            'id': FIREFOX_ID, 'strict_min_version': '140.0',
            # Posts/URLs and the user's two authentication cookies go to the
            # companion process on this machine, outside the browser itself.
            'data_collection_permissions': {'required': [
                'authenticationInfo', 'websiteContent', 'browsingActivity']},
        }}
    if target == 'safari':
        result['browser_specific_settings'] = {'safari': {'strict_min_version': '16.4'}}
    return result


def build(target, destination):
    destination = Path(destination)
    archive = destination.with_suffix('.zip')
    if destination.exists() or archive.exists():
        raise ValueError(f'Output already exists: {destination}. Choose a new output folder.')
    source = ROOT / 'chrome-extension'
    manifest = manifest_for(json.loads((source / 'manifest.json').read_text(encoding='utf-8')), target)
    destination.mkdir(parents=True)
    # Explicit extension assets only: no profiles, cookies, test dumps or .env.
    assets = [p for p in source.iterdir() if p.is_file() and p.suffix in ('.js', '.css', '.html')]
    for asset in assets:
        shutil.copyfile(asset, destination / asset.name)
    shutil.copytree(source / '_locales', destination / '_locales')
    config = {'nativeHost': SAFARI_APP_ID if target == 'safari' else 'com.zevent.archive', 'browser': target}
    (destination / 'archive_config.js').write_text(
        'globalThis.ARCHIVE_CONFIG = ' + json.dumps(config) + ';\n', encoding='utf-8')
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if (ROOT/'LICENSE').is_file():shutil.copyfile(ROOT/'LICENSE',destination/'LICENSE')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(destination.rglob('*')):
            if item.is_file():
                bundle.write(item, item.relative_to(destination))
    return destination, archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=(*TARGETS, 'all'), default='all')
    parser.add_argument('--output', type=Path, default=ROOT/'dist'/'extensions')
    args = parser.parse_args()
    for target in TARGETS if args.browser == 'all' else (args.browser,):
        folder, archive = build(target, args.output/target)
        print(folder)
        print(archive)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
