"""Une categorie par post : rangement, filtre et portee d'export."""
import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from twitter_x_archiver import categories
from twitter_x_archiver import summary_index
from twitter_x_archiver.server import make_handler
from twitter_x_archiver.export_results import export_results
from twitter_x_archiver.manual_archive import Jobs
from test_remove_post import build, tweet

TOKEN = 'jeton-de-test-tres-long-0123456789'
HEAD = {'Authorization': f'Bearer {TOKEN}', 'Host': '127.0.0.1:18765'}


def serve(data):
    jobs = Jobs(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(jobs, TOKEN))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'


def indexed(data):
    """La base de test n'a pas d'index de consultation : on le construit."""
    db = sqlite3.connect(data / 'collection.db')
    db.execute('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, doc TEXT NOT NULL)')
    summary_index.ensure(db)
    db.commit()
    db.close()


def prepare(tmp_path, rows, jobs=()):
    data = build(tmp_path, rows, jobs=jobs)
    indexed(data)
    return data, *serve(data)


def ranged(ident, name):
    row = tweet(ident)
    row['category'] = name
    row['manual_selections'] = [{'url': f'https://x.com/i/status/{ident}', 'mode': 'manual_url',
                                 'selected_at': '2026-09-06T00:00:00+00:00', 'category': name}]
    return row


# --------------------------------------------------------------- mise au propre
@pytest.mark.parametrize('value,expected', [
    ('  Essai   de   rangement ', 'Essai de rangement'),
    ('', categories.DEFAUT),
    (None, categories.DEFAUT),
    ('   ', categories.DEFAUT),
    ('x' * 200, 'x' * categories.LONGUEUR),
])
def test_names_are_tidied(value, expected):
    assert categories.clean(value) == expected


def test_a_post_without_a_category_falls_back_to_the_default():
    assert categories.of({}) == categories.DEFAUT
    assert categories.of({'category': 'Autre'}) == 'Autre'


# ------------------------------------------------------------------------ index
def test_the_index_filters_and_counts_by_category(tmp_path):
    data = build(tmp_path, [ranged('111', 'ZEVENT 2026'), ranged('222', 'ZEVENT 2026'),
                            ranged('333', 'Autre chose')])
    indexed(data)
    db = sqlite3.connect(f'file:{data}/collection.db?mode=ro', uri=True)
    try:
        assert summary_index.categories(db) == [{'category': 'ZEVENT 2026', 'count': 2},
                                                {'category': 'Autre chose', 'count': 1}]
        assert summary_index.search(db, category='Autre chose')['total'] == 1
        assert summary_index.search(db, category='ZEVENT 2026')['total'] == 2
        assert summary_index.search(db)['total'] == 3
        assert summary_index.search(db, category='Inconnue')['total'] == 0
    finally:
        db.close()


# --------------------------------------------------------------------- rangement
def test_moving_a_post_updates_the_list_without_asking_x(tmp_path):
    data, server, base = prepare(tmp_path, [ranged('111', 'ZEVENT 2026'), ranged('222', 'ZEVENT 2026')])
    try:
        r = httpx.post(base + '/api/category',
                       json={'tweet_id': '111', 'category': '  Moments   clippables '}, headers=HEAD)
        assert r.status_code == 200
        assert r.json() == {'tweet_id': '111', 'category': 'Moments clippables'}

        listed = httpx.get(base + '/api/categories', headers=HEAD).json()
        assert listed['default'] == categories.DEFAUT
        assert {c['category']: c['count'] for c in listed['categories']} == {
            'ZEVENT 2026': 1, 'Moments clippables': 1}

        page = httpx.get(base + '/api/posts?categorie=Moments%20clippables', headers=HEAD).json()
        assert [i['id'] for i in page['items']] == ['111']
        assert page['items'][0]['category'] == 'Moments clippables'

        db = sqlite3.connect(f'file:{data}/collection.db?mode=ro', uri=True)
        stored = json.loads(db.execute('SELECT doc FROM tweets WHERE id=?', ('111',)).fetchone()[0])
        db.close()
        assert stored['category'] == 'Moments clippables'
    finally:
        server.shutdown()


def test_an_empty_name_puts_the_post_back_in_the_default_category(tmp_path):
    data, server, base = prepare(tmp_path, [ranged('111', 'Autre chose')])
    try:
        r = httpx.post(base + '/api/category', json={'tweet_id': '111', 'category': ''}, headers=HEAD)
        assert r.json()['category'] == categories.DEFAUT
    finally:
        server.shutdown()


@pytest.mark.parametrize('body,status', [
    ({'tweet_id': '999', 'category': 'x'}, 404),
    ({'tweet_id': 'pas-un-nombre', 'category': 'x'}, 400),
    ({'category': 'x'}, 400),
])
def test_refused_moves(tmp_path, body, status):
    data, server, base = prepare(tmp_path, [ranged('111', 'ZEVENT 2026')])
    try:
        assert httpx.post(base + '/api/category', json=body, headers=HEAD).status_code == status
    finally:
        server.shutdown()


# ------------------------------------------------------------------------- file
def test_the_queue_keeps_the_chosen_category(tmp_path):
    data = build(tmp_path, [])
    jobs = Jobs(data)
    job = jobs.enqueue('https://x.com/a/status/111', mode='manual_url', category='  Moments  clippables ')
    assert job['selection']['category'] == 'Moments clippables'
    assert jobs.enqueue('https://x.com/a/status/222', mode='manual_url')['selection']['category'] == categories.DEFAUT


def test_a_finished_post_is_refetched_only_when_its_category_changes(tmp_path):
    data = build(tmp_path, [])
    jobs = Jobs(data)
    jobs.enqueue('https://x.com/a/status/111', mode='manual_url', category='ZEVENT 2026')
    jobs.update(jobs.get('111'), status='done')

    again = jobs.enqueue('https://x.com/a/status/111', mode='manual_url', category='ZEVENT 2026')
    assert again['status'] == 'done'          # rien n'a change : X n'est pas redemande

    moved = jobs.enqueue('https://x.com/a/status/111', mode='manual_url', category='Autre chose')
    assert moved['status'] == 'queued'
    assert moved['selection']['category'] == 'Autre chose'


# ----------------------------------------------------------------------- export
def test_the_export_can_be_limited_to_one_category(tmp_path):
    data = build(tmp_path, [ranged('111', 'ZEVENT 2026'), ranged('222', 'Autre chose')])
    out = tmp_path / 'sortie'

    everything = export_results(data, out)
    assert everything['tweets'] == 2

    only = export_results(data, out, only=['Autre chose'])
    assert only['tweets'] == 1
    lines = (out / only['path'].rsplit('\\', 1)[-1].rsplit('/', 1)[-1] / 'tweets.csv') \
        .read_text(encoding='utf-8-sig').splitlines()
    assert any('"222"' in line for line in lines[1:])
    assert not any('"111"' in line for line in lines[1:])


# ------------------------------------------------- rangement pendant l'archivage
def queued(data, ident='111'):
    """Une vraie entree de file, telle que l'extension la cree."""
    jobs = Jobs(data)
    jobs.enqueue(f'https://x.com/a/status/{ident}', mode='manual_extension')
    return jobs


def test_moving_a_post_still_downloading_updates_the_queue(tmp_path):
    """Le document n'existe pas encore : la file doit porter le rangement.

    Sans cela, le telechargement en cours ecrit sa propre categorie en fin de
    course et efface le choix fait dans le bandeau de l'extension.
    """
    data = build(tmp_path, [])
    indexed(data)
    jobs = queued(data)
    server, base = serve(data)
    try:
        r = httpx.post(base + '/api/category', json={'tweet_id': '111', 'category': 'Info'}, headers=HEAD)
        assert r.status_code == 200
        assert r.json() == {'tweet_id': '111', 'category': 'Info'}
        assert jobs.get('111')['selection']['category'] == 'Info'
    finally:
        server.shutdown()


def test_moving_an_archived_post_updates_the_queue_too(tmp_path):
    data = build(tmp_path, [ranged('111', 'ZEVENT 2026')])
    indexed(data)
    jobs = queued(data)
    jobs.update(jobs.get('111'), status='done')
    server, base = serve(data)
    try:
        httpx.post(base + '/api/category', json={'tweet_id': '111', 'category': 'Info'}, headers=HEAD)
        assert jobs.get('111')['selection']['category'] == 'Info'
        page = httpx.get(base + '/api/posts?categorie=Info', headers=HEAD).json()
        assert [i['id'] for i in page['items']] == ['111']
    finally:
        server.shutdown()


def test_the_archiver_applies_the_latest_category(tmp_path):
    """Ce que le collecteur ecrit en fin d'archivage suit la file relue.

    Reproduit la sequence exacte : la file part avec la categorie par defaut,
    le bandeau la change pendant le telechargement, et c'est le second choix
    qui doit rester.
    """
    data = build(tmp_path, [])
    jobs = queued(data)
    depart = jobs.get('111')                     # ce que le worker garde en main
    assert depart['selection']['category'] == categories.DEFAUT

    jobs.update(jobs.get('111'),
                selection={**jobs.get('111')['selection'], 'category': 'Info'})

    relu = jobs.get('111') or depart
    assert categories.clean((relu.get('selection') or {}).get('category')) == 'Info'
    assert categories.clean((depart.get('selection') or {}).get('category')) == categories.DEFAUT
