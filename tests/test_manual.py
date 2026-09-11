import asyncio
import copy
import gzip
import json
import threading
from http.server import ThreadingHTTPServer
import httpx
import pytest
from archive_urls import read_urls
from archive_server import make_handler
from manual_archive import Jobs,ManualWorker,canonical_url
from test_collector import fixture,make_config
from normalize import raw_objects


def test_account_status_exposes_only_quota_and_active_flag(tmp_path):
    import sqlite3
    from datetime import datetime,timedelta,timezone
    from archive_server import account_status
    with sqlite3.connect(tmp_path/'accounts.db') as db:
        db.execute('CREATE TABLE accounts(active INTEGER,locks TEXT,cookies TEXT)')
        reset=(datetime.now(timezone.utc)+timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
        db.execute('INSERT INTO accounts VALUES(?,?,?)',(1,json.dumps({'TweetDetail':reset}),'SECRET_NOT_TO_EXPOSE'))
    status=account_status(tmp_path)
    assert status['quota_wait'] is True and status['account_active'] is True
    assert set(status)=={'account_active','quota_wait','quota_reset_at'}
    assert 'SECRET_NOT_TO_EXPOSE' not in json.dumps(status)


@pytest.mark.parametrize('url',[
 'https://x.com/name/status/123?s=20','https://twitter.com/name/status/123/video/1',
 'https://x.com/i/status/123','https://x.com/i/web/status/123','http://mobile.twitter.com/name/status/123'])
def test_canonical_post_urls(url):
    assert canonical_url(url)==('123','https://x.com/i/status/123')


@pytest.mark.parametrize('url',['https://x.com/home','https://x.com.evil.test/a/status/123','https://evil.test/a/status/123',
 'file:///a/status/123','https://user:password@x.com/a/status/123','https://x.com:9000/a/status/123','https://x.com/a/status/0'])
def test_reject_non_post_urls(url):
    with pytest.raises(ValueError):canonical_url(url)


def test_txt_bom_comments_crlf_duplicates_and_invalid(tmp_path):
    p=tmp_path/'links.txt'
    p.write_text('\ufeff# Mes choix\r\n\r\nhttps://x.com/user/status/123?s=20\r\nhttps://twitter.com/user/status/123\r\ninvalid\r\nhttps://x.com/b/status/456\r\n',encoding='utf-8',newline='')
    good,bad=read_urls(p)
    assert [x['line'] for x in good]==[3,6] and bad==[{'line':5,'error':'invalid_post_url'}]


def test_queue_dedup_refresh_and_crash_recovery(tmp_path):
    jobs=Jobs(tmp_path)
    a=jobs.enqueue('https://x.com/a/status/123')
    assert jobs.enqueue('https://twitter.com/b/status/123')==a
    job=jobs.next();assert job['status']=='fetching'
    assert jobs.next() is None
    jobs.recover();assert jobs.get('123')['status']=='queued'
    job=jobs.next();jobs.update(job,status='done',result={'metadata_available':True})
    assert jobs.enqueue(a['url'])['status']=='done'
    assert jobs.enqueue(a['url'],refresh=True)['status']=='queued'


@pytest.mark.parametrize('text',['A chosen post',''])
def test_manual_fetch_one_post_outside_window_preserves_selection(tmp_path,text):
    class Rep:
        status_code=200
        def json(self):return payload
    class API:
        calls=[]
        async def tweet_details_raw(self,ident):self.calls.append(ident);return Rep()
    payload=fixture();tweets,_=raw_objects(payload);raw=next(iter(tweets.values()));ident=raw['rest_id']
    raw['legacy']['created_at']='Wed Jan 01 16:00:00 +0000 2025'
    raw['legacy']['full_text']=text
    raw['legacy']['extended_entities']['media']=[]
    extra=copy.deepcopy(raw);extra['rest_id']='222';payload['context']=extra
    async def run():
        jobs=Jobs(tmp_path/'data');jobs.enqueue('https://x.com/name/status/'+ident,mode='manual_txt',source={'line':3})
        worker=ManualWorker(make_config(tmp_path),jobs);worker.ready=True;worker.collector.api=API()
        result=await worker.process(jobs.next())
        assert result['status']=='done' and API.calls==[int(ident)]
        rows=list(worker.collector.store.rows());assert len(rows)==1 and rows[0]['within_collection_window'] is False
        assert rows[0]['selection_mode']=='manual_txt'
        post=tmp_path/'data'/result['result']['post_json'];assert post.exists()
        with gzip.open(tmp_path/'data'/result['result']['raw_response'],'rt') as f:env=json.load(f)
        assert env['selection']['source']['line']==3
        await worker.close()
    asyncio.run(run())


def test_missing_media_is_partial_not_success(tmp_path):
    class Rep:
        status_code=200
        def json(self):return fixture()
    class API:
        async def tweet_details_raw(self,ident):return Rep()
    async def run():
        cfg=make_config(tmp_path);jobs=Jobs(tmp_path/'data');jobs.enqueue('https://x.com/a/status/1234567890123456789')
        worker=ManualWorker(cfg,jobs);worker.ready=True;worker.collector.api=API()
        result=await worker.process(jobs.next())
        assert result['status']=='partial' and result['result']['media_failed']==3
        await worker.close()
    asyncio.run(run())


def test_failure_does_not_expose_exception_secrets(tmp_path):
    class API:
        async def tweet_details_raw(self,ident):raise RuntimeError('DO_NOT_LEAK_COOKIE')
    async def run():
        cfg=make_config(tmp_path);jobs=Jobs(tmp_path/'data');jobs.enqueue('https://x.com/a/status/123')
        worker=ManualWorker(cfg,jobs);worker.ready=True;worker.collector.api=API()
        result=await worker.process(jobs.next())
        assert result['status']=='retry' and result['error']=='RuntimeError'
        assert 'DO_NOT_LEAK_COOKIE' not in json.dumps(result)
        await worker.close()
    asyncio.run(run())


def test_bridge_requires_key_and_rejects_cross_site_requests(tmp_path):
    jobs=Jobs(tmp_path);token='synthetic-test-token'
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(jobs,token))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with httpx.Client(trust_env=False,headers={'Host':'127.0.0.1:18765'}) as client:
            payload={'url':'https://x.com/a/status/123'}
            assert client.post(base+'/api/archive',json=payload).status_code==403
            h={'Authorization':'Bearer '+token,'Origin':'https://evil.test'}
            assert client.post(base+'/api/archive',json=payload,headers=h).status_code==403
            h['Origin']='chrome-extension://'+'a'*32
            assert client.post(base+'/api/archive',json=payload,headers=h).status_code==202
            assert len(client.get(base+'/api/jobs',headers=h).json()['jobs'])==1
            assert client.get(base+'/api/jobs',headers={**h,'Host':'evil.test:18765'}).status_code==403
            assert client.post(base+'/api/archive',json={'url':'https://evil.test'},headers=h).status_code==400
    finally:server.shutdown();server.server_close();thread.join()
