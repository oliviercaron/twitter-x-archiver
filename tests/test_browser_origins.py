import threading
from http.server import ThreadingHTTPServer
import httpx
import pytest
from twitter_x_archiver.server import make_handler
from twitter_x_archiver.manual_archive import Jobs

TOKEN='test-browser-origin-token-000000000'
UUID='E28C6932-2A51-48B3-8B33-214187BBA831'

@pytest.fixture
def endpoint(tmp_path):
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(Jobs(tmp_path),TOKEN))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:yield f'http://127.0.0.1:{server.server_port}'
    finally:server.shutdown();server.server_close()

@pytest.mark.parametrize('origin', ['chrome-extension://'+'a'*32,'moz-extension://'+UUID,'safari-web-extension://'+UUID])
def test_browser_requests_still_require_secret(endpoint,origin):
    headers={'Host':'127.0.0.1:18765','Origin':origin}
    denied=httpx.get(endpoint+'/api/jobs',headers=headers,trust_env=False)
    assert denied.status_code==403
    headers['Authorization']='Bearer '+TOKEN
    response=httpx.get(endpoint+'/api/jobs',headers=headers,trust_env=False)
    assert response.status_code==200
    assert response.headers['Access-Control-Allow-Origin']==origin

@pytest.mark.parametrize('origin', ['https://x.com','https://evil.example','moz-extension://'+UUID+'/path',
                                   'moz-extension://'+UUID+'.evil.example','safari-web-extension://invalid','null'])
def test_website_cannot_impersonate_extension(endpoint,origin):
    response=httpx.get(endpoint+'/api/jobs',headers={'Host':'127.0.0.1:18765','Origin':origin,'Authorization':'Bearer '+TOKEN},trust_env=False)
    assert response.status_code==403
    assert 'Access-Control-Allow-Origin' not in response.headers
