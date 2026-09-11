"""A durable queue for explicit post selections; never performs a search."""
import asyncio
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from twscrape import NoAccountError
from . import categories
from .archive import atomic_json, now
from .collector import Collector
from .normalize import raw_objects, reference_only
from .session_store import path as session_path

QUOTA_QUEUE = 'TweetDetail'
QUOTA_SLICE = 1.0          # tranches courtes : l'arret du serveur reste immediat

HOSTS = {'x.com','www.x.com','twitter.com','www.twitter.com','mobile.twitter.com','mobile.x.com'}
POST = re.compile(r'^/(?:[A-Za-z0-9_]{1,50}|i/web)/status/(\d{1,20})(?:/(?:photo|video)/\d+)?/?$')


def canonical_url(value):
    value = str(value).strip()
    p = urlsplit(value)
    if p.scheme not in ('http','https') or p.hostname not in HOSTS or p.username or p.password or p.port:
        raise ValueError('URL de post X/Twitter attendue')
    match = POST.fullmatch(p.path)
    if not match or not (0 < int(match[1]) < 2**64):
        raise ValueError('URL de post X/Twitter attendue')
    ident = match[1]
    return ident, 'https://x.com/i/status/'+ident


class Jobs:
    def __init__(self, data):
        self.path = Path(data)/'manual_queue.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS jobs(tweet_id TEXT PRIMARY KEY, status TEXT, doc TEXT)')

    def connect(self):
        return sqlite3.connect(self.path,timeout=20)

    def get(self, ident):
        with self.connect() as c:
            r = c.execute('SELECT doc FROM jobs WHERE tweet_id=?',(ident,)).fetchone()
        return json.loads(r[0]) if r else None

    def all(self):
        with self.connect() as c:
            return [json.loads(r[0]) for r in c.execute('SELECT doc FROM jobs ORDER BY rowid DESC')]

    def enqueue(self, url, mode='manual_extension', note='', source=None, refresh=False,
                include_replies=False, category=''):
        ident,url = canonical_url(url)
        if mode not in {'manual_extension','manual_txt','manual_url'}:
            raise ValueError('Mode de sélection invalide')
        selection = {'url':url,'mode':mode,'selected_at':now(),'note':str(note)[:2000],'source':source,
                     'include_replies':bool(include_replies),'category':categories.clean(category)}
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            previous = c.execute('SELECT doc FROM jobs WHERE tweet_id=?',(ident,)).fetchone()
            old = json.loads(previous[0]) if previous else None
            if old:
                upgrade = include_replies and not old.get('include_replies',False)
                if old['status'] in ('queued','fetching'):
                    if upgrade:
                        old['include_replies']=True
                        old['selection']['include_replies']=True
                        c.execute('UPDATE jobs SET doc=? WHERE tweet_id=?',(json.dumps(old,ensure_ascii=False),ident))
                    return old
                deplace = old.get('selection',{}).get('category') != selection['category']
                if old['status']=='done' and not refresh and not upgrade and not deplace:
                    return old
            job = {'tweet_id':ident,'url':url,'selection':selection,'status':'queued','queued_at':now(),
                   'updated_at':now(),'attempts':old.get('attempts',0) if old else 0,
                   'previous_results':(old.get('previous_results',[])+[old.get('result')]) if old and old.get('result') else [],
                   'result':None,'error':None,'include_replies':bool(include_replies),'refresh':bool(refresh)}
            c.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?)',(ident,'queued',json.dumps(job,ensure_ascii=False)))
        return job

    def update(self, job, **changes):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT doc FROM jobs WHERE tweet_id=?',(job['tweet_id'],)).fetchone()
            latest=json.loads(current[0]) if current else job
            job = {**latest,**changes,'updated_at':now()}
            # An option enabled while the root downloads must not be lost.
            if changes.get('status')=='done' and job.get('include_replies') and not (job.get('result') or {}).get('discussion'):
                job['status']='queued'
            c.execute('UPDATE jobs SET status=?,doc=? WHERE tweet_id=?',(job['status'],json.dumps(job,ensure_ascii=False),job['tweet_id']))
        return job

    def statuses(self):
        """Identifiant et statut seulement : la liste complete pese trop pour un
        rafraichissement regulier depuis chaque onglet X."""
        with self.connect() as c:
            return {i: s for i, s in c.execute('SELECT tweet_id,status FROM jobs')}

    def next(self):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            r = c.execute("SELECT doc FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not r:
                return None
            job = json.loads(r[0]); job.update(status='fetching',updated_at=now(),attempts=job['attempts']+1)
            c.execute('UPDATE jobs SET status=?,doc=? WHERE tweet_id=?',('fetching',json.dumps(job,ensure_ascii=False),job['tweet_id']))
        return job

    def recover(self):
        # Call only after acquiring collector.lock: interrupted downloads are idempotent.
        for job in self.all():
            if job['status']=='fetching':
                self.update(job,status='queued',error='interrupted_will_resume')


class ManualWorker:
    def __init__(self, config, jobs):
        self.collector = Collector(config)
        self.jobs = jobs
        self.ready = False
        self.quota_until = None
        self.session_seen = None

    def session_changed(self):
        """Une session deposee par l'extension doit etre prise sans redemarrage."""
        try:
            stamp = session_path(self.collector.store.root).stat().st_mtime_ns
        except OSError:
            stamp = None
        changed = self.ready and stamp != self.session_seen
        self.session_seen = stamp
        return changed

    def quota_lock(self, queue=QUOTA_QUEUE):
        """Date de deblocage du compte pour cette file, lue dans accounts.db.

        None si aucun verrou en cours. Lecture seule : le pool twscrape reste
        seul a ecrire dans cette base.
        """
        path = self.collector.store.root/'accounts.db'
        if not path.exists():
            return None
        try:
            with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
                rows = db.execute('SELECT locks FROM accounts WHERE active').fetchall()
        except sqlite3.Error:
            return None
        dates = []
        for locks, in rows:
            value = (json.loads(locks or '{}') or {}).get(queue)
            if value:
                try:
                    dates.append(datetime.fromisoformat(value).replace(tzinfo=timezone.utc))
                except ValueError:
                    continue
        # Plusieurs comptes : le premier libere debloque la file.
        soonest = min(dates, default=None)
        return soonest if soonest and soonest > datetime.now(timezone.utc) else None

    async def wait_for_quota(self, stop=None):
        """Patiente jusqu'a expiration du verrou de debit. False si l'attente est refusee."""
        until = self.quota_lock()
        if until is None:
            return True
        remaining = (until - datetime.now(timezone.utc)).total_seconds()
        cap = float(self.collector.config.get('max_quota_wait_seconds', 1200))
        if remaining > cap:
            # Un verrou anormalement long releve d'une decision humaine, pas d'une attente.
            return False
        self.quota_until = until
        self.collector.store.event('quota_wait', queue=QUOTA_QUEUE, seconds=round(remaining))
        try:
            while remaining > 0:
                if stop is not None and stop.is_set():
                    return False
                await asyncio.sleep(min(QUOTA_SLICE, remaining))
                remaining = (until - datetime.now(timezone.utc)).total_seconds()
        finally:
            self.quota_until = None
        return True

    async def process(self, job):
        c = self.collector
        ident = job['tweet_id']
        try:
            if self.session_changed():
                self.ready = False
            if not self.ready:
                self.ready = await c.authenticate()
                if not self.ready:
                    return self.jobs.update(job,status='retry',error='cookies_missing')
            rep = await c.api.tweet_details_raw(int(ident))
            if rep is None:
                return self.jobs.update(job,status='retry',error='x_response_unavailable')
            payload = rep.json()
            env = c.store.archive(payload,job['url'],job['selection']['mode'],True,selection=job['selection'])
            tweets,_ = raw_objects(payload)
            if rep.status_code!=200:
                return self.jobs.update(job,status='retry',error='http_'+str(rep.status_code))
            if ident not in tweets or reference_only(tweets[ident]):
                # Keep a precise request receipt even when X cannot return the selected post.
                c.store.event('manual_tweet_unavailable',tweet_id=ident,raw_ref=env['raw_ref'])
                return self.jobs.update(job,status='retry',error='tweet_response_incomplete',result={'raw_response':env['raw_ref']})
            await c.ingest(env,rehydrate_id=ident)
            row = c.store.get(ident)
            # La categorie a pu changer pendant le telechargement, depuis le
            # bandeau de l'extension ou la page locale. La file fait foi : elle
            # a ete relue a l'instant, le document non.
            latest = self.jobs.get(ident) or job
            wanted = categories.clean((latest.get('selection') or {}).get('category'))
            if row.get('category') != wanted:
                row['category'] = wanted
                c.store.save(row)
                c.store.db.commit()
            media = row.get('media',[])
            failed = [m for m in media if not m.get('download',{}).get('download_success')]
            thumbs = [m for m in media if m.get('thumbnail_url') and not m.get('thumbnail_download',{}).get('download_success')]
            # Additional convenient per-post JSON receipt; canonical dataset remains one Parquet.
            destination = c.store.root/'posts'/ident
            atomic_json(destination/'tweet.json',row)
            # Preserve each explicit observation as well as its raw GraphQL page.
            atomic_json(destination/'snapshots'/(env['page_id']+'.json'),row)
            result = {'tweet_id':ident,'media_count':len(media),'media_downloaded':len(media)-len(failed),
                      'media_failed':len(failed),'thumbnail_failed':len(thumbs),'parse_error':row.get('parse_error'),
                      'post_json':str((destination/'tweet.json').relative_to(c.store.root)).replace('\\','/'),
                      'raw_response':env['raw_ref'],'parquet':'zevent2026_tweets.parquet',
                      'media_paths':list(dict.fromkeys(m['download']['local_media_path'] for m in media if m.get('download',{}).get('download_success'))),
                      'metadata_available':bool(row.get('record_completeness')=='payload_present' and row.get('author_id'))}
            state = 'partial' if failed or thumbs or not result['metadata_available'] else 'done'
            job=self.jobs.get(ident)
            if job.get('include_replies'):
                from .discussion_archive import Discussion
                discussion=Discussion(c,job,progress=lambda info:self.jobs.update(job,result={**result,'discussion':info}))
                self.jobs.update(job,result=result)
                result['discussion']=await discussion.run(env)
                if result['discussion']['status']!='finished_available_pages':state='partial'
            return self.jobs.update(job,status=state,result=result,error=None)
        except NoAccountError:
            # Limite de debit de X : le post reste en file, il n'a pas echoue.
            self.jobs.update(job,status='queued',error='quota_wait')
            return 'quota'
        except asyncio.CancelledError:
            self.jobs.update(job,status='queued',error='interrupted_will_resume')
            raise
        except Exception as exc:
            # No raw exception text, request headers, or cookies.
            c.store.event('manual_archive_failed',tweet_id=ident,error_class=type(exc).__name__)
            return self.jobs.update(job,status='retry',error=type(exc).__name__)

    async def drain(self, stop=None):
        while stop is None or not stop.is_set():
            job = self.jobs.next()
            if not job:
                if stop is None:break
                await asyncio.sleep(.5)
                continue
            outcome = await self.process(job)
            if outcome == 'quota' and not await self.wait_for_quota(stop):
                # Attente refusee : le post redevient une erreur visible plutot
                # que de faire tourner la boucle a vide.
                self.jobs.update(self.jobs.get(job['tweet_id']),status='retry',error='NoAccountError')
                if stop is None:
                    break
            await asyncio.sleep(self.collector.config.get('pause_seconds',2))

    async def close(self):
        c = self.collector
        if c.downloader:await c.downloader.close()
        c.export();c.store.validation()
        if c.sink_id is not None:
            from twscrape.logger import logger
            logger.remove(c.sink_id)
        c.store.db.close()
