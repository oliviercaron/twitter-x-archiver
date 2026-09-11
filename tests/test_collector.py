import asyncio
import copy
import gzip
import io
import json
from pathlib import Path
import httpx
import pyarrow.parquet as pq
import pytest
from PIL import Image
from twitter_x_archiver.archive import Store
from twitter_x_archiver.collector import Collector, config_load, plan, single_writer
from twitter_x_archiver.media import Downloader, original_image
from twitter_x_archiver.normalize import normalize, raw_objects, reference_only


def fixture():
    user={'__typename':'User','rest_id':'42','legacy':{'screen_name':'synthetic_author','name':'Synthetic fixture',
        'created_at':'Thu Jan 01 00:00:00 +0000 2015','description':'Synthetic test data','location':'',
        'followers_count':100,'friends_count':10,'statuses_count':50,'favourites_count':7,'listed_count':2,'media_count':5,
        'profile_image_url_https':'https://pbs.twimg.com/profile_images/synthetic.jpg'}}
    variants=[{'content_type':'application/x-mpegURL','url':'https://video.twimg.com/a.m3u8'},
              {'content_type':'video/mp4','bitrate':256000,'url':'https://video.twimg.com/low.mp4'},
              {'content_type':'video/mp4','bitrate':2176000,'url':'https://video.twimg.com/high.mp4'}]
    tweet={'__typename':'Tweet','rest_id':'1234567890123456789','legacy':{
        'created_at':'Fri Sep 04 16:00:00 +0000 2026','full_text':'ZEVENT Mastu test synthétique 😭',
        'user_id_str':'42','lang':'fr','conversation_id_str':'1234567890123456789',
        'reply_count':2,'retweet_count':3,'favorite_count':10,'quote_count':1,'bookmark_count':4,
        'entities':{'hashtags':[{'text':'ZEVENT'}],'user_mentions':[],'urls':[]},
        'extended_entities':{'media':[
            {'id_str':'501','media_key':'3_501','type':'photo','media_url_https':'https://pbs.twimg.com/media/photo.jpg',
             'original_info':{'width':1920,'height':1080},'ext_alt_text':'image synthétique'},
            {'id_str':'502','media_key':'7_502','type':'video','media_url_https':'https://pbs.twimg.com/media/preview.jpg',
             'video_info':{'duration_millis':2000,'aspect_ratio':[16,9],'variants':variants},'mediaStats':{'viewCount':'400'}},
            {'id_str':'503','type':'animated_gif','media_url_https':'https://pbs.twimg.com/media/gif.jpg',
             'video_info':{'variants':[{'content_type':'video/mp4','url':'https://video.twimg.com/gif.mp4'}]}}
        ]}},'views':{'count':'1000'},'core':{'user_results':{'result':user}}}
    return {'data':{'search_by_raw_query':{'search_timeline':{'timeline':{'instructions':[{
        'entries':[{'entryId':'tweet-'+tweet['rest_id'],'content':{'itemContent':{'tweet_results':{'result':tweet}}}}]
    }]}}}}}


def row(payload=None, observed='2026-09-07T12:00:00+00:00', query='ZEVENT'):
    tweets,users=raw_objects(payload or fixture())
    return normalize(next(iter(tweets.values())),users,None,observed,query,'0.20.1',['Mastu'],'raw/test.json.gz')


def make_config(tmp_path):
    root=Path(__file__).parents[1]
    import yaml
    cfg=yaml.safe_load((root/'src/twitter_x_archiver/defaults/config.yaml').read_text())
    cfg.update(timezone='Europe/Paris',start='2026-09-03T00:00:00',end_exclusive='2026-09-09T00:00:00',slice_hours=24,data_dir='data',include_streamers=False,pause_seconds=0,queries=['ZEVENT'],streamers_file='streamers.json')
    (tmp_path/'streamers.json').write_text(json.dumps({'streamers':[]}))
    (tmp_path/'config.yaml').write_text(yaml.safe_dump(cfg))
    return config_load(tmp_path/'config.yaml')


def test_all_variants_multiple_media_and_timezone():
    r=row()
    assert (r['photo_count'],r['video_count'],r['gif_count'])==(1,1,1)
    m=r['media'][1]
    assert m['n_video_variants']==3 and m['best_video_url'].endswith('high.mp4')
    assert m['media_variants'][0]['content_type']=='application/x-mpegURL'
    assert r['created_at_utc']=='2026-09-04T16:00:00+00:00'
    assert r['created_at_paris']=='2026-09-04T18:00:00+02:00'
    assert r['total_interactions']==16 and r['engagement_per_view']==.016
    assert r['streamer_name_match']==['Mastu']


def test_full_objects_survive_duplicate_identity_references():
    payload=fixture();tweets,users=raw_objects(payload)
    full=next(iter(tweets.values()));user=next(iter(users.values()))
    short={'__typename':'Tweet','rest_id':full['rest_id']}
    short_user={'__typename':'User','rest_id':user['rest_id']}
    for objects in ([full,short,short_user],[short,short_user,full]):
        found,authors=raw_objects(objects)
        assert found[full['rest_id']] is full
        assert authors[user['rest_id']] is user


def test_missing_metrics_are_null_not_zero():
    p=fixture(); tweets,_=raw_objects(p); t=next(iter(tweets.values()))
    del t['legacy']['favorite_count']; del t['views']
    r=row(p)
    assert r['like_count'] is None and r['view_count'] is None
    assert r['total_interactions'] is None and r['engagement_per_view'] is None


def test_v2_user_fields_and_null_legacy():
    p=fixture(); tweets,users=raw_objects(p); t=next(iter(tweets.values())); u=users['42']
    t.update(t.pop('legacy')); t['legacy']=None
    u['legacy']=None
    u.update(core={'screen_name':'new_user','name':'New','created_at':'2020-01-01T00:00:00Z'},
             relationship_counts={'followers':77,'following':3},tweet_counts={'tweets':12,'media_tweets':2},
             privacy={'protected':False},verification={'verified':True},profile_bio={'description':'bio'})
    r=row(p)
    assert r['author_username']=='new_user' and r['author_followers_count']==77
    assert r['author_listed_count'] is None and r['author_tweet_count']==12


def test_upsert_snapshots_queries_and_originals(tmp_path):
    s=Store(tmp_path)
    a=row(); s.upsert(a); s.db.commit()
    b=row(observed='2026-09-08T12:00:00+00:00',query='#ZEVENT'); b['like_count']=20
    s.upsert(b); s.db.commit()
    c=s.get(a['tweet_id'])
    assert c['collection_queries']==['ZEVENT','#ZEVENT']
    assert len(c['metrics_snapshots'])==2 and c['metrics_snapshots'][0]['like_count']==10
    assert c['like_count']==20 and len(list(s.rows()))==1
    s.export({}); out=pq.read_table(tmp_path/'zevent2026_tweets.parquet').to_pylist()
    assert len(out)==1 and len(json.loads(out[0]['media']))==3
    assert json.loads(out[0]['raw_tweet'])['legacy']['full_text']==a['raw_content']
    s.db.close()


def test_schema_union_preserves_late_fields(tmp_path):
    s=Store(tmp_path); a=row(); s.upsert(a)
    b=row(); b['tweet_id']='999'; b['new_field']='keep me'; s.upsert(b); s.db.commit(); s.export({})
    assert 'new_field' in pq.read_schema(tmp_path/'zevent2026_tweets.parquet').names
    s.db.close()


def test_raw_archive_is_replayable_gzip(tmp_path):
    s=Store(tmp_path); e=s.archive(fixture(),'ZEVENT','collect')
    with gzip.open(tmp_path/e['raw_ref'],'rt',encoding='utf-8') as f:
        loaded=json.load(f)
    assert loaded['response']==fixture() and loaded['query']=='ZEVENT'
    s.db.close()


def test_window_exact_boundaries_and_queries(tmp_path):
    cfg=make_config(tmp_path); qs,_,slices,_=plan(cfg)
    assert cfg['_start'].isoformat()=='2026-09-02T22:00:00+00:00'
    assert cfg['_end_exclusive'].isoformat()=='2026-09-08T22:00:00+00:00'
    assert len(slices)==6 and 'since:2026-09-02 until:2026-09-04' in slices[0]['query']


def test_ingest_actual_twscrape_parser_and_resume(tmp_path):
    async def run():
        c=Collector(make_config(tmp_path)); env=c.store.archive(fixture(),'ZEVENT','smoke')
        stats=await c.ingest(env)
        assert stats['kept']==1 and c.successful_parses==1 and c.parse_failures==0
        assert (await c.ingest(env))['kept']==0
        assert len(list(c.store.rows()))==1
        saved=next(c.store.rows())
        assert len(saved['parsed_tweet']['media']['videos'][0]['variants'])==2
        assert len(saved['media'][1]['media_variants'])==3
        c.store.db.close()
    asyncio.run(run())


def test_parser_failure_keeps_tweet_and_raw(tmp_path):
    async def run():
        c=Collector(make_config(tmp_path)); p=fixture(); _,users=raw_objects(p)
        del users['42']['legacy']['created_at']
        # Remove tweet's required timestamp: raw extraction must preserve an undated observation.
        tweets,_=raw_objects(p); del next(iter(tweets.values()))['legacy']['created_at']
        e=c.store.archive(p,'ZEVENT','collect'); await c.ingest(e)
        saved=next(c.store.rows()); assert saved['created_at_utc'] is None and saved['raw_tweet']
        assert c.parse_failures==1 and saved['within_collection_window'] is None
        c.store.db.close()
    asyncio.run(run())


def test_quote_media_archived_even_outside_window(tmp_path):
    async def run():
        c=Collector(make_config(tmp_path)); p=fixture(); ts,_=raw_objects(p); main=next(iter(ts.values()))
        quoted=copy.deepcopy(main); quoted['rest_id']='888'; quoted['legacy']['created_at']='Wed Jan 01 00:00:00 +0000 2025'
        main['legacy']['extended_entities']['media']=[]
        main['quoted_status_result']={'result':quoted}; main['legacy']['quoted_status_id_str']='888'
        await c.ingest(c.store.archive(p,'ZEVENT','collect'))
        rows=list(c.store.rows()); assert len(rows)==1
        assert rows[0]['has_video'] and rows[0]['media'][0]['source_tweet_id']=='888'
        assert rows[0]['media'][0]['media_relation']=='quote'
        c.store.db.close()
    asyncio.run(run())


def test_restricted_reply_tweet_is_not_invisible(tmp_path):
    """X enveloppe un post a reponses limitees ; le post reel n'a pas de __typename."""
    async def run():
        payload = fixture()
        entry = payload['data']['search_by_raw_query']['search_timeline']['timeline']['instructions'][0]['entries'][0]
        inner = entry['content']['itemContent']['tweet_results']['result']
        ident = inner['rest_id']
        del inner['__typename']
        entry['content']['itemContent']['tweet_results']['result'] = {
            '__typename': 'TweetWithVisibilityResults',
            'limitedActionResults': {'limited_actions': [{'action': 'Reply'}]},
            'tweet': inner}
        tweets, _ = raw_objects(payload)
        assert ident in tweets and not reference_only(tweets[ident])

        c = Collector(make_config(tmp_path))
        await c.ingest(c.store.archive(payload, 'ZEVENT', 'collect'))
        rows = list(c.store.rows())
        assert len(rows) == 1 and rows[0]['tweet_id'] == ident
        assert rows[0]['raw_content'] and rows[0]['author_username'] == 'synthetic_author'
        assert rows[0]['media_count'] == 3          # les medias suivent l'enveloppe
        assert rows[0]['is_context_tweet'] is False  # il est bien l'objet de l'entree
        c.store.db.close()
    asyncio.run(run())


def test_visibility_envelope_without_payload_is_ignored():
    payload = fixture()
    entry = payload['data']['search_by_raw_query']['search_timeline']['timeline']['instructions'][0]['entries'][0]
    entry['content']['itemContent']['tweet_results']['result'] = {
        '__typename': 'TweetWithVisibilityResults'}      # enveloppe vide
    tweets, _ = raw_objects(payload)
    assert tweets == {}


def test_quoted_context_columns_extracted_from_embedded_object():
    p=fixture(); ts,users=raw_objects(p); main=next(iter(ts.values()))
    quoted=copy.deepcopy(main); quoted['rest_id']='888'
    quoted['legacy'].update({'full_text':'Post cité','user_id_str':'99','favorite_count':500,
                             'in_reply_to_status_id_str':'777','conversation_id_str':'777'})
    quoted['core']['user_results']['result']=copy.deepcopy(users['42'])
    quoted['core']['user_results']['result'].update({'rest_id':'99'})
    quoted['core']['user_results']['result']['legacy']['screen_name']='autre_compte'
    # X sometimes wraps the embedded object in a visibility envelope.
    main['quoted_status_result']={'result':{'__typename':'TweetWithVisibilityResults','tweet':quoted}}
    main['legacy']['quoted_status_id_str']='888'
    row=normalize(main,users,None,'2026-09-08T00:00:00+00:00','ZEVENT','x',[],None)
    assert row['is_quote'] and not row['is_reply']
    assert row['quoted_context_available'] and row['quoted_tweet_id_observed']=='888'
    assert row['quoted_author_username']=='autre_compte' and row['quoted_author_id']=='99'
    assert row['quoted_text']=='Post cité' and row['quoted_like_count']==500
    assert row['quoted_is_reply'] and row['quoted_reply_to_tweet_id']=='777'
    assert row['quoted_video_count']==1 and row['is_self_quote'] is False
    assert row['reposted_context_available'] is False


def test_missing_quote_context_is_absent_not_zero():
    p=fixture(); ts,users=raw_objects(p); main=next(iter(ts.values()))
    # Reference-only object: X returned the identity without a payload.
    main['quoted_status_result']={'result':{'__typename':'Tweet','rest_id':'888'}}
    main['legacy']['quoted_status_id_str']='888'
    row=normalize(main,users,None,'2026-09-08T00:00:00+00:00','ZEVENT','x',[],None)
    assert row['is_quote'] and row['quoted_tweet_id']=='888'
    assert row['quoted_context_available'] is False
    assert row['is_self_quote'] is None
    assert not any(k.startswith('quoted_') and k not in
                   ('quoted_tweet_id','quoted_context_available') for k in row)


def png_bytes():
    b=io.BytesIO(); Image.new('RGB',(12,8),'red').save(b,format='PNG'); return b.getvalue()


def test_download_url_and_hash_dedup_and_retry(tmp_path):
    async def run():
        calls=[]
        def handler(req):
            calls.append(str(req.url))
            return httpx.Response(200,content=png_bytes(),headers={'content-type':'image/png'})
        s=Store(tmp_path); d=Downloader(s,{'image_hashes':True,'pause_seconds':0},httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        a=await d.download('https://pbs.twimg.com/a.png',Path('media/images/1_00.png'))
        b=await d.download('https://pbs.twimg.com/a.png',Path('media/images/2_00.png'))
        c=await d.download('https://pbs.twimg.com/b.png',Path('media/images/3_00.png'))
        assert len(calls)==2 and a['sha256']==c['sha256']
        assert a['local_media_path']==b['local_media_path']==c['local_media_path']
        assert a['actual_width']==12 and a['phash'] and a['download_success']
        assert len(list((tmp_path/'media/images').glob('*.png')))==1
        await d.close(); s.db.close()
    asyncio.run(run())


def test_media_rejects_html_and_external_redirect(tmp_path):
    async def run():
        responses=[httpx.Response(200,content=b'<html>error</html>'),httpx.Response(302,headers={'location':'http://127.0.0.1/secret'})]
        s=Store(tmp_path); d=Downloader(s,{},httpx.AsyncClient(transport=httpx.MockTransport(lambda r:responses.pop(0))))
        a=await d.download('https://video.twimg.com/test.mp4',Path('media/videos/a.mp4'),video=True)
        b=await d.download('https://pbs.twimg.com/test.jpg',Path('media/images/a.jpg'))
        assert not a['download_success'] and not b['download_success']
        assert b['download_error']=='unsupported_redirect_host'
        assert not list(tmp_path.rglob('*.partial'))
        await d.close(); s.db.close()
    asyncio.run(run())


def test_original_photo_url():
    assert original_image('https://pbs.twimg.com/media/a?format=png&name=small')=='https://pbs.twimg.com/media/a?format=png&name=orig'


def test_rehydrate_uncertain_keeps_availability(tmp_path):
    class Rep:
        status_code=200
        def json(self): return {'data':{}}
    class API:
        async def tweet_details_raw(self, ident): return Rep()
    async def run():
        c=Collector(make_config(tmp_path)); c.store.upsert(row()); c.store.db.commit(); c.api=API()
        await c.rehydrate(); assert next(c.store.rows())['tweet_currently_available'] is True
        c.store.db.close()
    asyncio.run(run())


def test_rehydrate_explicit_unavailable_preserves_files(tmp_path):
    class Rep:
        status_code=200
        def json(self): return {'errors':[{'code':144,'message':'No status found with that ID.'}]}
    class API:
        async def tweet_details_raw(self, ident): return Rep()
    async def run():
        c=Collector(make_config(tmp_path)); c.store.upsert(row()); c.store.db.commit(); c.api=API()
        await c.rehydrate(); saved=next(c.store.rows())
        assert saved['tweet_currently_available'] is False and saved['raw_content'] and len(saved['media'])==3
        c.store.db.close()
    asyncio.run(run())


def test_failed_search_checkpoint_is_not_completed(tmp_path):
    class API:
        async def search_raw(self,*args,**kwargs):
            raise ConnectionError('synthetic'); yield
    async def run():
        c=Collector(make_config(tmp_path)); c.api=API()
        with pytest.raises(ConnectionError): await c.search_slice(c.slices[0])
        assert c.store.state('query:'+c.slices[0]['key'])['status']=='interrupted_retry_from_slice_start'
        c.store.db.close()
    asyncio.run(run())


def test_single_writer_releases_lock(tmp_path):
    with single_writer(tmp_path):
        with pytest.raises(RuntimeError):
            with single_writer(tmp_path): pass
    with single_writer(tmp_path): pass


def test_full_search_consumes_all_pages_without_limit(tmp_path):
    class Rep:
        status_code=200
        def __init__(self, ident): self.ident=ident
        def json(self):
            p=fixture(); ts,_=raw_objects(p); next(iter(ts.values()))['rest_id']=self.ident
            return p
    class API:
        async def search_raw(self,q,limit,kv):
            assert limit==-1 and kv['product']=='Latest'
            for ident in ['1001','1002','1003']:
                yield Rep(ident)
        def _get_cursor(self,payload): return 'synthetic_cursor'
    async def run():
        c=Collector(make_config(tmp_path)); c.api=API(); item=c.slices[1]
        result=await c.search_slice(item)
        assert result['pages']==3 and result['status']=='exhausted_returned_results'
        assert len(list(c.store.rows()))==3
        # Repeating a completed query remains idempotent while archiving fresh observations.
        await c.search_slice(item)
        assert len(list(c.store.rows()))==3
        c.store.db.close()
    asyncio.run(run())


def test_partial_response_marks_carried_forward_values(tmp_path):
    s=Store(tmp_path); s.upsert(row()); s.db.commit()
    b=row(observed='2026-09-08T12:00:00+00:00'); b['view_count']=None
    s.upsert(b); s.db.commit(); latest=next(s.rows())
    assert latest['view_count']==1000 and 'view_count' in latest['carried_forward_fields']
    assert latest['metrics_snapshots'][-1]['view_count'] is None
    s.db.close()


def test_older_replay_does_not_revert_availability(tmp_path):
    s=Store(tmp_path); r=row(); s.upsert(r); s.db.commit()
    saved=next(s.rows()); saved.update(tweet_currently_available=False,availability_observed_at='2026-09-09T00:00:00+00:00')
    s.save(saved); s.db.commit(); s.upsert(row(observed='2026-09-08T00:00:00+00:00')); s.db.commit()
    assert next(s.rows())['tweet_currently_available'] is False
    s.db.close()


def test_empty_parquet_has_documented_columns(tmp_path):
    s=Store(tmp_path); s.export({})
    schema=pq.read_schema(tmp_path/'zevent2026_tweets.parquet')
    assert {'tweet_id','media','view_count','author_username','metrics_snapshots'}.issubset(schema.names)
    assert pq.read_table(tmp_path/'zevent2026_tweets.parquet').num_rows==0
    s.db.close()


def test_lost_database_download_reuses_receipt(tmp_path):
    async def run():
        s=Store(tmp_path); calls=[]
        def handle(req):
            calls.append(1); return httpx.Response(200,content=png_bytes())
        d=Downloader(s,{'image_hashes':False},httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        a=await d.download('https://pbs.twimg.com/recovery.png',Path('media/images/receipt.png'))
        s.db.execute('DELETE FROM files'); s.db.commit()
        b=await d.download('https://pbs.twimg.com/recovery.png',Path('media/images/receipt.png'))
        assert len(calls)==1 and a['sha256']==b['sha256'] and b['reused']
        await d.close(); s.db.close()
    asyncio.run(run())


def test_real_mp4_download_and_ffprobe(tmp_path):
    import shutil
    import subprocess
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg/ffprobe optional dependency unavailable')
    source=tmp_path/'synthetic_source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10',
                    '-t','1','-c:v','mpeg4','-y',str(source)],check=True,capture_output=True)
    content=source.read_bytes()
    async def run():
        s=Store(tmp_path/'archive')
        d=Downloader(s,{'ffprobe':'ffprobe'},httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req:httpx.Response(200,content=content,headers={'content-type':'video/mp4'}))))
        result=await d.download('https://video.twimg.com/synthetic.mp4',Path('media/videos/1_00.mp4'),video=True)
        assert result['download_success'] and result['validation_status']=='valid'
        assert result['actual_width']==160 and result['actual_height']==90 and result['actual_fps']==10
        assert abs(result['actual_duration_seconds']-1)<.1 and result['actual_codec']=='mpeg4'
        assert (s.root/result['local_media_path']).read_bytes()==content
        await d.close(); s.db.close()
    asyncio.run(run())


def test_pipeline_requires_all_probe_results(tmp_path):
    from twitter_x_archiver.run_pipeline import probe_ready
    s=Store(tmp_path)
    s.put_state('query:probe_a',{'base_query':'ZEVENT','status':'sampled_not_complete'})
    assert not probe_ready(tmp_path,['ZEVENT','#ZEVENT'])
    s.put_state('query:probe_b',{'base_query':'#ZEVENT','status':'exhausted_returned_results'})
    assert probe_ready(tmp_path,['ZEVENT','#ZEVENT'])
    s.db.close()


def test_pipeline_does_not_start_after_failed_probe(tmp_path):
    from twitter_x_archiver.run_pipeline import probe_ready
    s=Store(tmp_path)
    s.put_state('query:probe_a',{'base_query':'ZEVENT','status':'interrupted_retry_from_slice_start'})
    assert not probe_ready(tmp_path,['ZEVENT'])
    s.db.close()


def test_identifier_only_tweet_has_unknown_availability(tmp_path):
    async def run():
        c=Collector(make_config(tmp_path))
        p={'data':{'tweet':{'__typename':'Tweet','rest_id':'9000'}}}
        await c.ingest(c.store.archive(p,'ZEVENT','probe'))
        saved=c.store.get('9000')
        assert saved['record_completeness']=='reference_only'
        assert saved['tweet_currently_available'] is None and saved['parse_error'] is None
        assert saved['availability_history'][0]['available'] is None
        c.store.db.close()
    asyncio.run(run())


def test_reference_only_does_not_replace_known_availability(tmp_path):
    s=Store(tmp_path); original=row(); s.upsert(original); s.db.commit()
    partial=normalize({'__typename':'Tweet','rest_id':original['tweet_id']},{},None,
                      '2026-09-08T12:00:00+00:00','ZEVENT','0.20.1',[],'raw/ref.json.gz')
    s.upsert(partial); s.db.commit(); saved=s.get(original['tweet_id'])
    assert saved['tweet_currently_available'] is True
    assert saved['availability_observed_at']==original['availability_observed_at']
    assert saved['availability_history'][-1]['available'] is None
    assert saved['raw_content']==original['raw_content']
    s.db.close()
