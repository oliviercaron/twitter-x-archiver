"""Verified archive copy; the caller must quiesce all writers before perform().

The original archive is deliberately retained. Only the preference is switched,
and only after every source byte, copied file, and SQLite database is verified.
"""
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import threading
import tempfile
from pathlib import Path


RUNTIME_FILES = frozenset({'collector.lock', 'manual_server_status.json', 'manual_server.log'})
CHUNK = 1024 * 1024


def _absolute(value):
    text = str(value)
    if os.name != 'nt' and re.match(r'^[A-Za-z]:[\\/]', text):
        mount = Path('/mnt') / text[0].lower()
        if not mount.is_dir():
            raise ValueError('Lecteur Windows inaccessible.')
        text = str(mount / text[3:].replace('\\', '/'))
    path = Path(text)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Choisissez un chemin absolu sans segment « .. ».')
    _no_links(path)
    return path.resolve()


def _no_links(path):
    for current in (path, *path.parents):
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('Les liens symboliques et jonctions ne sont pas acceptés.')


def _signature(info):
    # Python on Windows reports ctime differently through stat and fstat.
    # Identity, size, modification time and final SHA256 checks remain mandatory.
    return (info.st_size, info.st_mtime_ns, info.st_ctime_ns if os.name != 'nt' else 0,
            info.st_ino, info.st_dev, info.st_mode)


def _scan(root):
    _no_links(root)
    if not root.is_dir():
        raise ValueError('Le dossier actuel est inaccessible.')
    files, directories, excluded = {}, [], []

    def walk(folder):
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                # Windows DirEntry.stat may omit file identity fields that fstat returns.
                # Use a fresh path stat so the comparison uses the same information.
                info = path.stat(follow_symlinks=False)
                _no_links(path)
                if stat.S_ISDIR(info.st_mode):
                    directories.append(relative)
                    walk(path)
                elif stat.S_ISREG(info.st_mode):
                    if relative in RUNTIME_FILES:
                        excluded.append(relative)
                    else:
                        files[relative] = _signature(info)
                else:
                    raise ValueError('Le dossier contient un fichier spécial non transférable.')

    walk(root)
    return files, directories, excluded


def _digest(path):
    _no_links(path)
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('Le fichier a changé pendant la vérification.')
        for chunk in iter(lambda: source.read(CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


class StorageManager:
    def __init__(self, source, preference_writer=None):
        self.source = _absolute(source)
        self.preference_writer = preference_writer
        self.lock = threading.Lock()
        self._state = {'status': 'idle', 'path': str(self.source), 'target': '',
                       'files_total': 0, 'files_done': 0, 'bytes_total': 0,
                       'bytes_done': 0, 'source_retained': True,
                       'excluded_files': [], 'message': ''}

    def view(self):
        with self.lock:
            return {**self._state, 'excluded_files': list(self._state['excluded_files'])}

    def _update(self, **values):
        with self.lock:
            self._state.update(values)

    def activate(self, destination):
        """Called by the parent after reopening the verified archive successfully."""
        target = _absolute(destination)
        with self.lock:
            if self._state['status'] != 'done' or target != Path(self._state['target']):
                raise ValueError('Le nouveau dossier n’a pas été vérifié.')
            self.source = target
            self._state['path'] = str(target)

    def prepare(self, destination):
        """Read-only validation; perform rescans once the caller has stopped writers."""
        target = _absolute(destination)
        if target == self.source or target in self.source.parents or self.source in target.parents:
            raise ValueError('Choisissez un dossier distinct, hors du dossier actuel et de ses parents.')
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError('Le dossier de destination doit être vide.')
        files, directories, excluded = _scan(self.source)
        ancestor = target
        while not ancestor.exists():
            ancestor = ancestor.parent
        if not ancestor.is_dir():
            raise ValueError('Le dossier de destination est inaccessible.')
        total = sum(info[0] for info in files.values())
        database_scratch = max((sum(files.get(name + suffix, (0,))[0]
                                   for suffix in ('', '-wal', '-shm'))
                                for name in files if name.lower().endswith('.db')), default=0)
        if shutil.disk_usage(ancestor).free < total + database_scratch + max(16 * CHUNK, total // 50):
            raise ValueError('Espace disque insuffisant pour copier et vérifier l’archive.')
        return {'target': str(target), 'files_total': len(files), 'bytes_total': total,
                'excluded_files': excluded}

    def request(self, destination):
        with self.lock:
            if self._state['status'] in {'pending', 'copying', 'verifying'}:
                raise ValueError('Un changement de dossier est déjà en cours ou terminé.')
            if self._state['status'] == 'done' and self.source != Path(self._state['target']):
                raise ValueError('Le changement de dossier doit être activé avant un autre transfert.')
            plan = self.prepare(destination)
            self._state.update(plan, status='pending', files_done=0, bytes_done=0,
                               message='En attente de la fin des opérations en cours.')
            self._state.pop('error_class', None)
            return dict(self._state)

    def _copy_file(self, source, target, expected):
        """Exclusive creation avoids overwriting even if another process adds a file."""
        _no_links(source)
        _no_links(target)
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(source, flags), 'rb') as reader:
            if _signature(os.fstat(reader.fileno())) != expected:
                raise ValueError('L’archive source a changé.')
            with target.open('xb') as writer:
                os.chmod(target, stat.S_IMODE(expected[-1]) & 0o600)
                for chunk in iter(lambda: reader.read(CHUNK), b''):
                    writer.write(chunk)
                    with self.lock:
                        self._state['bytes_done'] += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if _signature(os.fstat(reader.fileno())) != expected:
                raise ValueError('L’archive source a changé pendant la copie.')

    @staticmethod
    def _verify_database(path):
        # SQLite can rewrite its shared-memory sidecar even for read-only clients.
        # Check an isolated duplicate so every delivered file remains byte-identical.
        scratch = Path(tempfile.mkdtemp(prefix='.storage-verify-', dir=path.parent))
        try:
            for suffix in ('', '-wal', '-shm'):
                candidate = Path(str(path) + suffix)
                if candidate.exists():
                    _no_links(candidate)
                    shutil.copyfile(candidate, scratch / ('archive.db' + suffix))
                    os.chmod(scratch / ('archive.db' + suffix), 0o600)
            db = sqlite3.connect((scratch / 'archive.db').as_uri() + '?mode=ro', uri=True)
            try:
                if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Une base de données copiée est invalide.')
            finally:
                db.close()
        finally:
            # This exclusively created directory contains only our SQLite scratch
            # files. Remove individual files, never recursively touch archive data.
            for item in scratch.iterdir():
                if item.is_file() or item.is_symlink():
                    item.unlink()
            scratch.rmdir()

    def perform(self):
        """Synchronous; caller holds the collection lock and has stopped every writer."""
        with self.lock:
            if self._state['status'] != 'pending':
                raise ValueError('Aucun changement de dossier en attente.')
            target = Path(self._state['target'])
            self._state.update(status='copying', message='Copie de l’archive…')
        try:
            plan = self.prepare(target)
            files, directories, excluded = _scan(self.source)
            self._update(**plan, files_done=0, bytes_done=0)
            missing = []
            ancestor = target
            while not ancestor.exists():
                missing.append(ancestor)
                ancestor = ancestor.parent
            for folder in reversed(missing):
                _no_links(folder)
                folder.mkdir(mode=0o700)
            _no_links(target)
            os.chmod(target, 0o700)
            if any(target.iterdir()):
                raise ValueError('La destination n’est plus vide.')
            for relative in directories:
                folder = target / relative
                _no_links(folder)
                folder.mkdir(mode=0o700)
            hashes = {}
            for relative, info in files.items():
                source, copied = self.source / relative, target / relative
                self._copy_file(source, copied, info)
                hashes[relative] = _digest(source)
                if _digest(copied) != hashes[relative]:
                    raise ValueError('La vérification de la copie a échoué.')
                with self.lock:
                    self._state['files_done'] += 1
            self._update(status='verifying', message='Vérification des fichiers et des bases…')
            # Byte-copy DB + WAL together from a quiescent source. Read-only SQLite
            # sees any retained WAL, unlike immutable mode which would ignore it.
            for relative in files:
                if relative.lower().endswith('.db'):
                    self._verify_database(target / relative)
            final_files, final_dirs, _ = _scan(self.source)
            if files != final_files or directories != final_dirs:
                raise ValueError('L’archive source a changé pendant le transfert.')
            for relative, digest in hashes.items():
                if _digest(self.source / relative) != digest or _digest(target / relative) != digest:
                    raise ValueError('Un fichier a changé pendant la vérification.')
            # Recheck metadata after hashing to detect writers racing the final pass.
            if _scan(self.source)[:2] != (files, directories):
                raise ValueError('L’archive source a changé pendant la vérification.')
            copied_files, copied_dirs, _ = _scan(target)
            if set(copied_files) != set(files) or copied_dirs != directories:
                raise ValueError('La destination a changé pendant le transfert.')
            writer = self.preference_writer
            if writer is None:
                from .app_paths import set_data_dir
                writer = set_data_dir
            writer(target)
            self._update(status='done', message='Archive copiée et vérifiée. Le dossier original est conservé.',
                         source_retained=True)
        except Exception as exc:
            self._update(status='error', error_class=type(exc).__name__,
                         message='Changement non effectué. Le dossier actuel reste utilisé ; la copie partielle est conservée.')
        return self.view()
