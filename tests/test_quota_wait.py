import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from twscrape import NoAccountError

from twitter_x_archiver.manual_archive import Jobs, ManualWorker
from test_collector import make_config


def worker(tmp_path, **overrides):
    cfg = make_config(tmp_path)
    cfg.update(overrides)
    jobs = Jobs(cfg['_root'] / cfg['data_dir'])
    w = ManualWorker(cfg, jobs)
    w.ready = True
    return w, jobs


def set_lock(root, seconds, active=1, queue='TweetDetail'):
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / 'accounts.db') as db:
        db.execute('CREATE TABLE IF NOT EXISTS accounts(active INTEGER, locks TEXT)')
        db.execute('DELETE FROM accounts')
        until = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime('%Y-%m-%d %H:%M:%S')
        db.execute('INSERT INTO accounts VALUES(?,?)', (active, json.dumps({queue: until})))


def test_lock_absent_or_expired_reads_as_free(tmp_path):
    w, _ = worker(tmp_path)
    assert w.quota_lock() is None                      # accounts.db absente
    set_lock(w.collector.store.root, -60)
    assert w.quota_lock() is None                      # verrou expire
    set_lock(w.collector.store.root, 300)
    remaining = (w.quota_lock() - datetime.now(timezone.utc)).total_seconds()
    assert 290 < remaining <= 300


def test_inactive_account_lock_is_ignored(tmp_path):
    w, _ = worker(tmp_path)
    set_lock(w.collector.store.root, 300, active=0)
    assert w.quota_lock() is None


def test_wait_returns_immediately_when_free(tmp_path):
    w, _ = worker(tmp_path)
    assert asyncio.run(w.wait_for_quota()) is True
    assert w.quota_until is None


def test_wait_refused_beyond_the_cap(tmp_path):
    w, _ = worker(tmp_path, max_quota_wait_seconds=5)
    set_lock(w.collector.store.root, 600)
    assert asyncio.run(w.wait_for_quota()) is False    # ne bloque pas des heures
    assert w.quota_until is None


def test_wait_is_interrupted_by_the_stop_event(tmp_path):
    w, _ = worker(tmp_path, max_quota_wait_seconds=1200)
    set_lock(w.collector.store.root, 600)
    stop = threading.Event()
    stop.set()
    assert asyncio.run(w.wait_for_quota(stop)) is False
    assert w.quota_until is None                       # etat rendu meme si interrompu


def test_rate_limit_requeues_the_post_instead_of_failing(tmp_path):
    w, jobs = worker(tmp_path)
    jobs.enqueue('https://x.com/i/status/123')

    class API:
        async def tweet_details_raw(self, ident):
            raise NoAccountError('No account available for queue TweetDetail')

    w.collector.api = API()
    assert asyncio.run(w.process(jobs.next())) == 'quota'
    job = jobs.get('123')
    assert job['status'] == 'queued'                   # reste en file, pas 'retry'
    assert job['error'] == 'quota_wait'


def test_drain_gives_up_visibly_when_the_wait_is_refused(tmp_path):
    w, jobs = worker(tmp_path, max_quota_wait_seconds=0, pause_seconds=0)
    jobs.enqueue('https://x.com/i/status/123')
    set_lock(w.collector.store.root, 600)

    class API:
        async def tweet_details_raw(self, ident):
            raise NoAccountError('No account available for queue TweetDetail')

    w.collector.api = API()
    asyncio.run(w.drain())                             # doit se terminer, pas boucler
    job = jobs.get('123')
    assert job['status'] == 'retry' and job['error'] == 'NoAccountError'


def test_drain_resumes_after_a_short_lock(tmp_path):
    w, jobs = worker(tmp_path, max_quota_wait_seconds=1200, pause_seconds=0)
    jobs.enqueue('https://x.com/i/status/123')
    set_lock(w.collector.store.root, 1)
    calls = []

    class API:
        async def tweet_details_raw(self, ident):
            calls.append(ident)
            if len(calls) == 1:
                raise NoAccountError('No account available for queue TweetDetail')
            return None                                # 2e passage : le verrou est tombe

    w.collector.api = API()
    asyncio.run(asyncio.wait_for(w.drain(), timeout=30))
    assert len(calls) == 2                             # le post a bien ete rejoue
    assert jobs.get('123')['status'] == 'retry'        # via x_response_unavailable
    assert jobs.get('123')['error'] == 'x_response_unavailable'
