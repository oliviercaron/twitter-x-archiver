"""Service des medias : ce qui sort, ce qui ne sort pas, et la reprise sur intervalle."""
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from archive_server import make_handler
from manual_archive import Jobs

HOST = {'Host': '127.0.0.1:18765'}
CONTENT = bytes(range(256)) * 40          # 10 240 octets, motif verifiable


@pytest.fixture
def base(tmp_path):
    data = tmp_path / 'data'
    (data / 'media' / 'videos').mkdir(parents=True)
    (data / 'media' / 'images').mkdir(parents=True)
    (data / 'media' / 'videos' / 'clip.mp4').write_bytes(CONTENT)
    (data / 'media' / 'images' / 'vue.jpg').write_bytes(b'\xff\xd8\xff' + CONTENT)
    (data / 'secret.txt').write_text('interdit', encoding='utf-8')
    (data / 'media' / 'notes.txt').write_text('interdit', encoding='utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(Jobs(data), 'jeton-inutile-ici'))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


def test_whole_file_is_served_without_pairing(base):
    """Une balise img ou video ne peut pas envoyer d'en-tete d'autorisation."""
    r = httpx.get(base + '/media/videos/clip.mp4', headers=HOST)
    assert r.status_code == 200
    assert r.headers['content-type'] == 'video/mp4'
    assert r.headers['accept-ranges'] == 'bytes'
    assert r.content == CONTENT


def test_image_keeps_its_type(base):
    r = httpx.get(base + '/media/images/vue.jpg', headers=HOST)
    assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg'


@pytest.mark.parametrize('asked,start,end', [
    ('bytes=0-99', 0, 99),
    ('bytes=1000-1999', 1000, 1999),
    ('bytes=10000-', 10000, len(CONTENT) - 1),
    ('bytes=-100', len(CONTENT) - 100, len(CONTENT) - 1),
    ('bytes=0-999999', 0, len(CONTENT) - 1)])
def test_ranges_return_exactly_the_asked_bytes(base, asked, start, end):
    r = httpx.get(base + '/media/videos/clip.mp4', headers={**HOST, 'Range': asked})
    assert r.status_code == 206
    assert r.headers['content-range'] == f'bytes {start}-{end}/{len(CONTENT)}'
    assert r.content == CONTENT[start:end + 1]


def test_range_beyond_the_file_is_refused(base):
    r = httpx.get(base + '/media/videos/clip.mp4', headers={**HOST, 'Range': 'bytes=99999-'})
    assert r.status_code == 416
    assert r.headers['content-range'] == f'bytes */{len(CONTENT)}'


def test_unparsable_range_serves_the_whole_file(base):
    r = httpx.get(base + '/media/videos/clip.mp4', headers={**HOST, 'Range': 'octets=0-10'})
    assert r.status_code == 200 and r.content == CONTENT


@pytest.mark.parametrize('path', [
    '/media/../secret.txt',
    '/media/videos/../../secret.txt',
    '/media/..%2fsecret.txt',
    '/media/%2e%2e/secret.txt'])
def test_traversal_is_refused(base, path):
    assert httpx.get(base + path, headers=HOST).status_code == 403


@pytest.mark.parametrize('path', ['/media/notes.txt', '/media/videos/absent.mp4'])
def test_other_files_are_not_served(base, path):
    """Le type autorise ne suffit pas : le fichier doit exister, et inversement."""
    assert httpx.get(base + path, headers=HOST).status_code == 404


def test_wrong_host_is_refused(base):
    assert httpx.get(base + '/media/videos/clip.mp4',
                     headers={'Host': 'ailleurs.test'}).status_code == 403
