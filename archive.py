"""Transactional checkpoints and atomic exports; SQLite is internal, Parquet analytical."""
import gzip
import hashlib
import json
import os
import platform
import random
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from normalize import counts, snapshot


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.partial')
    with tmp.open('w', encoding='utf-8') as f:
        f.write(dumps(value)); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in ('raw','media/images','media/videos','media/thumbnails'):
            (self.root/sub).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root/'collection.db')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS tweets(id TEXT PRIMARY KEY, doc TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, doc TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files(key TEXT PRIMARY KEY, doc TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pages(id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, observed_at TEXT, kind TEXT, doc TEXT);
        ''')
        import summary_index
        summary_index.ensure(self.db)

    def get(self, ident):
        r = self.db.execute('SELECT doc FROM tweets WHERE id=?',(str(ident),)).fetchone()
        return json.loads(r[0]) if r else None

    def rows(self):
        for r in self.db.execute('SELECT doc FROM tweets ORDER BY id'):
            yield json.loads(r[0])

    def state(self, key, default=None):
        r = self.db.execute('SELECT doc FROM state WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default

    def put_state(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',(key,dumps(value)))
        self.db.commit()

    def event(self, kind, **details):
        # Call sites pass classifications/IDs, never exception messages or headers.
        self.db.execute('INSERT INTO events(observed_at,kind,doc) VALUES(?,?,?)',(now(),kind,dumps(details)))
        self.db.commit()

    def save(self, row):
        # Le resume s'ecrit dans la meme transaction que le document : les deux
        # ne peuvent pas diverger, et rien n'a besoin d'etre relu ensuite.
        import summary_index
        self.db.execute('INSERT OR REPLACE INTO tweets VALUES(?,?)',(row['tweet_id'],dumps(row)))
        summary_index.put(self.db,row['tweet_id'],row)

    def upsert(self, row):
        old = self.get(row['tweet_id'])
        snap = snapshot(row)
        availability_observation = {'observed_at':row['availability_observed_at'],'available':row['tweet_currently_available']}
        if old:
            if row.get('manual_selections'):
                row['manual_selections'] = list({dumps(s):s for s in old.get('manual_selections',[])+row['manual_selections']}.values())
            row['first_collected_at'] = min(old['first_collected_at'],row['first_collected_at'])
            row['last_collected_at'] = max(old['last_collected_at'],row['last_collected_at'])
            for key in ['collection_queries','raw_response_refs','discussion_roots']:
                row[key] = list(dict.fromkeys(old.get(key,[])+row.get(key,[])))
            row['collection_query'] = old['collection_query']
            history = old.get('metrics_snapshots', [])
            row['metrics_snapshots'] = history if snap in history else history+[snap]
            old_media = {(m.get('source_tweet_id'),m.get('media_id') or m.get('media_url'),m.get('media_relation')):m for m in old.get('media',[])}
            merged = []
            for m in row['media']:
                key = (m.get('source_tweet_id'),m.get('media_id') or m.get('media_url'),m.get('media_relation'))
                prior = old_media.pop(key, {})
                # Download result tied to exact source URL: preserve prior file history on variant change.
                for k in ['download','thumbnail_download','archived_downloads']:
                    if k in prior:
                        m[k] = prior[k]
                merged.append(m)
            merged.extend(old_media.values())
            row['media'] = merged
            row['availability_history'] = old.get('availability_history',[])
            if row['tweet_currently_available'] is None or old.get('availability_observed_at','') > row['availability_observed_at']:
                row['availability_observed_at'] = old['availability_observed_at']
                row['tweet_currently_available'] = old['tweet_currently_available']
            # Missing fields in a later partial response do not destroy prior information.
            carried = []
            for k,v in old.items():
                if row.get(k) is None and k not in ['parse_error']:
                    row[k] = v
                    if v is not None:
                        carried.append(k)
            row['carried_forward_fields'] = carried
            if row['metrics_observed_at'] < old.get('metrics_observed_at',''):
                latest = old.copy()
                for k in ['metrics_snapshots','collection_queries','raw_response_refs','first_collected_at','last_collected_at','media','discussion_roots']:
                    latest[k] = row[k]
                row = latest
        else:
            row['metrics_snapshots'] = [snap]
            row['availability_history'] = []
        if availability_observation not in row['availability_history']:
            row['availability_history'].append(availability_observation)
        counts(row)
        self.save(row)
        return old is None

    def archive(self, payload, query, mode, enabled=True, selection=None):
        page_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'_'+uuid.uuid4().hex
        envelope = {'page_id':page_id,'observed_at':now(),'query':query,'mode':mode,'response':payload}
        if selection is not None:
            envelope['selection'] = selection
        path = self.root/'raw'/(page_id+'.json.gz')
        if enabled:
            path.parent.mkdir(exist_ok=True)
            tmp = path.with_suffix('.partial')
            with tmp.open('wb') as stream:
                with gzip.GzipFile(fileobj=stream,mode='wb') as gz:
                    gz.write((dumps(envelope)+'\n').encode())
                stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp,path)
        envelope['raw_ref'] = str(path.relative_to(self.root)) if enabled else None
        return envelope

    def file(self, key):
        r = self.db.execute('SELECT doc FROM files WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else None

    def put_file(self, keys, result):
        for key in keys:
            self.db.execute('INSERT OR REPLACE INTO files VALUES(?,?)',(key,dumps(result)))
        self.db.commit()

    def export(self, metadata):
        rows = list(self.rows())
        # Nested, schema-evolving JSON is explicit and lossless. Simple lists stay native.
        native_lists = {'hashtags','cashtags','collection_queries','raw_response_refs','streamer_name_match'}
        converted = []
        for row in rows:
            converted.append({k:dumps(v) if isinstance(v,(list,dict)) and k not in native_lists else v for k,v in row.items()})
        # Union all columns: Arrow inference from the first row would silently drop later-only keys.
        keys = sorted(set().union(*(r.keys() for r in converted))) if converted else ['tweet_id']
        if converted:
            table = pa.table({k:[r.get(k) for r in converted] for k in keys})
        else:
            from normalize import normalize
            sample = normalize({'rest_id':'0'}, {}, None, now(), '', '', [], None)
            sample.update(metrics_snapshots=[],availability_history=[],python_version='',within_collection_window=None,
                          collection_mode='',is_context_tweet=False,carried_forward_fields=[])
            columns = {}
            for k,v in sample.items():
                typ = pa.list_(pa.string()) if k in native_lists else (pa.bool_() if isinstance(v,bool) else pa.int64() if isinstance(v,int) else pa.string())
                columns[k] = pa.array([],type=typ)
            table = pa.table(columns)
        schema_meta = dict(table.schema.metadata or {})
        schema_meta[b'nested_encoding'] = b'JSON strings except hashtags, cashtags, collection_queries, raw_response_refs, streamer_name_match'
        table = table.replace_schema_metadata(schema_meta)
        dest = self.root/'zevent2026_tweets.parquet'
        tmp = dest.with_suffix('.parquet.partial')
        pq.write_table(table,tmp,compression='zstd')
        # Ouverture en lecture-ecriture : sous Windows, fsync exige un
        # descripteur inscriptible et echoue en EBADF sur un 'rb'.
        with tmp.open('rb+') as f:
            os.fsync(f.fileno())
        os.replace(tmp,dest)
        all_media = [m for r in rows for m in r.get('media',[])]
        files = {m.get('download',{}).get('local_media_path'):m for m in all_media if m.get('download',{}).get('download_success')}
        dates = [r['created_at_utc'] for r in rows if r.get('created_at_utc')]
        queries = [json.loads(r[0]) for r in self.db.execute("SELECT doc FROM state WHERE key LIKE 'query:%'")]
        meta = {**metadata,'exported_at':now(),'python_version':platform.python_version(),'operating_system':platform.platform(),
                'unique_tweets':len(rows),'tweets_with_media':sum(r['has_media'] for r in rows),
                'tweets_with_video':sum(r['has_video'] for r in rows),'tweets_with_photo':sum(r['has_photo'] for r in rows),
                'downloaded_media_files':len(files),'downloaded_videos':sum(m['media_type'] in ('video','animated_gif') for m in files.values()),
                'downloaded_images':sum(m['media_type']=='photo' for m in files.values()),
                'media_failures':sum(m.get('download',{}).get('download_success') is False for m in all_media),
                'thumbnail_failures':sum(m.get('thumbnail_download',{}).get('download_success') is False for m in all_media),
                'tweets_without_author':sum(not r.get('author_id') for r in rows),
                'tweets_without_text':sum(not r.get('raw_content') for r in rows),
                'tweets_without_metrics':sum(all(r.get(k) is None for k in ['view_count','like_count','repost_count','reply_count','quote_count','bookmark_count']) for r in rows),
                'date_min':min(dates,default=None),'date_max':max(dates,default=None),
                'raw_tweet_observations':self.state('raw_count',0),
                'query_progress':queries,'errors_by_kind':dict(self.db.execute('SELECT kind,COUNT(*) FROM events GROUP BY kind')),
                'top_20_queries_by_new_unique':sorted(queries,key=lambda x:x.get('new_unique',0),reverse=True)[:20]}
        atomic_json(self.root/'metadata.json',meta)
        sample = random.Random(2026).sample(rows,min(10,len(rows)))
        sample = [{'created_at':r.get('created_at_utc'),'username':r.get('author_username'),'followers':r.get('author_followers_count'),
                   'texte':r.get('raw_content'),'views':r.get('view_count'),'likes':r.get('like_count'),'reposts':r.get('repost_count'),
                   'has_video':r.get('has_video'),'video_views':[m.get('video_view_count') for m in r['media'] if m['media_type']=='video'],
                   'local_video_path':[m.get('download',{}).get('local_media_path') for m in r['media'] if m['media_type']=='video']} for r in sample]
        atomic_json(self.root/'sample_10.json',sample)
        return meta

    def validation(self):
        rows = list(self.rows())
        rng = random.Random(2026)
        chosen = []
        for flag in (True,False):
            group = [r for r in rows if r['has_video']==flag]
            for row in rng.sample(group,min(10,len(group))):
                chosen.append({'tweet_id':row['tweet_id'],'tweet_url':row['tweet_url'],'raw_content':row.get('raw_content'),
                               'author_username':row.get('author_username'),'has_video':row['has_video'],
                               'metrics_observed_at':row.get('metrics_observed_at'),'media':row['media'],
                               'manual_checks':dict.fromkeys(['url','text','author','metrics','media_correspondence','duration'],'pending')})
        path = self.root/'validation_sample.json'
        # Preserve any manual annotations on subsequent exports.
        prior = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        annotations = {r['tweet_id']:r['manual_checks'] for r in prior.get('sample',[])}
        for r in chosen:
            r['manual_checks'] = annotations.get(r['tweet_id'],r['manual_checks'])
        result = {'generated_at':now(),'status':'manual_review_pending' if chosen else 'blocked_no_authenticated_data',
                  'requested_per_group':10,'sample':chosen}
        atomic_json(path,result)
        lines = ['# Validation du corpus','',f"État : {result['status']}",
                 f"Échantillon : {sum(r['has_video'] for r in chosen)} avec vidéo, {sum(not r['has_video'] for r in chosen)} sans vidéo.",
                 'Les contrôles humains ne sont jamais déclarés réalisés automatiquement.','']
        for r in chosen:
            lines += [f"- [{r['tweet_id']}]({r['tweet_url']}) — @{r['author_username']} — contrôles : {r['manual_checks']}"]
        (self.root/'validation_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
