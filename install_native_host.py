#!/usr/bin/env python3
"""Declarer l'hote natif pour Chromium et Firefox, dans le compte courant.

A lancer une seule fois. Ensuite l'extension demarre le serveur d'archivage
elle-meme, au premier clic, sans que vous ayez rien a lancer.

La cle publique Chromium est conservee (ou creee si absente) pour figer son
identifiant. Firefox emploie un identifiant explicite dans son manifeste.
Chaque famille a son manifeste natif, restreint a sa seule extension :
allowed_origins pour Chromium, allowed_extensions pour Firefox.

La declaration emploie le registre HKCU sous Windows et les emplacements
personnels documentes sous macOS/Linux. Aucun droit administrateur n'est
requis. --uninstall retire les declarations, sans effacer les archives ni
les fichiers du programme. Safari utilise son application native dediee.
"""
import argparse
import base64
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path

import app_paths

ROOT = app_paths.home()
EXTENSION = ROOT / 'chrome-extension'
HOST_NAME = 'com.zevent.archive'
FIREFOX_EXTENSION_ID = 'archive-x@local.extension'
# Empaquete, tout vit a cote de l'executable ; en sources, dans son dossier.
HOST_DIR = ROOT if app_paths.FROZEN else ROOT / 'native_host'
KEY_FILE = ROOT / 'data' / 'extension_key.pem'
# Chaque navigateur base sur Chromium lit son propre emplacement, et cet
# emplacement change selon le systeme : le registre sous Windows, un fichier
# depose dans le profil sous macOS et Linux.
REGISTRY_ROOTS = [r'Software\Google\Chrome\NativeMessagingHosts',
                  r'Software\Microsoft\Edge\NativeMessagingHosts',
                  r'Software\BraveSoftware\Brave-Browser\NativeMessagingHosts',
                  r'Software\Chromium\NativeMessagingHosts']
FIREFOX_REGISTRY_ROOTS = [r'Software\Mozilla\NativeMessagingHosts']

MACOS_DIRS = ['Google/Chrome', 'Google/Chrome Beta', 'Chromium',
              'Microsoft Edge', 'BraveSoftware/Brave-Browser']
LINUX_DIRS = ['google-chrome', 'google-chrome-beta', 'chromium',
              'microsoft-edge', 'BraveSoftware/Brave-Browser']


def host_directories(browser='chromium'):
    '''Emplacements utilisateur documentes ; Firefox ignore XDG_CONFIG_HOME.'''
    if browser not in ('chromium', 'firefox'):
        raise ValueError(f'Navigateur inconnu : {browser}')
    home = Path.home()
    if browser == 'firefox':
        if sys.platform == 'darwin':
            return [home / 'Library/Application Support/Mozilla/NativeMessagingHosts']
        return [home / '.mozilla/native-messaging-hosts']
    if sys.platform == 'darwin':
        base = home / 'Library' / 'Application Support'
        return [base / d / 'NativeMessagingHosts' for d in MACOS_DIRS]
    base = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    return [base / d / 'NativeMessagingHosts' for d in LINUX_DIRS]


def public_key_der(private_pem=None):
    """Cle publique DER ; la privee n'est utile que pour empaqueter un .crx."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    if private_pem and private_pem.exists():
        key = serialization.load_pem_private_key(private_pem.read_bytes(), password=None)
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if private_pem:
            private_pem.parent.mkdir(parents=True, exist_ok=True)
            private_pem.write_bytes(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()))
            try:
                private_pem.chmod(0o600)
            except OSError:
                pass
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)


def extension_id(der):
    """Identifiant Chrome : SHA-256 de la cle, 16 premiers octets, chiffres mappes a-p."""
    digest = hashlib.sha256(der).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(c, 16)) for c in digest)


def ensure_manifest_key():
    path = EXTENSION / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if 'key' in manifest:
        der = base64.b64decode(manifest['key'])
        return manifest, extension_id(der), False
    der = public_key_der(KEY_FILE)
    # `key` avant `permissions` : l'ordre reste lisible pour un humain.
    ordered = {}
    for name, value in manifest.items():
        ordered[name] = value
        if name == 'description':
            ordered['key'] = base64.b64encode(der).decode('ascii')
    if 'key' not in ordered:
        ordered['key'] = base64.b64encode(der).decode('ascii')
    if 'nativeMessaging' not in ordered.get('permissions', []):
        ordered['permissions'] = list(ordered.get('permissions', [])) + ['nativeMessaging']
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return ordered, extension_id(der), True


def host_command():
    """Ce que le lanceur doit executer pour parler a Chrome.

    Empaquete, l'executable se rappelle lui-meme en mode hote : il n'y a plus
    de Python sur la machine. Depuis les sources, il appelle le script.
    """
    if app_paths.FROZEN:
        return f'"{sys.executable}" --native-host', f'{shlex.quote(sys.executable)} --native-host'
    return (f'"{sys.executable}" -u "%~dp0host.py"',
            f'{shlex.quote(sys.executable)} -u {shlex.quote(str(HOST_DIR / "host.py"))}')


def write_launcher():
    """Lanceur stdio, accepte par Chromium et Firefox (y compris .bat)."""
    HOST_DIR.mkdir(parents=True, exist_ok=True)
    windows, posix = host_command()
    if os.name == 'nt':
        launcher = HOST_DIR / 'zevent_host.bat'
        launcher.write_text(f'@echo off\r\n{windows} %*\r\n', encoding='utf-8')
        return launcher
    launcher = HOST_DIR / 'zevent_host.sh'
    launcher.write_text(f'#!/bin/sh\nexec {posix} "$@"\n', encoding='utf-8')
    launcher.chmod(0o755)
    return launcher


def write_host_manifest(ident=None, browser='chromium'):
    if browser not in ('chromium', 'firefox'):
        raise ValueError(f'Navigateur inconnu : {browser}')
    if browser == 'chromium' and not ident:
        raise ValueError('Identifiant Chromium manquant')
    launcher = write_launcher()
    # Meme nom d'hote, fichiers distincts : les listes d'autorisations des
    # deux familles ne sont pas interchangeables.
    folder = HOST_DIR / 'firefox' if browser == 'firefox' else HOST_DIR
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / f'{HOST_NAME}.json'
    content = {
        'name': HOST_NAME,
        'description': "Demarre Archivage X a la demande",
        'path': str(launcher),
        'type': 'stdio',
    }
    if browser == 'firefox':
        content['allowed_extensions'] = [FIREFOX_EXTENSION_ID]
    else:
        content['allowed_origins'] = [f'chrome-extension://{ident}/']
    manifest.write_text(json.dumps(content, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def declare(manifest_path=None, browser='chromium'):
    """Windows passe par le registre, macOS et Linux par un fichier depose."""
    if os.name == 'nt':
        return registry(manifest_path, browser)
    touched = []
    content = Path(manifest_path).read_text(encoding='utf-8') if manifest_path else None
    for folder in host_directories(browser):
        target = folder / f'{HOST_NAME}.json'
        if content is None:
            if target.exists():
                target.unlink()
                touched.append(str(target))
            continue
        # Preparer aussi une installation neuve, avant le premier lancement
        # du navigateur. Aucun fichier de preferences n'est cree ou modifie.
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        touched.append(str(target))
    return touched


def registry(manifest_path=None, browser='chromium'):
    if browser not in ('chromium', 'firefox'):
        raise ValueError(f'Navigateur inconnu : {browser}')
    import winreg
    touched = []
    roots = FIREFOX_REGISTRY_ROOTS if browser == 'firefox' else REGISTRY_ROOTS
    for branch in roots:
        key_path = branch + '\\' + HOST_NAME
        try:
            if manifest_path is None:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            else:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(manifest_path))
            touched.append('HKCU\\' + key_path)
        except FileNotFoundError:
            pass                                # branche absente : navigateur non installe
        except OSError as exc:
            print(f'  {key_path} : {type(exc).__name__}')
    return touched


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--uninstall', action='store_true', help='retirer la declaration')
    ap.add_argument('--browser', choices=('all', 'chromium', 'firefox'), default='all',
                    help='famille a declarer (les deux par defaut)')
    args = ap.parse_args(argv)
    browsers = ('chromium', 'firefox') if args.browser == 'all' else (args.browser,)

    if args.uninstall:
        removed = [path for browser in browsers for path in declare(None, browser)]
        print('Cles retirees :', ', '.join(removed) or 'aucune')
        print("L'extension continue de fonctionner ; elle ne pourra plus demarrer le serveur.")
        return 0

    changed = False
    for browser in browsers:
        if browser == 'chromium':
            _, ident, changed = ensure_manifest_key()
        else:
            ident = FIREFOX_EXTENSION_ID
        host_manifest = write_host_manifest(ident, browser)
        keys = declare(host_manifest, browser)
        print(f'  navigateur            : {browser}')
        print(f'  identifiant extension : {ident}')
        print(f'  manifeste hote        : {host_manifest}')
        for k in keys:
            print(f'  declare               : {k}')
    print()
    if changed:
        print("La cle publique vient d'etre ajoutee au manifeste de l'extension.")
        print("Son identifiant change donc une fois : rechargez l'extension dans")
        print("chrome://extensions, puis reconnectez-la depuis la page locale.")
    elif 'chromium' in browsers:
        print("Le manifeste portait deja sa cle : l'identifiant est inchange.")
    print("Rechargez l'extension dans le navigateur concerne.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
