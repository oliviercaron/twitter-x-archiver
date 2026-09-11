"""Ou trouver les fichiers, selon qu'on tourne depuis les sources ou un executable.

Un binaire empaquete deplie ses ressources dans un dossier temporaire : le code
lu par `__file__` n'est plus au meme endroit que les donnees de l'utilisateur.
Les deux notions doivent donc etre distinguees.

- `assets()` : ce qui est embarque et ne change jamais (interface locale,
  configuration par defaut) ;
- `home()`   : le dossier du programme et de ses lanceurs ;
- `user_data()` : le profil stable de l'utilisateur ;
- `resolve_data_dir()` : les archives, en conservant les installations
  anterieures et le choix explicite d'un dossier.

Depuis les sources, home et assets se confondent avec le dossier du projet.
La resolution ne teste pas l'ecriture et ne cree aucun dossier.
"""
import os
import json
import shutil
import sys
import tempfile
from pathlib import Path

FROZEN = bool(getattr(sys, 'frozen', False))


def assets():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def home():
    return Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent


def writable(folder):
    """Un dossier accepte-t-il vraiment une ecriture ?

    Les droits declares mentent souvent sous Windows, notamment sous Program
    Files, ou la virtualisation laisse croire a une reussite. Ecrire un fichier
    pour de vrai est la seule verification qui tienne.
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / '.essai-ecriture'
        probe.write_text('', encoding='utf-8')
        probe.unlink()
        return True
    except OSError:
        return False


def user_data():
    """Emplacement stable du profil, independant du dossier du programme."""
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        return base / 'Archivage X'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Archivage X'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'archivage-x'


class StoragePathError(ValueError):
    """Le dossier choisi doit etre retrouve avant de rouvrir une archive."""


def storage_settings_path():
    return user_data() / 'storage.json'


def _configured_data(config=None):
    if config is None:
        # Lecture uniquement : un simple ping de l'hote ne doit pas creer de
        # profil ni copier la configuration dans le dossier de l'application.
        target = home() / 'config.yaml'
        if not target.is_file():
            target = user_data() / 'config.yaml'
        if not target.is_file():
            return None
        import yaml
        config = yaml.safe_load(target.read_text(encoding='utf-8')) or {}
        config['_root'] = target.parent
    value = config.get('data_dir')
    if not value:
        return None
    directory = Path(value).expanduser()
    if directory.is_absolute():
        return directory.resolve()
    directory = (Path(config.get('_root', home())) / directory).resolve()
    # Le 'data' fourni avec le programme est un ancien defaut, pas un choix
    # explicite de l'utilisateur. Les autres chemins relatifs sont conserves.
    if str(value).replace('\\', '/').rstrip('/') not in ('data', './data'):
        return directory
    return directory if _has_archive(directory) else None


def _has_archive(folder):
    if not folder.is_dir():
        return False
    disposable = {'manual_server_status.json', 'manual_server.log', 'collector.lock',
                  '.DS_Store', '.essai-ecriture'}
    for path in folder.rglob('*'):
        if path.is_file() and path.name not in disposable and not path.name.endswith(('.lock', '.log', '.tmp')):
            return True
    return False


def resolve_data_dir(config=None):
    """Dossier absolu des archives ; cette resolution n'effectue aucune ecriture."""
    settings = storage_settings_path()
    if settings.exists():
        try:
            saved = json.loads(settings.read_text(encoding='utf-8'))
            value = saved['data_dir']
            if not isinstance(value, str) or not value:
                raise ValueError('data_dir manquant')
            directory = Path(value).expanduser()
            if not directory.is_absolute():
                raise ValueError('chemin relatif')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StoragePathError(f'Le réglage du dossier des archives est illisible : {settings}. '
                                   'Rétablissez ce réglage avant de démarrer.') from exc
        if not directory.is_dir():
            raise StoragePathError(f'Le dossier des archives est introuvable : {directory}. '
                                   'Reconnectez le disque ou rétablissez ce dossier avant de démarrer.')
        return directory.resolve()
    configured = _configured_data(config)
    if configured is not None:
        return configured
    legacy_name = 'ZEVENT Archivage' if sys.platform in ('win32', 'darwin') else 'zevent-archivage'
    for directory in (home() / 'data', user_data().parent / legacy_name / 'data'):
        if _has_archive(directory):
            return directory.resolve()
    return (user_data() / 'archives').resolve()


def set_data_dir(path):
    """Enregistrer atomiquement un dossier deja cree et valide par l'appelant."""
    directory = Path(path).expanduser().resolve()
    if not directory.is_dir():
        raise StoragePathError(f'Le dossier des archives doit exister : {directory}')
    target = storage_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.storage-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'version': 1, 'data_dir': str(directory)}, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return directory


def data_home():
    """Ancien point d'entree : parent du dossier, sans tester ni creer de fichier.

    Le nom final peut etre 'archives', 'data' ou un choix utilisateur ; les
    nouveaux appelants doivent employer resolve_data_dir() directement.
    """
    return resolve_data_dir().parent


def ensure_config(name='config.yaml'):
    """Conserver la configuration existante ou en creer une dans le profil."""
    target = home() / name
    if target.is_file():
        return target
    target = user_data() / name
    if not target.exists():
        default = assets() / name
        if default.is_file() and default != target:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(default, target)
    return target
