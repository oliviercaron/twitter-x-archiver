"""Demarrer, arreter et interroger le serveur d'archivage, sur les trois systemes.

Source unique pour l'hote natif, les outils de maintenance et les lanceurs.

Le serveur tourne avec l'environnement du projet quand il existe, sinon avec le
Python courant. Sous Windows, WSL reste un repli pour les installations faites
de ce cote-la. Les deux modes ecrivent le meme `manual_server_status.json`, qui
porte sa plateforme : un PID seul ne dit pas dans quel monde chercher le
processus.

L'etat fait foi par HTTP, pas par le fichier. Un statut peut survivre a un
arret brutal, une reponse sur /health non.
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import app_paths

# Empaquete, le code vit dans un dossier temporaire. Les lanceurs vivent
# avec l'executable et les donnees dans le dossier resolu par app_paths.
ROOT = app_paths.home()
# Resoudre le dossier a chaque appel : le choix peut changer apres une migration.
# Ne pas lire de preferences ni creer de dossier pendant l'import de l'hote.
URL = 'http://127.0.0.1:18765'
HEALTH = URL + '/health'
START_TIMEOUT = 40.0
WINDOWS = os.name == 'nt'

VENV_PYTHON = ROOT / ('.venv/Scripts/python.exe' if WINDOWS else '.venv/bin/python')
# pythonw n'a pas de console du tout : rien ne clignote au demarrage.
VENV_PYTHONW = ROOT / '.venv/Scripts/pythonw.exe'
WSL_LAUNCHER = ROOT / 'run_manual.sh'


def wsl_path(path):
    text = str(path).replace('\\', '/')
    return '/mnt/' + text[0].lower() + text[2:] if len(text) > 1 and text[1] == ':' else text


def command():
    """Comment lancer le serveur, du plus autonome au plus dependant.

    Un `dist/` present dans l'arborescence des sources n'est jamais choisi : cet
    executable pourrait embarquer une ancienne version du code ; le lanceur
    utilise toujours les sources de son installation.
    """
    if getattr(sys, 'frozen', False):
        return [sys.executable]                    # deja empaquete : se relancer
    if WINDOWS and VENV_PYTHONW.exists():
        return [str(VENV_PYTHONW), str(ROOT / 'archive_server.py')]
    if VENV_PYTHON.exists():
        return [str(VENV_PYTHON), str(ROOT / 'archive_server.py')]
    if WINDOWS and WSL_LAUNCHER.exists() and not VENV_PYTHON.exists():
        return ['wsl.exe', 'bash', wsl_path(WSL_LAUNCHER)]
    return [sys.executable, str(ROOT / 'archive_server.py')]


def is_running(timeout=2):
    """/health ne demande aucune authentification et ne revele rien."""
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as r:
            return r.status == 200 and json.loads(r.read()).get('service') == 'zevent-manual'
    except (urllib.error.URLError, OSError, ValueError):
        return False


def status(data=None):
    path = (Path(data) if data is not None else app_paths.resolve_data_dir()) / 'manual_server_status.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    return {'running': is_running(), 'url': URL, 'pid': state.get('pid'),
            'platform': state.get('platform'), 'started_at': state.get('started_at')}


def in_wsl(pid):
    if not WINDOWS:
        return False
    return subprocess.run(['wsl.exe', '-e', 'bash', '-lc', f'ps -p {pid} >/dev/null'],
                          capture_output=True).returncode == 0


def in_windows(pid):
    if not WINDOWS:
        return False
    out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                         capture_output=True, text=True, errors='replace')
    return str(pid) in (out.stdout or '')


def in_posix(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                    # existe, mais appartient a quelqu'un d'autre
    except OSError:
        return False


CHECKS = {'windows': in_windows, 'wsl': in_wsl, 'posix': in_posix}


def worlds(platform):
    """Ou chercher le processus. Un etat ancien ne porte pas sa plateforme,
    et dans le doute il vaut mieux regarder partout que conclure a tort."""
    if platform in CHECKS:
        return (platform,)
    return ('windows', 'wsl') if WINDOWS else ('posix',)


def alive(pid, platform=None):
    if not pid:
        return False
    return any(CHECKS[w](pid) for w in worlds(platform))


def _spawn(sink):
    """Detache le serveur : il doit survivre au navigateur et a la console."""
    shared = dict(close_fds=True, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                  stdout=sink, stderr=sink,
                  env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
    if not WINDOWS:
        subprocess.Popen(command(), start_new_session=True, **shared)
        return
    # CREATE_NO_WINDOW et non DETACHED_PROCESS : ce dernier prive le processus
    # de la console du parent, et Windows lui en ouvre alors une neuve, vide.
    # Les deux drapeaux s'excluent, il faut choisir celui qui n'affiche rien.
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    breakaway = getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0)
    last = None
    # Un job qui interdit la sortie fait echouer la creation, alors on retente sans.
    for extra in ((breakaway, 0) if breakaway else (0,)):
        try:
            subprocess.Popen(command(), creationflags=flags | extra, **shared)
            return
        except OSError as exc:
            last = exc
    raise last


def start(wait=START_TIMEOUT):
    if is_running():
        return {'ok': True, 'already': True, **status()}
    log = app_paths.resolve_data_dir() / 'manual_server.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log.open('ab') as sink:
            _spawn(sink)
    except OSError as exc:
        return {'ok': False, 'error': type(exc).__name__, **status()}
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if is_running():
            return {'ok': True, 'already': False, **status()}
        time.sleep(0.5)
    # Refus possible : verrou collector.lock tenu par un autre collecteur.
    return {'ok': False, 'error': 'timeout', **status()}


def _ask_shutdown(data=None):
    """Demande au serveur de s'arreter lui-meme.

    Le tuer sauterait la fermeture de la base et l'ecriture du statut. Et sans
    console, un `taskkill` sans /F n'atteint personne : le processus n'a aucune
    fenetre pour recevoir la demande de fermeture.
    """
    root = Path(data) if data is not None else app_paths.resolve_data_dir()
    try:
        token = json.loads((root / 'secrets_bridge.json').read_text(encoding='utf-8'))['token']
    except (OSError, ValueError, KeyError):
        return False
    request = urllib.request.Request(URL + '/api/shutdown', data=b'{}', method='POST',
                                     headers={'Authorization': 'Bearer ' + token,
                                              'Content-Type': 'application/json',
                                              'Host': '127.0.0.1:18765'})
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status in (200, 202)
    except (urllib.error.URLError, OSError):
        return False


def _terminate(pid, world):
    if world == 'wsl':
        subprocess.run(['wsl.exe', '-e', 'bash', '-lc', f'kill -TERM {pid}'], capture_output=True)
    elif world == 'windows':
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _gone(pid, platform, deadline):
    while time.monotonic() < deadline:
        if not is_running() and not alive(pid, platform):
            return True
        time.sleep(0.5)
    return False


def stop(timeout=25.0):
    state = status()
    pid, platform = state.get('pid'), state.get('platform')
    if not is_running() and not alive(pid, platform):
        return True
    if _ask_shutdown() and _gone(pid, platform, time.monotonic() + timeout * 0.6):
        return True
    # Le serveur ne repond plus, ou refuse : il reste la maniere forte.
    for world in (worlds(platform) if pid else ()):
        _terminate(pid, world)
    return _gone(pid, platform, time.monotonic() + timeout * 0.4)
