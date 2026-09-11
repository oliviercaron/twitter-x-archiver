import json
import sqlite3

import pytest

from twitter_x_archiver.remove_post import apply, plan, post_id, verify


def build(tmp_path, rows, jobs=(), extra_files=()):
    """Fabrique un jeu de donnees minimal mais realiste : base, file, fichiers."""
    data = tmp_path / 'data'
    (data / 'media' / 'videos').mkdir(parents=True)
    (data / 'media' / 'images').mkdir(parents=True)
    (data / 'raw').mkdir()
    db = sqlite3.connect(data / 'collection.db')
    db.execute('CREATE TABLE tweets(id TEXT PRIMARY KEY, doc TEXT)')
    db.execute('CREATE TABLE files(key TEXT PRIMARY KEY, doc TEXT)')
    db.execute('CREATE TABLE pages(id TEXT PRIMARY KEY)')
    for row in rows:
        db.execute('INSERT INTO tweets VALUES(?,?)', (row['tweet_id'], json.dumps(row)))
        for item in row.get('media', []):
            d = item.get('download') or {}
            path = d.get('local_media_path')
            if path:
                (data / path).write_bytes(b'contenu')
                (data / (path + '.json')).write_text('{}', encoding='utf-8')
                db.execute('INSERT OR IGNORE INTO files VALUES(?,?)',
                           ('sha256:' + (d.get('sha256') or path), json.dumps({'local_media_path': path})))
        for ref in row.get('raw_response_refs') or []:
            (data / ref).write_bytes(b'\x1f\x8b')
            db.execute('INSERT OR IGNORE INTO pages VALUES(?)', (ref.split('/')[-1].replace('.json.gz', ''),))
        (data / 'posts' / row['tweet_id']).mkdir(parents=True, exist_ok=True)
        (data / 'posts' / row['tweet_id'] / 'tweet.json').write_text(json.dumps(row), encoding='utf-8')
    db.commit(); db.close()
    for name in extra_files:
        (data / name).write_bytes(b'orphelin')
    q = sqlite3.connect(data / 'manual_queue.db')
    q.execute('CREATE TABLE jobs(tweet_id TEXT PRIMARY KEY, status TEXT, doc TEXT)')
    for ident, status in jobs:
        q.execute('INSERT INTO jobs VALUES(?,?,?)', (ident, status, json.dumps({'url': 'u'})))
    q.commit(); q.close()
    return data


def tweet(ident, path=None, sha=None, raw=None, relation='own'):
    row = {'tweet_id': ident, 'author_username': 'a', 'created_at_utc': '2026-09-06T00:00:00+00:00',
           'raw_content': 'texte', 'media': [], 'raw_response_refs': list(raw or [])}
    if path:
        row['media'] = [{'media_relation': relation, 'source_tweet_id': ident,
                         'download': {'local_media_path': path, 'sha256': sha}}]
    return row


@pytest.mark.parametrize('value,expected', [
    ('https://x.com/name/status/123?s=20', '123'),
    ('https://twitter.com/name/status/123/video/1', '123'),
    ('https://x.com/i/web/status/123', '123'),
    ('456', '456')])
def test_accepted_references(value, expected):
    assert post_id(value) == expected


@pytest.mark.parametrize('value', [
    'https://x.com/home', 'https://x.com.evil.test/a/status/123',
    'https://user:pass@x.com/a/status/123', 'https://x.com:9000/a/status/123',
    'https://x.com/a/status/0'])
def test_rejected_references(value):
    with pytest.raises(ValueError):
        post_id(value)


def test_exclusive_post_leaves_nothing(tmp_path):
    data = build(tmp_path,
                 [tweet('111', 'media/videos/111_00.mp4', 'aa', ['raw/p1.json.gz']),
                  tweet('222', 'media/videos/222_00.mp4', 'bb', ['raw/p2.json.gz'])],
                 jobs=[('111', 'done'), ('222', 'done')])
    p = plan(data, '111')
    assert not p['shared_media'] and not p['shared_raw']
    apply(p, data, force_shared=False)
    assert verify(data, '111') == []
    # Le post voisin est intact.
    assert (data / 'media/videos/222_00.mp4').exists()
    assert (data / 'raw/p2.json.gz').exists()
    assert (data / 'posts/222/tweet.json').exists()
    db = sqlite3.connect(data / 'collection.db')
    assert db.execute('SELECT COUNT(*) FROM tweets').fetchone()[0] == 1


def test_media_shared_with_another_post_is_withheld(tmp_path):
    # Cas d'une citation : les deux lignes pointent le meme fichier.
    data = build(tmp_path, [tweet('111', 'media/videos/111_00.mp4', 'aa'),
                            tweet('222', 'media/videos/111_00.mp4', 'aa', relation='quote')])
    p = plan(data, '222')
    assert [m['path'] for m in p['shared_media']] == ['media/videos/111_00.mp4']
    assert p['media'] == []


def test_media_shared_by_hash_under_another_name_is_withheld(tmp_path):
    # Re-upload : chemins differents, octets identiques.
    data = build(tmp_path, [tweet('111', 'media/videos/111_00.mp4', 'aa'),
                            tweet('222', 'media/videos/222_00.mp4', 'aa')])
    assert [m['path'] for m in plan(data, '222')['shared_media']] == ['media/videos/222_00.mp4']


def test_raw_archive_used_by_another_row_is_withheld(tmp_path):
    data = build(tmp_path, [tweet('111', raw=['raw/commun.json.gz']),
                            tweet('222', raw=['raw/commun.json.gz'])])
    p = plan(data, '111')
    assert p['raw'] == []
    assert p['shared_raw'][0]['used_by'] == ['222']


def test_force_shared_removes_them_anyway(tmp_path):
    data = build(tmp_path, [tweet('111', 'media/videos/111_00.mp4', 'aa', ['raw/commun.json.gz']),
                            tweet('222', 'media/videos/111_00.mp4', 'aa', ['raw/commun.json.gz'],
                                  relation='quote')])
    p = plan(data, '222')
    assert p['shared_media'] and p['shared_raw']
    apply(p, data, force_shared=True)
    assert not (data / 'media/videos/111_00.mp4').exists()
    assert not (data / 'raw/commun.json.gz').exists()
    assert verify(data, '222') == []


def test_files_named_after_the_post_but_unreferenced_are_removed(tmp_path):
    # Residu d'un telechargement dont la ligne a perdu son chemin.
    data = build(tmp_path, [tweet('111')],
                 extra_files=['media/images/111_00.jpg', 'media/videos/111_09.mp4'])
    p = plan(data, '111')
    assert p['stray'] == ['media/images/111_00.jpg', 'media/videos/111_09.mp4']
    apply(p, data, force_shared=False)
    assert verify(data, '111') == []


def test_queue_only_post_is_removed_without_touching_the_database(tmp_path):
    data = build(tmp_path, [tweet('222')], jobs=[('111', 'queued'), ('222', 'done')])
    p = plan(data, '111')
    assert p['row'] is None and p['job'] == 'queued'
    apply(p, data, force_shared=False)
    assert verify(data, '111') == []
    q = sqlite3.connect(data / 'manual_queue.db')
    assert [r[0] for r in q.execute('SELECT tweet_id FROM jobs')] == ['222']


def test_verify_reports_a_leftover(tmp_path):
    data = build(tmp_path, [tweet('111')])
    (data / 'media' / 'videos' / '111_00.mp4').write_bytes(b'oublie')
    assert 'media/videos/111_00.mp4' in [x.replace('\\', '/') for x in verify(data, '111')]
