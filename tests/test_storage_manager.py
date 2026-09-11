import json
import os
import sqlite3
from unittest.mock import Mock

import pytest

from twitter_x_archiver import storage_manager
from twitter_x_archiver.storage_manager import StorageManager


@pytest.fixture
def archive(tmp_path):
    source = tmp_path / 'archive'
    (source / 'media').mkdir(parents=True)
    (source / 'raw').mkdir()
    (source / 'media' / 'échantillon.mp4').write_bytes(b'media' * 100)
    (source / 'raw' / 'page.json.gz').write_bytes(b'compressed source fixture')
    (source / 'audit.log').write_text('reproducibility record')
    (source / 'secrets_bridge.json').write_text('fixture token, never printed')
    os.chmod(source / 'secrets_bridge.json', 0o600)
    with sqlite3.connect(source / 'collection.db') as db:
        db.execute('CREATE TABLE tweets(id TEXT, doc TEXT)')
        db.execute('INSERT INTO tweets VALUES (?, ?)', ('1', json.dumps({'text': 'échantillon'})))
    for name in storage_manager.RUNTIME_FILES:
        (source / name).write_text('operational fixture')
    return source


def test_copies_every_archive_file_verifies_database_and_switches_last(archive, tmp_path):
    target = tmp_path / 'new archive'
    def commit(path):
        assert path == target
        assert (path / 'media' / 'échantillon.mp4').read_bytes() == (archive / 'media' / 'échantillon.mp4').read_bytes()
        with sqlite3.connect(path / 'collection.db') as db:
            assert db.execute('SELECT id FROM tweets').fetchone() == ('1',)
        assert manager.view()['status'] == 'verifying'
    writer = Mock(side_effect=commit)
    manager = StorageManager(archive, writer)
    assert manager.prepare(target)['files_total'] == 5
    assert not target.exists()
    assert manager.request(target)['status'] == 'pending'
    result = manager.perform()
    assert result['status'] == 'done' and result['source_retained']
    assert result['files_done'] == result['files_total'] == 5
    assert result['bytes_done'] == result['bytes_total']
    assert set(result['excluded_files']) == storage_manager.RUNTIME_FILES
    assert (target / 'audit.log').read_bytes() == (archive / 'audit.log').read_bytes()
    assert all(not (target / name).exists() for name in storage_manager.RUNTIME_FILES)
    assert (archive / 'collection.db').exists()
    if os.name != 'nt':  # Windows uses inherited ACLs, not POSIX mode bits.
        assert (target / 'secrets_bridge.json').stat().st_mode & 0o077 == 0
    writer.assert_called_once_with(target)


@pytest.mark.parametrize('kind', ['same', 'ancestor', 'nested', 'nonempty', 'file', 'relative'])
def test_refuses_unsafe_destination_without_writing(archive, tmp_path, kind):
    occupied = tmp_path / 'occupied'
    occupied.mkdir()
    (occupied / 'keep').write_text('existing')
    targets = {'same': archive, 'ancestor': tmp_path, 'nested': archive / 'child',
               'nonempty': occupied, 'file': occupied / 'keep', 'relative': 'relative/path'}
    writer = Mock()
    manager = StorageManager(archive, writer)
    with pytest.raises(ValueError): manager.request(targets[kind])
    assert manager.view()['status'] == 'idle'
    writer.assert_not_called()
    assert (occupied / 'keep').read_text() == 'existing'


def test_no_symlinks_source_or_target(archive, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = archive / 'escape'
    link.symlink_to(outside, target_is_directory=True)
    manager = StorageManager(archive, Mock())
    with pytest.raises(ValueError, match='liens'): manager.request(tmp_path / 'new')
    link.unlink()
    destination_link = tmp_path / 'linked'
    destination_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='liens'): manager.request(destination_link / 'child')


def test_disk_space_checked_before_destination_creation(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(storage_manager.shutil, 'disk_usage', lambda path: Mock(free=0))
    target = tmp_path / 'new'
    with pytest.raises(ValueError, match='Espace'): StorageManager(archive, Mock()).request(target)
    assert not target.exists()


def test_perform_rescans_after_pending_work_completes(archive, tmp_path):
    manager = StorageManager(archive, Mock())
    manager.request(tmp_path / 'new')
    (archive / 'completed-later.json').write_text('download finished before pause')
    result = manager.perform()
    assert result['status'] == 'done'
    assert result['files_total'] == 6
    assert (tmp_path / 'new' / 'completed-later.json').exists()


def test_corrupted_copy_never_switches_and_keeps_source_and_partial(archive, tmp_path, monkeypatch):
    writer = Mock()
    manager = StorageManager(archive, writer)
    manager.request(tmp_path / 'new')
    original = manager._copy_file
    def corrupt(source, target, expected):
        original(source, target, expected)
        target.write_bytes(b'corrupted copy')
    monkeypatch.setattr(manager, '_copy_file', corrupt)
    assert manager.perform()['status'] == 'error'
    writer.assert_not_called()
    assert (archive / 'audit.log').read_text() == 'reproducibility record'
    assert (tmp_path / 'new').exists()


def test_source_mutation_during_copy_never_switches(archive, tmp_path, monkeypatch):
    writer = Mock()
    manager = StorageManager(archive, writer)
    manager.request(tmp_path / 'new')
    original = manager._copy_file
    def mutate(source, target, expected):
        original(source, target, expected)
        (archive / 'late-file').write_text('external writer')
    monkeypatch.setattr(manager, '_copy_file', mutate)
    assert manager.perform()['status'] == 'error'
    writer.assert_not_called()


def test_invalid_database_is_rejected_even_when_hashes_match(archive, tmp_path):
    (archive / 'collection.db').write_bytes(b'not sqlite')
    writer = Mock()
    manager = StorageManager(archive, writer)
    manager.request(tmp_path / 'new')
    assert manager.perform()['status'] == 'error'
    writer.assert_not_called()


def test_preference_failure_is_reported_and_original_retained(archive, tmp_path):
    writer = Mock(side_effect=OSError('private path must never be reported'))
    manager = StorageManager(archive, writer)
    manager.request(tmp_path / 'new')
    result = manager.perform()
    assert result['status'] == 'error' and result['source_retained']
    assert 'private path' not in json.dumps(result)
    assert (archive / 'collection.db').exists()


def test_pending_request_and_completed_copy_cannot_be_run_twice(archive, tmp_path):
    writer = Mock()
    manager = StorageManager(archive, writer)
    manager.request(tmp_path / 'new')
    with pytest.raises(ValueError): manager.request(tmp_path / 'other')
    assert manager.perform()['status'] == 'done'
    with pytest.raises(ValueError): manager.perform()
    writer.assert_called_once()


def test_destination_populated_after_request_is_never_overwritten(archive, tmp_path):
    target = tmp_path / 'new'
    manager = StorageManager(archive, Mock())
    manager.request(target)
    target.mkdir()
    (target / 'keep').write_text('keep')
    assert manager.perform()['status'] == 'error'
    assert list(target.iterdir()) == [target / 'keep']


def test_stable_wal_snapshot_preserves_committed_rows(archive, tmp_path):
    # Keep connection open only to preserve an uncheckpointed WAL fixture.
    db = sqlite3.connect(archive / 'collection.db')
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('INSERT INTO tweets VALUES (?, ?)', ('2', '{}'))
        db.commit()
        manager = StorageManager(archive, Mock())
        manager.request(tmp_path / 'new')
        result = manager.perform()
        assert result['status'] == 'done', result
        copied = sqlite3.connect(tmp_path / 'new' / 'collection.db')
        try: assert copied.execute('SELECT COUNT(*) FROM tweets').fetchone() == (2,)
        finally: copied.close()
    finally:
        db.close()


def test_verified_target_can_be_activated_and_moved_again(archive, tmp_path):
    writer = Mock()
    manager = StorageManager(archive, writer)
    target = tmp_path / 'first'
    manager.request(target)
    assert manager.perform()['status'] == 'done'
    with pytest.raises(ValueError): manager.activate(tmp_path / 'wrong')
    with pytest.raises(ValueError): manager.request(tmp_path / 'second')
    manager.activate(target)
    assert manager.view()['path'] == str(target)
    manager.request(tmp_path / 'second')
    assert manager.perform()['status'] == 'done'
    assert writer.call_count == 2
    assert (archive / 'collection.db').exists() and (target / 'collection.db').exists()


def test_running_copy_rejects_reentrant_perform_and_request(archive, tmp_path, monkeypatch):
    manager = StorageManager(archive, Mock())
    manager.request(tmp_path / 'new')
    original = manager._copy_file
    def assert_busy(source, target, expected):
        assert manager.view()['status'] == 'copying'
        with pytest.raises(ValueError): manager.perform()
        with pytest.raises(ValueError): manager.request(tmp_path / 'other')
        original(source, target, expected)
    monkeypatch.setattr(manager, '_copy_file', assert_busy)
    assert manager.perform()['status'] == 'done'


def test_private_new_parent_directories_and_no_verification_artifacts(archive, tmp_path):
    manager = StorageManager(archive, Mock())
    target = tmp_path / 'new parent' / 'new archive'
    manager.request(target)
    assert manager.perform()['status'] == 'done'
    if os.name != 'nt':
        assert target.parent.stat().st_mode & 0o077 == 0
        assert target.stat().st_mode & 0o077 == 0
    assert not list(target.rglob('.storage-verify-*'))


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='FIFO unavailable')
def test_special_files_are_refused_without_opening_them(archive, tmp_path):
    os.mkfifo(archive / 'named-pipe')
    with pytest.raises(ValueError, match='spécial'):
        StorageManager(archive, Mock()).request(tmp_path / 'new')
