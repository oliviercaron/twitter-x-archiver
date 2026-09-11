#!/usr/bin/env python3
"""ZEVENT X collection. See README.md; never put cookies in CLI arguments."""
import argparse
import asyncio
import copy
import dataclasses
import gzip
import hashlib
import importlib.metadata
import inspect
import json
import os
import platform
import sys
from contextlib import aclosing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml
from dotenv import load_dotenv
import app_paths
import categories
from archive import Store, atomic_json, now
from session_store import read_session
from normalize import at, counts, flatten, media_items, normalize, raw_objects, reference_only, stamp
from media import Downloader

ROOT = Path(__file__).resolve().parent
VERSION = importlib.metadata.version('twscrape')


def config_load(path):
    path = Path(path).resolve()
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    config['_root'] = path.parent
    zone = ZoneInfo(config['timezone'])
    for key in ('start','end_exclusive'):
        d = datetime.fromisoformat(str(config[key]))
        if d.tzinfo is None:
            d = d.replace(tzinfo=zone)
        config['_'+key] = d.astimezone(timezone.utc)
    if config['_start'] >= config['_end_exclusive']:
        raise ValueError('Invalid date window')
    if config.get('slice_hours',24) <= 0:
        raise ValueError('slice_hours must be positive')
    return config


def plan(config):
    # La liste des streamers est propre a une edition du ZEVENT : elle enrichit
    # `streamer_name_match` mais n'est indispensable a rien. Son absence laisse
    # simplement ce champ vide, au lieu d'empecher tout demarrage.
    path = config['_root']/config['streamers_file']
    if not path.exists():
        path = app_paths.assets()/config['streamers_file']
    source = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'streamers':[]}
    queries = list(config['queries'])
    aliases = []
    for s in source['streamers']:
        names = [s.get('display_name'),s.get('twitch_username'),s.get('x_username'),*s.get('aliases',[])]
        names = list({n.casefold():n for n in names if n}.values())
        aliases.extend(names)
        if config.get('include_streamers',True):
            for n in names:
                quoted = '"'+n.replace('"','')+'"'
                queries.extend((quoted+' '+op).strip() for op in config.get('streamer_operators',['filter:videos']))
    queries = list(dict.fromkeys(queries))
    slices = []
    start = config['_start']
    while start < config['_end_exclusive']:
        end = min(start+timedelta(hours=config.get('slice_hours',24)),config['_end_exclusive'])
        # X date operators are only day-resolution: overlap UTC date envelopes, filter exact bounds locally.
        upper = end if end.time() == datetime.min.time() else end.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
        for base in queries:
            q = f'{base} since:{start:%Y-%m-%d} until:{upper:%Y-%m-%d}'
            key = hashlib.sha256((q+start.isoformat()+end.isoformat()).encode()).hexdigest()[:24]
            slices.append({'key':key,'query':q,'base_query':base,'start':start.isoformat(),'end':end.isoformat()})
        start = end
    return queries,sorted(set(aliases)),slices,source


def inspect_schema(directory):
    import twscrape.models as models
    from twscrape import API
    from twscrape.accounts_pool import AccountsPool
    names = ['Tweet','User','UserRef','Media','MediaPhoto','MediaVideo','MediaAnimated','MediaVideoVariant','SummaryCard','PollCard','BroadcastCard']
    result = {'twscrape_version':VERSION,'python_version':platform.python_version(),
              'models':{n:{f.name:str(f.type) for f in dataclasses.fields(getattr(models,n))} for n in names if hasattr(models,n)},
              'signatures':{f.__qualname__:str(inspect.signature(f)) for f in [API,API.search_raw,API.tweet_details_raw,AccountsPool.add_account_cookies]},
              'installed_models_sha256':hashlib.sha256(Path(inspect.getfile(models)).read_bytes()).hexdigest()}
    atomic_json(directory/'schema_actual.json',result)
    return result


@contextmanager
def single_writer(root):
    """OS lock releases on process death; works under Windows and Linux."""
    lock = root/'collector.lock'
    f = lock.open('a+b')
    try:
        f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError('Another collector is already running') from None
    try:
        yield
    finally:
        if os.name == 'nt':
            f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            fcntl.flock(f.fileno(),fcntl.LOCK_UN)
        f.close()


class Collector:
    def __init__(self, config):
        self.config = config
        self.queries,self.aliases,self.slices,self.streamers = plan(config)
        self.store = Store(config['_root']/config['data_dir'])
        self.downloader = None
        self.api = None
        self.interrupted_request = False
        self.parse_failures = 0
        self.successful_parses = 0
        self.sink_id = None

    def metadata(self):
        return {'collector':'twscrape','twscrape_version':VERSION,
                'period_start_utc':self.config['_start'].isoformat(),'period_end_exclusive_utc':self.config['_end_exclusive'].isoformat(),
                'timezone':self.config['timezone'],'exact_queries':[s['query'] for s in self.slices],
                'streamers_source':{k:v for k,v in self.streamers.items() if k!='streamers'},
                'coverage_status':'search_index_coverage_not_exhaustiveness',
                'window_still_open':datetime.now(timezone.utc)<self.config['_end_exclusive'],
                'authentication_status':self.store.state('authentication_status','not_configured')}

    def export(self):
        return self.store.export(self.metadata())

    async def authenticate(self):
        load_dotenv(self.config['_root']/'.env',override=False)
        token, ct0 = os.environ.get('X_AUTH_TOKEN'),os.environ.get('X_CT0')
        # A defaut de .env, la session deposee par l'extension. Les valeurs ne
        # sont jamais journalisees ni renvoyees par l'API.
        if not (token and ct0):
            session = read_session(self.store.root)
            if session:
                token, ct0 = session['auth_token'], session['ct0']
        # Prevent inherited network proxies and telemetry. One dedicated account only.
        for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy','TWS_PROXY'):
            os.environ.pop(key,None)
        os.environ['TWS_TELEMETRY']='0'
        os.environ['TWS_HTTP_BACKEND']=self.config.get('http_backend','curl')
        from twscrape import API
        from twscrape.logger import logger
        # Do not forward dependency messages: they may include request context.
        logger.remove()
        def log_event(message):
            text = message.record['message'].lower()
            kind = None
            if 'rate limit' in text or 'locked' in text or 'waiting' in text:
                kind = 'rate_limited'
            elif 'protected' in text:
                kind = 'account_protected'
            elif any(w in text for w in ['expired','banned','ban detected','blocked']):
                kind = 'authentication_failed'; self.interrupted_request = True
            elif message.record['level'].no >= 30 or any(w in text for w in ['stalled','failed','abort','unhandled','cooling','retrying']):
                kind = 'temporary_network_error'; self.interrupted_request = True
            if kind:
                self.store.event(kind)
                print(kind,flush=True)
        self.sink_id = logger.add(log_event,level='DEBUG',catch=True)
        self.api = API(str(self.store.root/'accounts.db'),raise_when_no_account=True,debug=False)
        accounts = await self.api.pool.get_all()
        if len(accounts)>1 or any(a.username != 'research' or a.proxy for a in accounts):
            raise RuntimeError('Dedicated account database must contain only research without proxy')
        existing = accounts[0] if accounts else None
        if bool(token) != bool(ct0):
            raise RuntimeError('Both X_AUTH_TOKEN and X_CT0 are required')
        if token and ct0:
            if any(c in token+ct0 for c in ';\r\n'):
                raise RuntimeError('Invalid cookie value format')
            if not existing or existing.cookies.get('auth_token')!=token or existing.cookies.get('ct0')!=ct0:
                await self.api.pool.add_account_cookies('research',f'auth_token={token}; ct0={ct0}')
        elif not existing:
            self.store.put_state('authentication_status','missing_cookies')
            print('Session X absente. Connectez-vous à X dans Chrome, puis cliquez sur « Utiliser ma session X » sur la page locale.')
            return False
        self.store.put_state('authentication_status','configured_not_yet_verified')
        self.downloader = Downloader(self.store,self.config)
        return True

    async def ingest(self, envelope, exact=None, rehydrate_id=None, selected_ids=None, discussion_root=None):
        page_id = envelope['page_id']
        if self.store.db.execute('SELECT 1 FROM pages WHERE id=?',(page_id,)).fetchone():
            return {'raw':0,'kept':0,'new_unique':0,'video':0,'photo':0}
        payload = envelope['response']
        tweets,users = raw_objects(payload)
        from twscrape.models import Tweet
        from twscrape.utils import to_old_rep
        # Library compatibility parsing is separate from analytical extraction: defaults stay in parsed_tweet only.
        try:
            compat = to_old_rep(copy.deepcopy(payload))
        except Exception:
            compat = {'tweets':{},'users':{}}
        rows = {}
        for ident,raw in tweets.items():
            parsed, error = None, None
            try:
                if reference_only(raw):
                    self.store.event('tweet_reference_only',tweet_id=ident)
                else:
                    parsed = json.loads(Tweet.parse(compat['tweets'][ident],compat).json())
                    self.successful_parses += 1
            except Exception as exc:
                error = type(exc).__name__
                self.parse_failures += 1
                self.store.event('parse_error',tweet_id=ident,error_class=error)
            row = normalize(raw,users,parsed,envelope['observed_at'],envelope['query'],VERSION,self.aliases,envelope.get('raw_ref'),error)
            row['python_version'] = platform.python_version()
            row['collection_mode'] = envelope['mode']
            if discussion_root:
                row['discussion_roots'] = [str(discussion_root)]
            if envelope.get('selection') and (not discussion_root or ident == str(discussion_root)):
                selection = envelope['selection']
                row['manual_selections'] = [selection]
                row['selection_mode'] = selection['mode']
                row['selected_at'] = selection['selected_at']
                row['selection_url'] = selection['url']
                row['category'] = categories.clean(selection.get('category'))
            rows[ident] = row
        # Archive associated quote/repost media even when source tweet is outside the search window.
        for ident,row in rows.items():
            if selected_ids is not None and ident not in selected_ids:
                continue
            seen = {(m.get('source_tweet_id'),m.get('media_id') or m.get('media_url')) for m in row['media']}
            for field,relation in [('quoted_tweet_id','quote'),('reposted_tweet_id','repost')]:
                ref = rows.get(row[field])
                if ref:
                    for orig in ref['media']:
                        key = (orig.get('source_tweet_id'),orig.get('media_id') or orig.get('media_url'))
                        if key not in seen:
                            m = copy.deepcopy(orig); m['media_relation']=relation; row['media'].append(m); seen.add(key)
            counts(row)
            # Un post a reponses limitees est sous tweet_results.result.tweet.rest_id.
            row['is_context_tweet'] = not any(at(n,'tweet_results.result.rest_id')==ident or at(n,'tweet_results.result.tweet.rest_id')==ident or n.get('entryId')=='tweet-'+ident for n in self._timeline_entries(payload))
        stats = {'raw':len(rows),'kept':0,'new_unique':0,'video':0,'photo':0,'media':0}
        kept_ids = []
        for ident,row in rows.items():
            if selected_ids is not None and ident not in selected_ids:
                continue
            if rehydrate_id and ident != str(rehydrate_id):
                continue
            date = row.get('created_at_utc')
            inside = bool(date and self.config['_start'] <= datetime.fromisoformat(date) < self.config['_end_exclusive'])
            row['within_collection_window'] = inside if date else None
            if exact and date:
                inside = datetime.fromisoformat(exact['start']) <= datetime.fromisoformat(date) < datetime.fromisoformat(exact['end'])
            if not inside and date and not rehydrate_id and selected_ids is None:
                continue  # raw response and referenced media remain archived
            stats['new_unique'] += self.store.upsert(row)
            stats['kept'] += 1; stats['video'] += row['has_video']; stats['photo'] += row['has_photo']
            stats['media'] += row['has_media']
            kept_ids.append(ident)
        self.store.db.execute('INSERT INTO pages VALUES(?)',(page_id,))
        self.store.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('raw_count',str(self.store.state('raw_count',0)+len(rows))))
        self.store.db.commit()
        # Tweets durable before network downloads. --download-media retries unfinished files.
        self.export()
        if self.downloader:
            for ident in kept_ids:
                await self.downloader.enrich(self.store.get(ident))
        self.export()
        return stats

    @staticmethod
    def _timeline_entries(payload):
        from normalize import walk
        return (n for n in walk(payload) if n.get('entryId') or 'tweet_results' in n)

    async def search_slice(self, item, mode='collect', page_limit=None):
        key = 'query:'+item['key']
        prior = self.store.state(key,{})
        state = {**item,'status':'running','started_at':now(),'attempts':prior.get('attempts',0)+1,
                 'new_unique':prior.get('new_unique',0),'raw':prior.get('raw',0),'pages':prior.get('pages',0),
                 'kept_observations':prior.get('kept_observations',0),'media_observations':prior.get('media_observations',0)}
        self.store.put_state(key,state)
        self.interrupted_request = False
        parsed_before = self.successful_parses
        pages, kept = 0,0
        try:
            async with aclosing(self.api.search_raw(item['query'],limit=-1,kv={'product':'Latest'})) as gen:
                async for rep in gen:
                    payload = rep.json()
                    env = self.store.archive(payload,item['query'],mode,self.config.get('archive_raw',True))
                    if rep.status_code!=200 or payload.get('errors'):
                        self.store.event('graphql_error',query_key=item['key'],http_status=rep.status_code)
                        self.interrupted_request=True
                        state['status']='retry_required'
                        break
                    stats = await self.ingest(env,exact=item)
                    pages += 1; kept += stats['kept']
                    for k in ('raw','new_unique'):
                        state[k] += stats[k]
                    state['pages'] += 1
                    state['kept_observations'] += stats['kept']
                    state['media_observations'] += stats.get('media',0)
                    state['last_page_at']=env['observed_at']
                    state['last_cursor']=self.api._get_cursor(payload)
                    state['last_page_video']=stats['video']; state['last_page_photo']=stats['photo']
                    self.store.put_state(key,state)
                    print(f"{mode}: {item['base_query']} — pages {pages}, tweets retenus {kept}",flush=True)
                    if page_limit and pages>=page_limit:
                        state['status']='sampled_not_complete'; break
                    await asyncio.sleep(self.config.get('pause_seconds',2))
            else_status = 'retry_required' if self.interrupted_request else ('provisional' if datetime.fromisoformat(item['end'])>datetime.now(timezone.utc) else 'exhausted_returned_results')
            if state['status']=='running':
                state['status']=else_status
            state['empirical_result']='results_observed' if kept else 'zero_results_inconclusive'
            state['observed_media_fraction'] = state['media_observations']/state['kept_observations'] if state['kept_observations'] else None
            state['operator_support'] = 'not_proven_by_results_alone'
            if mode=='smoke' and kept and self.successful_parses>parsed_before:
                self.store.put_state('smoke_passed',{'at':now(),'version':VERSION,'kept':kept,'parsed_successfully':self.successful_parses-parsed_before})
                self.store.put_state('authentication_status','verified')
            state['finished_at']=now()
            self.store.put_state(key,state)
            return state
        except BaseException:
            state['status']='interrupted_retry_from_slice_start'
            self.store.put_state(key,state)
            raise

    async def rehydrate(self):
        for ident in [r['tweet_id'] for r in self.store.rows()]:
            self.interrupted_request=False
            rep = await self.api.tweet_details_raw(int(ident))
            if rep is None:
                self.store.event('temporary_network_error',tweet_id=ident)
                continue
            payload=rep.json()
            env=self.store.archive(payload,'tweet_details:'+ident,'rehydrate',self.config.get('archive_raw',True))
            tweets,_=raw_objects(payload)
            if ident in tweets and rep.status_code==200:
                await self.ingest(env,rehydrate_id=ident)
            else:
                # Only explicit missing-status or tombstone evidence changes availability.
                from normalize import walk
                codes={x.get('code') for x in payload.get('errors',[]) if isinstance(x,dict)}
                tombstones=[x for x in walk(payload) if x.get('__typename') in ('TweetTombstone','TweetUnavailable')]
                explicit=bool(codes.intersection({144,179}) or tombstones)
                # A tombstone for a reply is not evidence about the focal tweet.
                focal_tombstone=any(str(x.get('rest_id',''))==ident for x in tombstones)
                explicit=bool(codes.intersection({144,179}) or focal_tombstone)
                row=self.store.get(ident)
                if explicit and not self.interrupted_request:
                    row['tweet_currently_available']=False
                    row['availability_observed_at']=now()
                    row.setdefault('availability_history',[]).append({'observed_at':now(),'available':False,'evidence':'explicit_endpoint_unavailability'})
                    self.store.save(row); self.store.db.commit()
                    self.store.event('account_protected' if 179 in codes else 'tweet_deleted_or_unavailable',tweet_id=ident)
                else:
                    self.store.event('tweet_not_found',tweet_id=ident,availability='unknown')
            self.export()
            await asyncio.sleep(self.config.get('pause_seconds',2))

    async def run(self,args):
        inspect_schema(self.config['_root'])
        atomic_json(self.store.root/'query_plan.json',{'base_queries':self.queries,'slices':self.slices,'empirical_status':'not_tested_without_authentication'})
        try:
            if args.mode in ('prepare','inspect'):
                return 0
            if args.mode in ('report','download-media','rebuild'):
                if args.mode=='download-media':
                    self.downloader=Downloader(self.store,self.config)
                    for row in list(self.store.rows()):
                        await self.downloader.enrich(row)
                elif args.mode=='rebuild':
                    for path in sorted((self.store.root/'raw').glob('*.json.gz')):
                        with gzip.open(path,'rt',encoding='utf-8') as f:
                            env=json.load(f)
                        env['raw_ref']=str(path.relative_to(self.store.root))
                        await self.ingest(env,rehydrate_id=env['query'].split(':',1)[1] if env['mode']=='rehydrate' else None)
                return 0
            if not await self.authenticate():
                return 2
            if args.mode=='rehydrate':
                await self.rehydrate(); return 0
            # Default starts with a small full-window smoke test, never thousands of queries blindly.
            if args.mode=='smoke' or (args.mode=='collect' and not self.store.state('smoke_passed')):
                item=next(s.copy() for s in self.slices if s['base_query']=='ZEVENT')
                start,end=self.config['_start'],self.config['_end_exclusive']
                item.update(start=start.isoformat(),end=end.isoformat(),key='smoke',
                            query=f'ZEVENT since:{start:%Y-%m-%d} until:{end+timedelta(days=1):%Y-%m-%d}')
                await self.search_slice(item,'smoke',page_limit=1)
                if args.mode=='smoke':
                    return 0 if self.store.state('smoke_passed') else 3
                if not self.store.state('smoke_passed'):
                    print('Smoke test non concluant : consulter metadata.json. Collecte complète non lancée.')
                    return 3
            if args.mode=='probe':
                for base in self.config['queries']:
                    item=next(s.copy() for s in self.slices if s['base_query']==base)
                    item['key']='probe_'+item['key']
                    item['start']=self.config['_start'].isoformat(); item['end']=self.config['_end_exclusive'].isoformat()
                    item['query']=base+f" since:{self.config['_start']:%Y-%m-%d} until:{self.config['_end_exclusive']+timedelta(days=1):%Y-%m-%d}"
                    await self.search_slice(item,'probe',page_limit=1)
                return 0
            for item in self.slices:
                previous=self.store.state('query:'+item['key'],{})
                if previous.get('status')=='exhausted_returned_results' and not args.rerun:
                    continue
                # Future slices stay pending and are naturally collected on a subsequent run.
                if datetime.fromisoformat(item['start'])>datetime.now(timezone.utc):
                    continue
                await self.search_slice(item)
            return 0
        finally:
            if self.downloader:
                await self.downloader.close()
            meta=self.export()
            self.store.validation()
            print(json.dumps({k:meta[k] for k in ['raw_tweet_observations','unique_tweets','tweets_with_media','tweets_with_video','tweets_with_photo','downloaded_videos','downloaded_images','media_failures','tweets_without_author','tweets_without_metrics','tweets_without_text','date_min','date_max','top_20_queries_by_new_unique']},ensure_ascii=False,indent=2))
            print('Aperçu de 10 tweets :')
            print((self.store.root/'sample_10.json').read_text(encoding='utf-8'))
            if self.sink_id is not None:
                from twscrape.logger import logger
                logger.remove(self.sink_id)
            self.store.db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(ROOT/'config.yaml'))
    parser.add_argument('--mode',choices=['prepare','inspect','smoke','probe','collect','rehydrate','download-media','report','rebuild'],default='collect')
    parser.add_argument('--rerun',action='store_true',help='Repeat completed date slices to discover late-indexed posts')
    args=parser.parse_args()
    try:
        config=config_load(args.config)
        root=config['_root']/config['data_dir']; root.mkdir(parents=True,exist_ok=True)
        with single_writer(root):
            return asyncio.run(Collector(config).run(args))
    except KeyboardInterrupt:
        print('Interruption : observations sauvegardées, relancer la même commande.'); return 130
    except Exception as exc:
        # Never print exception text or traceback which might contain cookie/header values.
        print(f'Échec ({type(exc).__name__}). État conservé. Vérifier configuration, compte et metadata.json.'); return 1


if __name__=='__main__':
    sys.exit(main())
