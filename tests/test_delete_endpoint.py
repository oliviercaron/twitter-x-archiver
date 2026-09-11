"""Suppression depuis l'interface : ce qui part, ce qui est refuse, ce qui reste."""
import sqlite3
import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from twitter_x_archiver.server import make_handler
from twitter_x_archiver.manual_archive import Jobs
from test_remove_post import build, tweet

TOKEN = 'jeton-de-test-tres-long-0123456789'
HEAD = {'Authorization': f'Bearer {TOKEN}', 'Host': '127.0.0.1:18765'}


def serve(data):
    jobs = Jobs(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(jobs, TOKEN))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'


def prepare(tmp_path, rows, jobs=()):
    data = build(tmp_path, rows, jobs=jobs)
    return data, *serve(data)


def test_deleting_removes_every_trace(tmp_path):
    data, server, base = prepare(
        tmp_path,
        [tweet('111', 'media/videos/111_00.mp4', 'aa', ['raw/p1.json.gz']),
         tweet('222', 'media/videos/222_00.mp4', 'bb', ['raw/p2.json.gz'])],
        jobs=[('111', 'done'), ('222', 'done')])
    try:
        r = httpx.post(base + '/api/delete', json={'tweet_id': '111'}, headers=HEAD)
        assert r.status_code == 200
        assert r.json() == {'deleted': '111', 'left': []}

        assert not (data / 'media/videos/111_00.mp4').exists()
        assert not (data / 'raw/p1.json.gz').exists()
        assert not (data / 'posts/111').exists()
        db = sqlite3.connect(data / 'collection.db')
        assert db.execute('SELECT COUNT(*) FROM tweets WHERE id=?', ('111',)).fetchone()[0] == 0
        db.close()
        q = sqlite3.connect(data / 'manual_queue.db')
        assert [i for i, in q.execute('SELECT tweet_id FROM jobs')] == ['222']
        q.close()

        # Le voisin est intact.
        assert (data / 'media/videos/222_00.mp4').exists()
        assert (data / 'raw/p2.json.gz').exists()
        assert (data / 'posts/222/tweet.json').exists()
    finally:
        server.shutdown()


def test_shared_media_is_refused_not_destroyed(tmp_path):
    data, server, base = prepare(
        tmp_path,
        [tweet('111', 'media/videos/111_00.mp4', 'aa'),
         tweet('222', 'media/videos/111_00.mp4', 'aa', relation='quote')])
    try:
        r = httpx.post(base + '/api/delete', json={'tweet_id': '222'}, headers=HEAD)
        assert r.status_code == 409 and r.json()['error'] == 'shared'
        assert (data / 'media/videos/111_00.mp4').exists()
        db = sqlite3.connect(data / 'collection.db')
        assert db.execute('SELECT COUNT(*) FROM tweets').fetchone()[0] == 2
        db.close()

        # Le refus se leve explicitement, jamais par defaut.
        forced = httpx.post(base + '/api/delete', json={'tweet_id': '222', 'force': True}, headers=HEAD)
        assert forced.status_code == 200
        assert not (data / 'media/videos/111_00.mp4').exists()
    finally:
        server.shutdown()


@pytest.mark.parametrize('ident', ['111', '222'])
def test_preserving_shared_files_deletes_only_the_selected_post(tmp_path, ident):
    rows = [tweet('111', 'media/videos/111_00.mp4', 'shared', ['raw/111_page.json.gz']),
            tweet('222', 'media/videos/111_00.mp4', 'shared', ['raw/111_page.json.gz'], relation='quote')]
    selected = next(row for row in rows if row['tweet_id'] == ident)
    selected['media'] += tweet(ident, f'media/videos/{ident}_own.mp4', 'own')['media']
    data, server, base = prepare(tmp_path, rows, jobs=[('111', 'done'), ('222', 'done')])
    other = '222' if ident == '111' else '111'
    before = (data / f'posts/{other}/tweet.json').read_bytes()
    retained = ['media/videos/111_00.mp4', 'media/videos/111_00.mp4.json', 'raw/111_page.json.gz']
    original = {path: (data / path).read_bytes() for path in retained}
    try:
        response = httpx.post(base + '/api/delete', headers=HEAD,
                              json={'tweet_id': ident, 'preserve_shared': True})
        assert response.status_code == 200
        assert response.json() == {'deleted': ident, 'left': [], 'preserved_shared': 3}
        assert not (data / f'posts/{ident}').exists()
        assert not (data / f'media/videos/{ident}_own.mp4').exists()
        assert not (data / f'media/videos/{ident}_own.mp4.json').exists()
        assert (data / f'posts/{other}/tweet.json').read_bytes() == before
        assert {path: (data / path).read_bytes() for path in retained} == original
        with sqlite3.connect(data / 'collection.db') as db:
            assert db.execute('SELECT id FROM tweets').fetchall() == [(other,)]
            assert db.execute('SELECT id FROM pages').fetchall() == [('111_page',)]
            receipts = [json.loads(row[0]) for row in db.execute('SELECT doc FROM files')]
            assert receipts == [{'local_media_path': 'media/videos/111_00.mp4'}]
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        with sqlite3.connect(data / 'manual_queue.db') as db:
            assert db.execute('SELECT tweet_id FROM jobs').fetchall() == [(other,)]
    finally:
        server.shutdown()
        server.server_close()


def test_preserve_shared_does_not_hide_unrelated_leftovers(tmp_path):
    from twitter_x_archiver import remove_post
    data = build(tmp_path, [tweet('111', 'media/videos/111_00.mp4', 'shared'),
                            tweet('222', 'media/videos/111_00.mp4', 'shared')])
    plan = remove_post.plan(data, '111')
    remove_post.apply(plan, data, force_shared=False, backup=False)
    (data / 'media/videos/111_failed.mp4').write_bytes(b'undeleted')
    assert remove_post.verify(data, '111', preserved=remove_post.preserved_paths(plan)) == [
        str(Path('media/videos/111_failed.mp4'))]


def test_preserve_shared_keeps_files_referenced_only_by_another_post(tmp_path):
    data, server, base = prepare(tmp_path, [tweet('111'),
        tweet('222', 'media/videos/111_00.mp4', 'shared')])
    try:
        response = httpx.post(base + '/api/delete', headers=HEAD,
                              json={'tweet_id': '111', 'preserve_shared': True})
        assert response.status_code == 200 and response.json()['left'] == []
        assert (data / 'media/videos/111_00.mp4').read_bytes() == b'contenu'
        assert (data / 'media/videos/111_00.mp4.json').is_file()
        assert (data / 'posts/222/tweet.json').is_file()
    finally:
        server.shutdown()
        server.server_close()


def test_conflicting_shared_file_options_are_rejected(tmp_path):
    data, server, base = prepare(tmp_path, [tweet('111')])
    try:
        response = httpx.post(base + '/api/delete', headers=HEAD,
                              json={'tweet_id': '111', 'preserve_shared': True, 'force': True})
        assert response.status_code == 400
        assert (data / 'posts/111/tweet.json').exists()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('body', [
    {}, {'tweet_id': ''}, {'tweet_id': 'abc'}, {'tweet_id': '../../etc'},
    {'tweet_id': '1' * 21}, {'tweet_id': 123}])
def test_malformed_identifiers_are_rejected(tmp_path, body):
    data, server, base = prepare(tmp_path, [tweet('111')])
    try:
        r = httpx.post(base + '/api/delete', json=body, headers=HEAD)
        # Un entier reste acceptable une fois converti ; le reste est refuse.
        expected = 200 if body.get('tweet_id') == 123 else 400
        assert r.status_code == expected, body
        db = sqlite3.connect(data / 'collection.db')
        assert db.execute('SELECT COUNT(*) FROM tweets').fetchone()[0] == 1
        db.close()
    finally:
        server.shutdown()


def test_deleting_requires_pairing(tmp_path):
    data, server, base = prepare(tmp_path, [tweet('111', 'media/videos/111_00.mp4', 'aa')])
    try:
        r = httpx.post(base + '/api/delete', json={'tweet_id': '111'},
                       headers={'Host': '127.0.0.1:18765'})
        assert r.status_code == 403
        assert (data / 'media/videos/111_00.mp4').exists()
    finally:
        server.shutdown()


def test_unknown_post_is_a_no_op(tmp_path):
    data, server, base = prepare(tmp_path, [tweet('111')])
    try:
        r = httpx.post(base + '/api/delete', json={'tweet_id': '999'}, headers=HEAD)
        assert r.status_code == 200 and r.json()['left'] == []
        db = sqlite3.connect(data / 'collection.db')
        assert db.execute('SELECT COUNT(*) FROM tweets').fetchone()[0] == 1
        db.close()
    finally:
        server.shutdown()


def test_no_backup_is_left_behind(tmp_path):
    """L'utilisateur a confirme : « plus une trace » exclut aussi les copies."""
    data, server, base = prepare(tmp_path, [tweet('111', 'media/videos/111_00.mp4', 'aa'),
                                            tweet('222')],
                                 jobs=[('111', 'done'), ('222', 'done')])
    try:
        assert httpx.post(base + '/api/delete', json={'tweet_id': '111'},
                          headers=HEAD).status_code == 200
        assert list(data.glob('*avant_suppression*')) == []
        # Les bases restent saines et le voisin intact.
        for name, table, kept in (('collection.db', 'tweets', 'id'), ('manual_queue.db', 'jobs', 'tweet_id')):
            db = sqlite3.connect(data / name)
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert [i for i, in db.execute(f'SELECT {kept} FROM {table}')] == ['222']
            db.close()
    finally:
        server.shutdown()


def index(tmp_path, posts):
    """Base minimale avec l'index de consultation."""
    from twitter_x_archiver import summary_index
    data = tmp_path / 'data'
    data.mkdir(exist_ok=True)
    db = sqlite3.connect(data / 'collection.db')
    db.executescript('CREATE TABLE tweets(id TEXT PRIMARY KEY, doc TEXT);'
                     'CREATE TABLE state(key TEXT PRIMARY KEY, doc TEXT);')
    for ident, row in posts.items():
        db.execute('INSERT INTO tweets VALUES(?,?)', (ident, __import__('json').dumps(row)))
    db.commit()
    summary_index.ensure(db)
    return db, summary_index


def post(author, posted, views=0, kind='video'):
    return {'author_username': author, 'created_at_paris': posted,
            'last_collected_at': posted, 'view_count': views, 'raw_content': f'texte de {author}',
            'media': [{'media_type': kind,
                       'download': {'local_media_path': f'media/videos/{author}.mp4',
                                    'download_success': True}}]}


def test_date_bounds_filter_on_publication(tmp_path):
    db, idx = index(tmp_path, {
        '1': post('a', '2026-09-04T10:00:00+02:00'),
        '2': post('b', '2026-09-06T23:59:00+02:00'),
        '3': post('c', '2026-09-08T01:00:00+02:00')})
    assert idx.search(db, since='2026-09-06')['total'] == 2
    assert idx.search(db, until='2026-09-06')['total'] == 2
    assert idx.search(db, since='2026-09-06', until='2026-09-06')['total'] == 1
    # Une heure tardive ne doit pas faire basculer un post au jour suivant.
    assert idx.search(db, until='2026-09-06')['items'][0]['author'] in ('a', 'b')
    assert idx.span(db) == {'from': '2026-09-04', 'to': '2026-09-08'}
    db.close()


def test_ascending_and_descending_publication(tmp_path):
    db, idx = index(tmp_path, {
        '1': post('vieux', '2026-09-01T10:00:00+02:00'),
        '2': post('recent', '2026-09-08T10:00:00+02:00')})
    assert [i['author'] for i in idx.search(db, sort='publie')['items']] == ['recent', 'vieux']
    assert [i['author'] for i in idx.search(db, sort='publie_asc')['items']] == ['vieux', 'recent']
    db.close()


def test_filters_combine(tmp_path):
    db, idx = index(tmp_path, {
        '1': post('zerator', '2026-09-05T10:00:00+02:00', 100),
        '2': post('zerator', '2026-09-08T10:00:00+02:00', 900),
        '3': post('mastu', '2026-09-05T10:00:00+02:00', 500)})
    combined = idx.search(db, author='zerator', since='2026-09-05', until='2026-09-05')
    assert combined['total'] == 1 and combined['items'][0]['views'] == 100
    assert idx.search(db, q='mastu')['total'] == 1
    assert idx.search(db, sort='vues')['items'][0]['views'] == 900
    db.close()


def test_paging_never_goes_out_of_range(tmp_path):
    db, idx = index(tmp_path, {str(i): post(f'a{i}', '2026-09-05T10:00:00+02:00') for i in range(30)})
    assert idx.search(db, page=1, size=24)['page'] == 1
    assert len(idx.search(db, page=2, size=24)['items']) == 6
    # Une page trop grande revient a la derniere, plutot que de renvoyer du vide.
    assert idx.search(db, page=99, size=24)['page'] == 2
    assert idx.search(db, q='introuvable')['total'] == 0
    db.close()


def test_refusal_names_the_neighbours(tmp_path):
    """Un refus muet ne se distingue pas d'une panne : il doit nommer la cause."""
    data, server, base = prepare(
        tmp_path,
        [tweet('111', 'media/videos/111_00.mp4', 'aa'),
         tweet('222', 'media/videos/111_00.mp4', 'aa', relation='quote'),
         tweet('333', 'media/videos/111_00.mp4', 'aa', relation='quote')])
    try:
        r = httpx.post(base + '/api/delete', json={'tweet_id': '111'}, headers=HEAD)
        assert r.status_code == 409
        body = r.json()
        assert body['error'] == 'shared'
        assert sorted(body['with']) == ['222', '333']
        assert body['others'] == 2
        assert (data / 'media/videos/111_00.mp4').exists()
    finally:
        server.shutdown()


def test_locked_file_is_retried_before_giving_up(tmp_path, monkeypatch):
    """Une video encore lue resiste un instant sous Windows."""
    from twitter_x_archiver import remove_post
    data = build(tmp_path, [tweet('111', 'media/videos/111_00.mp4', 'aa')])
    attempts = {'n': 0}
    real = remove_post.Path.unlink

    def stubborn(self, *a, **k):
        if self.name == '111_00.mp4':
            attempts['n'] += 1
            if attempts['n'] < 3:
                raise PermissionError('fichier en cours de lecture')
        return real(self, *a, **k)

    monkeypatch.setattr(remove_post.Path, 'unlink', stubborn)
    monkeypatch.setattr(remove_post.time, 'sleep', lambda s: None)
    plan = remove_post.plan(data, '111')
    remove_post.apply(plan, data, force_shared=False, backup=False)
    assert attempts['n'] == 3                      # deux refus, puis la reussite
    assert not (data / 'media/videos/111_00.mp4').exists()
