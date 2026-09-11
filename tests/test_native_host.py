"""L'hote natif est teste comme Chrome l'appelle : par le lanceur, en stdio."""
import base64
import json
import os
import shlex
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from install_native_host import extension_id  # noqa: E402
import install_native_host  # noqa: E402,F401

LAUNCHER = ROOT / 'native_host' / 'zevent_host.bat'
HOST = ROOT / 'native_host' / 'host.py'
EXTENSION_MANIFEST = ROOT / 'chrome-extension' / 'manifest.json'
HOST_MANIFEST = ROOT / 'native_host' / 'com.zevent.archive.json'
windows_only = pytest.mark.skipif(sys.platform != 'win32', reason='lanceur .bat propre a Windows')


@pytest.fixture(autouse=True)
def isolated_native_host(monkeypatch, tmp_path):
    """Exercise generated launchers against a harmless local service stub."""
    directory=tmp_path/'native_host'
    directory.mkdir()
    (directory/'host.py').write_text(HOST.read_text(encoding='utf-8'),encoding='utf-8')
    (tmp_path/'server_control.py').write_text(
        "def status():return {'running':False,'url':'http://127.0.0.1:18765'}\n"
        "def start():return {'ok':True,**status()}\n",encoding='utf-8')
    monkeypatch.setattr(install_native_host,'HOST_DIR',directory)
    monkeypatch.setattr(install_native_host.app_paths,'FROZEN',False)
    extension=json.loads(EXTENSION_MANIFEST.read_text(encoding='utf-8'))
    manifest=install_native_host.write_host_manifest(extension_id(base64.b64decode(extension['key'])))
    monkeypatch.setattr(sys.modules[__name__],'HOST_MANIFEST',manifest)
    monkeypatch.setattr(sys.modules[__name__],'LAUNCHER',Path(json.loads(manifest.read_text())['path']))


def talk(messages):
    """Envoie des commandes a l'hote et lit ses reponses."""
    payload = b''
    for m in messages:
        body = json.dumps(m).encode('utf-8')
        payload += struct.pack('<I', len(body)) + body
    out = subprocess.run([str(LAUNCHER)], input=payload, capture_output=True, timeout=120)
    answers, data = [], out.stdout
    while len(data) >= 4:
        length = struct.unpack('<I', data[:4])[0]
        answers.append(json.loads(data[4:4 + length].decode('utf-8')))
        data = data[4 + length:]
    return answers, out


def test_extension_id_follows_chrome_rules():
    key = json.loads(EXTENSION_MANIFEST.read_text(encoding='utf-8'))['key']
    ident = extension_id(base64.b64decode(key))
    assert len(ident) == 32 and set(ident) <= set('abcdefghijklmnop')
    assert extension_id(base64.b64decode(key)) == ident        # deterministe


def test_generated_key_yields_a_usable_id():
    crypto = pytest.importorskip('cryptography')                # absent du runtime de collecte
    del crypto
    from install_native_host import public_key_der
    a, b = public_key_der(), public_key_der()
    assert extension_id(a) != extension_id(b)                   # deux cles, deux identites
    assert set(extension_id(a)) <= set('abcdefghijklmnop')


def test_host_manifest_allows_only_our_extension():
    manifest = json.loads(HOST_MANIFEST.read_text(encoding='utf-8'))
    extension = json.loads(EXTENSION_MANIFEST.read_text(encoding='utf-8'))
    expected = extension_id(base64.b64decode(extension['key']))
    assert manifest['allowed_origins'] == [f'chrome-extension://{expected}/']
    assert manifest['type'] == 'stdio'
    assert manifest['path'].endswith(('zevent_host.bat','zevent_host.sh'))
    assert 'nativeMessaging' in extension['permissions']


@windows_only
def test_declared_launcher_exists():
    manifest = json.loads(HOST_MANIFEST.read_text(encoding='utf-8'))
    assert Path(manifest['path']).exists()


@windows_only
def test_ping_reports_the_server_state():
    answers, _ = talk([{'type': 'ping'}])
    assert len(answers) == 1
    a = answers[0]
    assert a['ok'] is True and a['url'] == 'http://127.0.0.1:18765'
    assert isinstance(a['running'], bool)


@windows_only
def test_unknown_command_is_refused_without_crashing():
    answers, _ = talk([{'type': 'lancer_autre_chose'}, {'type': 'ping'}])
    assert answers[0] == {'ok': False, 'error': 'unknown_command'}
    assert answers[1]['ok'] is True                 # l'hote reste disponible ensuite


@windows_only
def test_malformed_input_closes_cleanly():
    out = subprocess.run([str(LAUNCHER)], input=b'\x05\x00\x00\x00pasJSON',
                         capture_output=True, timeout=60)
    assert out.returncode == 0                      # pas de trace d'exception vers Chrome
    assert b'Traceback' not in out.stderr


@windows_only
def test_oversized_length_is_rejected():
    out = subprocess.run([str(LAUNCHER)], input=struct.pack('<I', 2 ** 31) + b'x',
                         capture_output=True, timeout=60)
    assert out.returncode == 0 and out.stdout == b''


def test_wsl_path_conversion():
    from server_control import wsl_path
    assert wsl_path(r'D:\zevent2026\run_manual.sh') == '/mnt/d/zevent2026/run_manual.sh'
    assert wsl_path('/mnt/d/deja/converti.sh') == '/mnt/d/deja/converti.sh'


def test_macos_directories(monkeypatch, tmp_path):
    import install_native_host as ins
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(ins.Path, 'home', staticmethod(lambda: tmp_path))
    dirs = ins.host_directories()
    assert all(str(d).startswith(str(tmp_path / 'Library' / 'Application Support')) for d in dirs)
    assert any('Google/Chrome/NativeMessagingHosts' in d.as_posix() for d in dirs)


def test_linux_directories_follow_xdg(monkeypatch, tmp_path):
    import install_native_host as ins
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(ins.Path, 'home', staticmethod(lambda: tmp_path))
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    assert any('.config/google-chrome/NativeMessagingHosts' in d.as_posix()
               for d in ins.host_directories())
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'ailleurs'))
    assert all(str(d).startswith(str(tmp_path / 'ailleurs')) for d in ins.host_directories())


def test_every_supported_browser_is_covered(monkeypatch, tmp_path):
    import install_native_host as ins
    monkeypatch.setattr(ins.Path, 'home', staticmethod(lambda: tmp_path))
    for platform in ('darwin', 'linux'):
        monkeypatch.setattr(sys, 'platform', platform)
        names = ' '.join(d.as_posix().lower() for d in ins.host_directories())
        for browser in ('chrome', 'chromium', 'edge', 'brave'):
            assert browser in names, (platform, browser)


@pytest.mark.parametrize('platform,suffix', [
    ('darwin', 'Library/Application Support/Mozilla/NativeMessagingHosts'),
    ('linux', '.mozilla/native-messaging-hosts'),
])
def test_firefox_uses_mozilla_user_directory(monkeypatch, tmp_path, platform, suffix):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(install_native_host.Path, 'home', staticmethod(lambda: tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'other-config'))
    assert install_native_host.host_directories('firefox') == [tmp_path / suffix]


def test_browser_manifests_have_separate_allowlists(monkeypatch, tmp_path):
    ins = install_native_host
    monkeypatch.setattr(ins, 'HOST_DIR', tmp_path)
    chrome_path = ins.write_host_manifest('a' * 32)
    firefox_path = ins.write_host_manifest(browser='firefox')
    chrome = json.loads(chrome_path.read_text(encoding='utf-8'))
    firefox = json.loads(firefox_path.read_text(encoding='utf-8'))
    assert chrome_path != firefox_path
    assert chrome_path.name == firefox_path.name == ins.HOST_NAME + '.json'
    assert chrome['allowed_origins'] == ['chrome-extension://' + 'a' * 32 + '/']
    assert 'allowed_extensions' not in chrome
    assert firefox['allowed_extensions'] == ['archive-x@local.extension']
    assert 'allowed_origins' not in firefox
    assert chrome['path'] == firefox['path']
    assert Path(firefox['path']).is_absolute()


@pytest.mark.skipif(os.name == 'nt', reason='fichiers de declaration POSIX')
def test_firefox_install_before_browser_first_run_and_uninstall(monkeypatch, tmp_path):
    ins = install_native_host
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(ins.Path, 'home', staticmethod(lambda: tmp_path / 'user'))
    monkeypatch.setattr(ins, 'HOST_DIR', tmp_path / 'app')
    source = ins.write_host_manifest(browser='firefox')
    expected = tmp_path / 'user/.mozilla/native-messaging-hosts' / source.name
    assert ins.declare(source, 'firefox') == [str(expected)]
    assert expected.read_bytes() == source.read_bytes()
    other = expected.with_name('other.host.json')
    other.write_text('{}')
    assert ins.declare(None, 'firefox') == [str(expected)]
    assert not expected.exists() and other.exists() and source.exists()
    assert ins.declare(None, 'firefox') == []


def test_firefox_registry_targets_only_current_user_mozilla(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from contextlib import nullcontext
    calls = []
    fake = SimpleNamespace(HKEY_CURRENT_USER='HKCU', REG_SZ=1,
                           CreateKey=lambda hive, path: nullcontext((hive, path)),
                           SetValueEx=lambda *args: calls.append(('set', args)),
                           DeleteKey=lambda *args: calls.append(('delete', args)))
    monkeypatch.setitem(sys.modules, 'winreg', fake)
    manifest = tmp_path / 'com.zevent.archive.json'
    key = r'Software\Mozilla\NativeMessagingHosts\com.zevent.archive'
    assert install_native_host.registry(manifest, 'firefox') == ['HKCU\\' + key]
    assert calls == [('set', (('HKCU', key), None, 0, 1, str(manifest)))]
    install_native_host.registry(None, 'firefox')
    assert calls[-1] == ('delete', ('HKCU', key))


@pytest.mark.skipif(os.name == 'nt', reason='lanceur shell POSIX')
def test_posix_launcher_preserves_paths_and_browser_arguments(monkeypatch, tmp_path):
    ins = install_native_host
    folder = tmp_path / "Archivage été $HOME ' test"
    monkeypatch.setattr(ins, 'HOST_DIR', folder)
    monkeypatch.setattr(ins.app_paths, 'FROZEN', False)
    launcher = ins.write_launcher()
    # Le double remplace seulement l'hote : aucun serveur ni compte n'est touche.
    (folder / 'host.py').write_text(
        'import json, sys, struct\n'
        'data=json.dumps(sys.argv[1:]).encode()\n'
        'sys.stdout.buffer.write(struct.pack("<I",len(data))+data)\n', encoding='utf-8')
    args = ['manifest with spaces.json', 'archive-x@local.extension']
    out = subprocess.run([str(launcher), *args], capture_output=True, timeout=10)
    assert out.returncode == 0, out.stderr
    length = struct.unpack('<I', out.stdout[:4])[0]
    assert json.loads(out.stdout[4:4 + length]) == args
    assert launcher.stat().st_mode & 0o111


def test_frozen_posix_command_keeps_native_host_mode(monkeypatch):
    monkeypatch.setattr(install_native_host.app_paths, 'FROZEN', True)
    executable = "/Applications/Archivage X's $test/archivage-x"
    monkeypatch.setattr(sys, 'executable', executable)
    _, posix = install_native_host.host_command()
    assert shlex.split(posix) == [executable, '--native-host']


def test_firefox_only_install_does_not_change_chrome_manifest(monkeypatch, tmp_path):
    ins = install_native_host
    monkeypatch.setattr(ins, 'HOST_DIR', tmp_path)
    monkeypatch.setattr(ins, 'ensure_manifest_key', lambda: pytest.fail('Chrome modifie'))
    calls = []
    monkeypatch.setattr(ins, 'declare', lambda path, browser: calls.append((path, browser)) or [])
    assert ins.main(['--browser', 'firefox']) == 0
    assert calls[0][1] == 'firefox'
    assert ins.main(['--browser', 'firefox', '--uninstall']) == 0
    assert calls[-1] == (None, 'firefox')
