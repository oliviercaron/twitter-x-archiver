import asyncio
import copy
import json
from pathlib import Path
from twitter_x_archiver.discussion_archive import Discussion
from twitter_x_archiver.manual_archive import Jobs, ManualWorker
from twitter_x_archiver.normalize import raw_objects
from test_collector import fixture, make_config


def tweet(ident,parent=None,conversation='100'):
    raw=copy.deepcopy(next(iter(raw_objects(fixture())[0].values())))
    raw['rest_id']=str(ident)
    raw['legacy'].update(conversation_id_str=conversation,full_text='Reply fixture '+str(ident),
        created_at='Wed Jan 01 16:00:00 +0000 2025',extended_entities={'media':[]})
    if parent:raw['legacy']['in_reply_to_status_id_str']=str(parent)
    return raw


class Rep:
    status_code=200
    def __init__(self,*tweets,more=False):
        self.payload={'tweets':list(tweets)}
        if more:self.payload['cursor']={'cursorType':'ShowMoreThreads','value':'test_cursor'}
    def json(self):return self.payload


def test_recursive_multilevel_metadata_and_unrelated_exclusion(tmp_path):
    class API:
        calls=[]
        async def tweet_details_raw(self,ident,kv=None):
            if kv is None:return Rep(tweet('100'))
            pages=[p async for p in self.tweet_replies_raw(ident)]
            return pages[min(1 if kv.get('cursor') else 0,len(pages)-1)]
        async def tweet_replies_raw(self,ident,limit=-1):
            self.calls.append(ident)
            if ident==100:
                yield Rep(tweet('101','100'),tweet('999','998','998'),more=True)
                yield Rep(tweet('102','101'))
            elif ident==102:yield Rep(tweet('103','102'))
            else:yield Rep(tweet(str(ident),'100' if ident==101 else '102'))
    async def run():
        jobs=Jobs(tmp_path/'data');jobs.enqueue('https://x.com/a/status/100',include_replies=True)
        worker=ManualWorker(make_config(tmp_path),jobs);worker.ready=True;worker.collector.api=API()
        result=await worker.process(jobs.next())
        assert result['status']=='done'
        summary=result['result']['discussion']
        assert summary['reply_count_archived']==3 and summary['max_depth_observed']==3 and summary['exhaustive'] is False
        assert set(API.calls)=={100,101,102,103}
        assert {r['tweet_id'] for r in worker.collector.store.rows()}=={'100','101','102','103'}
        child=worker.collector.store.get('103')
        assert child['reply_to_tweet_id']=='102' and child['author_id']=='42'
        assert child['within_collection_window'] is False and child['discussion_roots']==['100']
        assert not child.get('manual_selections')
        assert (tmp_path/'data/posts/103/tweet.json').exists()
        assert len(json.loads((tmp_path/'data/discussions/100/edges.json').read_text()))==3
        await worker.close()
    asyncio.run(run())


def test_failure_resume_and_unverified_pagination(tmp_path):
    class API:
        fail=True
        calls=[]
        async def tweet_details_raw(self,ident,kv=None):
            if kv is None:return Rep(tweet('100'))
            pages=[p async for p in self.tweet_replies_raw(ident)]
            return pages[min(1 if kv.get('cursor') else 0,len(pages)-1)]
        async def tweet_replies_raw(self,ident,limit=-1):
            self.calls.append(ident)
            if ident==100:yield Rep(tweet('101','100'))
            elif self.fail:raise RuntimeError('SECRET_MUST_NOT_BE_EXPORTED')
            else:yield Rep(tweet('101','100'),more=True)
    async def run():
        jobs=Jobs(tmp_path/'data');jobs.enqueue('https://x.com/a/status/100',include_replies=True)
        worker=ManualWorker(make_config(tmp_path),jobs);worker.ready=True;api=API();worker.collector.api=api
        a=await worker.process(jobs.next());assert a['status']=='partial'
        api.fail=False;api.calls=[]
        jobs.enqueue('https://x.com/a/status/100',include_replies=True)
        b=await worker.process(jobs.next());assert b['status']=='partial'
        assert set(api.calls)=={101}  # Root branch already traversed; failed child resumed.
        saved=(tmp_path/'data/discussions/100/state.json').read_text()
        assert 'SECRET_MUST_NOT_BE_EXPORTED' not in saved
        assert json.loads(saved)['branches']['101']['status']=='pagination_unverified'
        await worker.close()
    asyncio.run(run())


def test_subthread_child_before_parent_and_missing_parent(tmp_path):
    async def run():
        jobs=Jobs(tmp_path/'data');job=jobs.enqueue('https://x.com/a/status/200',include_replies=True)
        worker=ManualWorker(make_config(tmp_path),jobs);c=worker.collector
        seed=c.store.archive(Rep(tweet('200','100')).json(),'root','manual_url',selection=job['selection'])
        await c.ingest(seed,rehydrate_id='200')
        discussion=Discussion(c,job)
        await discussion.accept(seed)
        # Same conversation sibling must not be mistaken for a descendant of the selected reply.
        a=c.store.archive(Rep(tweet('202','201'),tweet('999','100')).json(),'page1','manual_discussion',selection=job['selection'])
        await discussion.accept(a)
        assert c.store.get('202') is None and c.store.get('999') is None
        b=c.store.archive(Rep(tweet('201','200')).json(),'page2','manual_discussion',selection=job['selection'])
        await discussion.accept(b)
        result=discussion.export('partial')
        assert result['reply_count_archived']==2 and result['max_depth_observed']==2
        assert c.store.get('202')['reply_to_tweet_id']=='201'
        await worker.close()
    asyncio.run(run())


def test_queue_upgrade_and_concurrent_option_is_not_lost(tmp_path):
    jobs=Jobs(tmp_path)
    jobs.enqueue('https://x.com/a/status/100');old=jobs.next()
    jobs.enqueue('https://x.com/a/status/100',include_replies=True)
    result=jobs.update(old,status='done',result={'media_count':0})
    assert result['status']=='queued' and result['include_replies']
    done=jobs.update(jobs.next(),status='done',result={'discussion':{'status':'finished_available_pages'}})
    assert jobs.enqueue(done['url'],include_replies=True)['status']=='done'
    jobs.enqueue('https://x.com/a/status/300');jobs.update(jobs.next(),status='done',result={})
    assert jobs.enqueue('https://x.com/a/status/300',include_replies=True)['status']=='queued'


def test_changing_cursors_without_new_replies_stop_conservatively(tmp_path):
    class API:
        calls=0
        async def tweet_details_raw(self,ident,kv=None):
            if kv is None:return Rep(tweet('100'))
            self.calls+=1
            rep=Rep(tweet('100'))
            rep.payload['cursor']={'cursorType':'Bottom','value':'new-cursor-'+str(self.calls)}
            return rep
    async def run():
        jobs=Jobs(tmp_path/'data');jobs.enqueue('https://x.com/a/status/100',include_replies=True)
        worker=ManualWorker(make_config(tmp_path),jobs);worker.ready=True;api=API();worker.collector.api=api
        result=await worker.process(jobs.next())
        assert api.calls==3 and result['status']=='partial'
        state=json.loads((tmp_path/'data/discussions/100/state.json').read_text())
        assert state['branches']['100']['stop_reason']=='three_pages_without_new_replies'
        assert result['result']['discussion']['exhaustive'] is False
        await worker.close()
    asyncio.run(run())


def test_bottom_cursor_page_and_terminal_empty_response(tmp_path):
    class API:
        calls=[]
        async def tweet_details_raw(self,ident,kv=None):
            if kv is None:return Rep(tweet('100'))
            cursor=kv.get('cursor');self.calls.append((ident,cursor))
            if ident==100 and not cursor:
                rep=Rep(tweet('100'));rep.payload['cursor']={'cursorType':'Bottom','value':'next'};return rep
            if ident==100 and cursor=='next':return Rep(tweet('101','100'))
            return Rep()
    async def run():
        jobs=Jobs(tmp_path/'data');jobs.enqueue('https://x.com/a/status/100',include_replies=True)
        worker=ManualWorker(make_config(tmp_path),jobs);worker.ready=True;api=API();worker.collector.api=api
        result=await worker.process(jobs.next())
        assert result['status']=='done' and result['result']['discussion']['reply_count_archived']==1
        assert (100,'next') in api.calls and (101,None) in api.calls
        await worker.close()
    asyncio.run(run())
