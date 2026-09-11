"""Storage location selection never creates an accidental empty archive."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from twitter_x_archiver import app_paths
from twitter_x_archiver import server_control


@pytest.fixture
def locations(monkeypatch, tmp_path):
    app = tmp_path / 'program'
    profile = tmp_path / 'profile' / 'Archivage X'
    assets = tmp_path / 'assets'
    app.mkdir()
    assets.mkdir()
    monkeypatch.setattr(app_paths, 'home', lambda: app)
    monkeypatch.setattr(app_paths, 'user_data', lambda: profile)
    monkeypatch.setattr(app_paths, 'assets', lambda: assets)
    return app, profile, assets


def archive(folder):
    folder.mkdir(parents=True)
    (folder / 'archives.db').write_bytes(b'archive marker')
    return folder


@pytest.mark.parametrize('platform,expected', [
    ('win32', 'local/Archivage X'),
    ('darwin', 'home/Library/Application Support/Archivage X'),
    ('linux', 'xdg/archivage-x'),
])
def test_os_profile_location(monkeypatch, tmp_path, platform, expected):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))
    assert app_paths.user_data() == tmp_path / expected
    assert list(tmp_path.iterdir()) == []


def test_fresh_install_is_stable_without_creating_directories(locations):
    app, profile, _ = locations
    assert app_paths.resolve_data_dir() == profile / 'archives'
    assert app_paths.resolve_data_dir({'data_dir': 'data', '_root': app}) == profile / 'archives'
    assert list(app.iterdir()) == []
    assert not profile.exists()


def test_existing_local_archive_preserved(locations):
    app, profile, _ = locations
    old = archive(app / 'data')
    assert app_paths.resolve_data_dir() == old
    assert not profile.exists()


def test_only_temporary_files_do_not_make_an_archive(locations):
    app, profile, _ = locations
    (app / 'data').mkdir()
    (app / 'data/manual_server.log').write_text('old startup')
    (app / 'data/manual_server_status.json').write_text('{}')
    assert app_paths.resolve_data_dir() == profile / 'archives'


@pytest.mark.parametrize('platform,old_name', [('win32', 'ZEVENT Archivage'),
                                               ('darwin', 'ZEVENT Archivage'),
                                               ('linux', 'zevent-archivage')])
def test_legacy_profile_preserved(monkeypatch, locations, platform, old_name):
    _, profile, _ = locations
    monkeypatch.setattr(sys, 'platform', platform)
    old = archive(profile.parent / old_name / 'data')
    assert app_paths.resolve_data_dir() == old
    assert not profile.exists()


def test_explicit_configuration_wins_over_legacy_archive(locations, tmp_path):
    app, _, _ = locations
    archive(app / 'data')
    chosen = tmp_path / 'selected'
    assert app_paths.resolve_data_dir({'data_dir': str(chosen)}) == chosen
    assert not chosen.exists()
    assert app_paths.resolve_data_dir({'data_dir': 'custom', '_root': app}) == app / 'custom'


def test_status_and_server_use_same_existing_configuration(locations, monkeypatch, tmp_path):
    app, _, _ = locations
    chosen = archive(tmp_path / 'archives ailleurs')
    (app / 'config.yaml').write_text('data_dir: ' + json.dumps(str(chosen)), encoding='utf-8')
    (chosen / 'manual_server_status.json').write_text('{"pid":42,"platform":"posix"}')
    monkeypatch.setattr(server_control, 'is_running', lambda: False)
    assert app_paths.resolve_data_dir() == chosen
    assert server_control.status()['pid'] == 42


def test_saved_preference_wins_and_atomic_failure_keeps_it(locations, monkeypatch, tmp_path):
    _, profile, _ = locations
    first = archive(tmp_path / 'first')
    second = archive(tmp_path / 'second')
    assert app_paths.set_data_dir(first) == first
    assert app_paths.resolve_data_dir({'data_dir': str(second)}) == first
    def fail_replace(*args):
        raise OSError('simulated replacement failure')
    monkeypatch.setattr(app_paths.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        app_paths.set_data_dir(second)
    assert app_paths.resolve_data_dir() == first
    assert [p.name for p in profile.iterdir()] == ['storage.json']


def test_missing_saved_destination_never_falls_back(locations, tmp_path):
    app, profile, _ = locations
    archive(app / 'data')
    missing = tmp_path / 'disconnected-drive'
    profile.mkdir(parents=True)
    app_paths.storage_settings_path().write_text(json.dumps({'data_dir': str(missing)}))
    with pytest.raises(app_paths.StoragePathError, match='Reconnect the drive'):
        app_paths.resolve_data_dir()
    assert not missing.exists()
    assert not (profile / 'archives').exists()


@pytest.mark.parametrize('contents', ['not json', '{}', '{"data_dir":"relative"}', '[]'])
def test_bad_preference_blocks_instead_of_falling_back(locations, contents):
    _, profile, _ = locations
    profile.mkdir(parents=True)
    app_paths.storage_settings_path().write_text(contents)
    with pytest.raises(app_paths.StoragePathError, match='Cannot read'):
        app_paths.resolve_data_dir()


def test_set_path_requires_existing_directory(locations, tmp_path):
    _, profile, _ = locations
    with pytest.raises(app_paths.StoragePathError):
        app_paths.set_data_dir(tmp_path / 'missing')
    assert not profile.exists()


def test_config_seeds_user_profile_and_preserves_existing_configs(locations):
    app, profile, assets = locations
    (assets / 'defaults').mkdir()
    (assets / 'defaults' / 'config.yaml').write_text('data_dir: data\n')
    assert app_paths.ensure_config() == profile / 'config.yaml'
    assert not (app / 'config.yaml').exists()
    (profile / 'config.yaml').write_text('data_dir: custom\n')
    app_paths.ensure_config()
    assert (profile / 'config.yaml').read_text() == 'data_dir: custom\n'
    (app / 'config.yaml').write_text('data_dir: previous\n')
    assert app_paths.ensure_config() == app / 'config.yaml'


def test_server_control_import_does_not_resolve_or_write(monkeypatch, locations):
    _, profile, _ = locations
    monkeypatch.setattr(app_paths, 'resolve_data_dir', lambda *a: pytest.fail('resolution at import'))
    spec = importlib.util.spec_from_file_location('twitter_x_archiver._storage_control_test', ROOT / 'src/twitter_x_archiver/server_control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not profile.exists()


def test_shutdown_uses_current_saved_directory(monkeypatch, locations, tmp_path):
    chosen = archive(tmp_path / 'chosen')
    app_paths.set_data_dir(chosen)
    (chosen / 'secrets_bridge.json').write_text('{"token":"test-local-token"}')
    requests = []
    class Response:
        status = 202
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def fake_open(request, **kwargs):
        requests.append(request)
        return Response()
    monkeypatch.setattr(server_control.urllib.request, 'urlopen', fake_open)
    assert server_control._ask_shutdown() is True
    assert requests[0].headers['Authorization'] == 'Bearer test-local-token'


def test_start_log_uses_current_saved_directory(monkeypatch, locations, tmp_path):
    chosen = archive(tmp_path / 'chosen')
    app_paths.set_data_dir(chosen)
    monkeypatch.setattr(server_control, 'is_running', lambda: False)
    monkeypatch.setattr(server_control, '_spawn', lambda stream: stream.write(b'test startup'))
    result = server_control.start(wait=0)
    assert result['error'] == 'timeout'
    assert (chosen / 'manual_server.log').read_bytes() == b'test startup'
