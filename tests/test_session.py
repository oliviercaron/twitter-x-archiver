"""Session X : validation, stockage local, et ce qui ne doit jamais en sortir."""
import json
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from archive_server import make_handler
from manual_archive import Jobs, ManualWorker
from session_store import forget_session, read_session, session_present, valid, write_session
from test_collector import make_config

GOOD_TOKEN = 'a' * 40
GOOD_CT0 = 'b' * 160


@pytest.mark.parametrize('value', [GOOD_TOKEN, GOOD_CT0, 'aB9._~-' * 3, 'x' * 300])
def test_accepted_cookie_shapes(value):
    assert valid(value)


@pytest.mark.parametrize('value', [
    '', 'court', 'a' * 301, 'avec espace' + 'a' * 20, 'point;virgule' + 'a' * 20,
    'retour\nligne' + 'a' * 20, 'retour\rligne' + 'a' * 20, 'accent é' + 'a' * 20,
    None, 42, ['a' * 40]])
def test_refused_cookie_shapes(value):
    assert not valid(value)


def test_round_trip_and_forget(tmp_path):
    assert session_present(tmp_path) is False
    write_session(tmp_path, GOOD_TOKEN, GOOD_CT0)
    assert session_present(tmp_path) is True
    assert read_session(tmp_path) == {'auth_token': GOOD_TOKEN, 'ct0': GOOD_CT0}
    # Seuls les deux cookies attendus sont conserves.
    assert set(json.loads((tmp_path / 'session.json').read_text(encoding='utf-8'))) == {'auth_token', 'ct0'}
    forget_session(tmp_path)
    assert session_present(tmp_path) is False and read_session(tmp_path) is None


def test_invalid_session_is_never_written(tmp_path):
    with pytest.raises(ValueError):
        write_session(tmp_path, GOOD_TOKEN, 'trop court')
    assert session_present(tmp_path) is False
    # Un fichier corrompu se lit comme une absence, pas comme une session vide.
    write_session(tmp_path, GOOD_TOKEN, GOOD_CT0)
    (tmp_path / 'session.json').write_text('{"auth_token": "x"}', encoding='utf-8')
    assert read_session(tmp_path) is None and session_present(tmp_path) is False


def serve(tmp_path):
    data = tmp_path / 'data'
    data.mkdir(exist_ok=True)
    jobs = Jobs(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(jobs, 'jeton-de-test-tres-long-0123456789'))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}', data


def test_endpoint_requires_pairing_and_validates(tmp_path):
    server, base, data = serve(tmp_path)
    # Le serveur epingle l'hote attendu ; le test tourne sur un port libre.
    head = {'Authorization': 'Bearer jeton-de-test-tres-long-0123456789',
            'Host': '127.0.0.1:18765'}
    anonymous = {'Host': '127.0.0.1:18765'}
    try:
        # Sans jeton d'appairage, rien ne passe.
        r = httpx.post(base + '/api/session', json={'auth_token': GOOD_TOKEN, 'ct0': GOOD_CT0}, headers=anonymous)
        assert r.status_code == 403 and session_present(data) is False

        r = httpx.post(base + '/api/session', json={'auth_token': 'court', 'ct0': GOOD_CT0}, headers=head)
        assert r.status_code == 400 and r.json()['error'] == 'session_rejected'
        assert session_present(data) is False

        r = httpx.post(base + '/api/session', json={'auth_token': GOOD_TOKEN, 'ct0': GOOD_CT0}, headers=head)
        assert r.status_code == 200 and r.json() == {'session_present': True}
        assert read_session(data)['auth_token'] == GOOD_TOKEN

        # La reponse ne renvoie jamais les valeurs recues.
        assert GOOD_TOKEN not in r.text and GOOD_CT0 not in r.text

        listing = httpx.get(base + '/api/jobs', headers=head)
        assert listing.json()['service_status']['session_present'] is True
        assert GOOD_TOKEN not in listing.text and GOOD_CT0 not in listing.text

        assert httpx.post(base + '/api/session/forget', json={}, headers=head).json() == {'session_present': False}
    finally:
        server.shutdown()


def test_worker_reloads_a_new_session_without_restart(tmp_path):
    cfg = make_config(tmp_path)
    root = cfg['_root'] / cfg['data_dir']
    worker = ManualWorker(cfg, Jobs(root))
    assert worker.session_changed() is False        # rien de pret, rien a recharger
    worker.ready = True
    assert worker.session_changed() is False        # etat stable
    write_session(root, GOOD_TOKEN, GOOD_CT0)
    assert worker.session_changed() is True         # session deposee : reauthentifier
    assert worker.session_changed() is False
    forget_session(root)
    assert worker.session_changed() is True         # session retiree aussi


def test_summary_text_is_readable_without_touching_the_archive():
    from summaries import readable, summarise
    row = {'raw_content': 'Pour mes lesbiennes &lt;3 #ZEVENT https://t.co/uUIwdetyEr',
           'author_username': 'a', 'like_count': 3, 'view_count': 9,
           'media': [{'media_type': 'video',
                      'thumbnail_download': {'local_media_path': 'media/thumbnails/1_00.jpg'},
                      'download': {'local_media_path': 'media/videos/1_00.mp4'}}]}
    out = summarise(row)
    assert out['text'] == 'Pour mes lesbiennes <3 #ZEVENT'
    assert row['raw_content'].endswith('uUIwdetyEr')      # la source n'est pas modifiee
    assert out['thumb'] == 'media/thumbnails/1_00.jpg'    # la vignette, pas la video
    assert out['kind'] == 'video' and out['likes'] == 3


def test_summary_falls_back_to_the_image_itself():
    from summaries import summarise
    row = {'media': [{'media_type': 'photo', 'download': {'local_media_path': 'media/images/1_00.jpg'}}]}
    assert summarise(row)['thumb'] == 'media/images/1_00.jpg'
    assert summarise({'media': [{'media_type': 'video',
                                 'download': {'local_media_path': 'media/videos/1_00.mp4'}}]})['thumb'] is None


def test_summaries_are_cached_until_the_database_changes(tmp_path):
    import sqlite3
    import summaries as mod
    data = tmp_path / 'data'
    data.mkdir()
    db = sqlite3.connect(data / 'collection.db')
    db.execute('CREATE TABLE tweets(id TEXT PRIMARY KEY, doc TEXT)')
    db.execute('INSERT INTO tweets VALUES(?,?)', ('1', json.dumps({'author_username': 'a', 'media': []})))
    db.commit(); db.close()
    mod._cache.update(signature=None, value={})
    first = mod.summaries(data)
    assert list(first) == ['1']
    assert mod.summaries(data) is first                    # meme objet : rien n'a ete relu
    assert mod.summaries(tmp_path / 'absent') == {}


def test_summary_exposes_a_playable_video():
    from summaries import summarise
    row = {'media': [
        {'media_type': 'photo', 'download': {'local_media_path': 'media/images/1_00.jpg',
                                             'download_success': True}},
        {'media_type': 'video', 'download': {'local_media_path': 'media/videos/1_01.mp4',
                                             'download_success': True,
                                             'actual_duration_seconds': 17.14}}]}
    out = summarise(row)
    assert out['video'] == 'media/videos/1_01.mp4'
    assert out['duration'] == 17.1
    # Un post mixte est une video : classer sur le premier media le rangerait
    # en photo et le ferait disparaitre du filtre.
    assert out['kind'] == 'video'


def test_a_failed_download_is_not_playable():
    from summaries import summarise
    out = summarise({'media': [{'media_type': 'video',
                                'download': {'local_media_path': 'media/videos/1_00.mp4',
                                             'download_success': False}}]})
    assert out['video'] is None and out['duration'] is None
    assert out['kind'] == 'video'          # le post reste une video, elle manque seulement


def test_kind_without_media_stays_empty():
    from summaries import summarise
    assert summarise({'media': []})['kind'] is None
    assert summarise({'media': [{'media_type': 'animated_gif',
                                 'download': {'local_media_path': 'a.mp4',
                                              'download_success': True}}]})['kind'] == 'video'
