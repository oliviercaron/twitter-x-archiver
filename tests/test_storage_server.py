"""Offline checks of the storage API and worker pause/copy/reopen lifecycle."""
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
import httpx
import pytest
import archive_server as server
from manual_archive import Jobs
from storage_manager import StorageManager

TOKEN='synthetic-storage-token'
HEADERS={'Host':'127.0.0.1:18765','Authorization':'Bearer '+TOKEN}

@pytest.fixture
def endpoint(tmp_path):
    source=tmp_path/'source';source.mkdir()
    storage=StorageManager(source,lambda _:None)
    pause=threading.Event()
    stop=threading.Event()
    host=ThreadingHTTPServer(('127.0.0.1',0),server.make_handler(Jobs(source),TOKEN,stop,storage,threading.RLock(),pause))
    threading.Thread(target=host.serve_forever,daemon=True).start()
    with httpx.Client(base_url=f'http://127.0.0.1:{host.server_port}',headers=HEADERS,trust_env=False) as client:
        yield client,storage,pause,stop
    host.shutdown();host.server_close()


def test_storage_authenticated_and_invalid_destination_safe(endpoint,tmp_path):
    client,storage,pause,_=endpoint
    assert client.get('/api/storage',headers={'Authorization':''}).status_code==403
    bad=client.post('/api/storage/move',json={'destination':str(storage.source)})
    assert bad.status_code==400
    assert bad.json()['error']=='storage_request_rejected'
    assert not pause.is_set()
    assert client.get('/api/storage').json()['status']=='idle'


def test_busy_blocks_changes_through_activation_but_keeps_health_and_progress(endpoint,tmp_path):
    client,storage,pause,stop=endpoint
    result=client.post('/api/storage/move',json={'destination':str(tmp_path/'target')})
    assert result.status_code==202 and pause.is_set()
    for path in ['/api/archive','/api/delete','/api/category','/api/session','/api/results']:
        assert client.post(path,json={}).status_code==409
    assert client.get('/health').status_code==200
    assert client.get('/').status_code==200
    assert client.get('/api/storage').json()['status']=='pending'
    assert client.get('/api/jobs').status_code==503
    storage.perform()
    assert client.get('/api/storage').json()['status']=='switching'
    assert client.post('/api/archive',json={}).status_code==409
    assert client.post('/api/shutdown',json={}).status_code==202 and stop.is_set()


def test_active_export_refuses_move(tmp_path,monkeypatch):
    class Export:
        thread=type('Running',(),{'is_alive':lambda _:True})()
        def __init__(self,_):pass
    monkeypatch.setattr(server,'ExportManager',Export)
    source=tmp_path/'source';source.mkdir()
    storage=StorageManager(source,lambda _:None);pause=threading.Event()
    host=ThreadingHTTPServer(('127.0.0.1',0),server.make_handler(Jobs(source),TOKEN,None,storage,None,pause))
    threading.Thread(target=host.serve_forever,daemon=True).start()
    try:
        r=httpx.post(f'http://127.0.0.1:{host.server_port}/api/storage/move',headers=HEADERS,
                     json={'destination':str(tmp_path/'target')},trust_env=False)
        assert r.status_code==409 and r.json()['error']=='export_running'
        assert storage.view()['status']=='idle' and not pause.is_set()
    finally:host.shutdown();host.server_close()


@pytest.mark.parametrize('fail_copy',[False,True])
def test_main_closes_worker_before_copy_and_reopens_correct_archive(tmp_path,monkeypatch,fail_copy):
    source=tmp_path/'source';source.mkdir();target=tmp_path/'target'
    (source/'media').mkdir();(source/'media'/'example.jpg').write_bytes(b'synthetic-image')
    (source/'export_settings.json').write_text(json.dumps({'destination':str(tmp_path/'my-csv')}))
    monkeypatch.setattr(server.app_paths,'ensure_config',lambda:tmp_path/'config.yaml')
    monkeypatch.setattr(server,'config_load',lambda _: {'data_dir':'data','_root':tmp_path})
    monkeypatch.setattr(server.app_paths,'resolve_data_dir',lambda _:source)
    pointers=[]
    def save(path):
        if fail_copy and path==target:raise OSError('synthetic full disk')
        pointers.append(path)
    monkeypatch.setattr(server.app_paths,'set_data_dir',save)
    hosts=[]
    def make_server(address,handler):
        host=ThreadingHTTPServer(('127.0.0.1',0),handler);hosts.append(host);return host
    monkeypatch.setattr(server,'ThreadingHTTPServer',make_server)
    opened=[]
    class Worker:
        def __init__(self,config,jobs):
            self.data=Path(config['data_dir']);self.jobs=jobs;opened.append(self.data)
        async def drain(self,stop):
            token=json.loads((source/'secrets_bridge.json').read_text())['token']
            with httpx.Client(base_url=f'http://127.0.0.1:{hosts[0].server_port}',headers={**HEADERS,'Authorization':'Bearer '+token},trust_env=False) as client:
                if len(opened)==1:
                    assert client.post('/api/storage/move',json={'destination':str(target)}).status_code==202
                    assert stop.is_set()
                else:
                    expected=source if fail_copy else target
                    assert self.data==expected
                    assert (self.data/'finalized.txt').read_text()=='closed before copying'
                    assert (self.data/'media'/'example.jpg').read_bytes()==b'synthetic-image'
                    assert json.loads((self.data/'secrets_bridge.json').read_text())['token']==token
                    assert client.get('/api/storage').json()['status']==('error' if fail_copy else 'done')
                    assert client.get('/api/jobs').status_code==200
                    assert client.get('/api/results').json()['destination'].endswith('my-csv')
                    assert client.post('/api/shutdown',json={}).status_code==202
        async def close(self):
            (self.data/'finalized.txt').write_text('closed before copying')
    monkeypatch.setattr(server,'ManualWorker',Worker)
    server.main()
    assert opened==[source,source if fail_copy else target]
    assert pointers==([source] if fail_copy else [source,target])
    assert (source/'media'/'example.jpg').exists()
