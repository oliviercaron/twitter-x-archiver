"""Archive the accessible reply tree, with durable traversal and explicit coverage limits."""
import asyncio
import gzip
import hashlib
import json
from archive import atomic_json, now
from normalize import raw_objects, reference_only


def continuations(value):
    found=[]
    if isinstance(value,dict):
        if value.get('cursorType') in ('ShowMoreThreads','Bottom','ShowMore') and value.get('value'):
            found.append(value['value'])
        for v in value.values():found.extend(continuations(v))
    elif isinstance(value,list):
        for v in value:found.extend(continuations(v))
    return list(dict.fromkeys(v for v in found if isinstance(v,str)))


class Discussion:
    def __init__(self,collector,job,progress=None):
        self.c=collector;self.job=job;self.root=job['tweet_id']
        self.progress=progress
        self.folder=collector.store.root/'discussions'/self.root
        self.path=self.folder/'state.json'
        self.state=json.loads(self.path.read_text()) if self.path.exists() else {
            'root_id':self.root,'created_at':now(),'nodes':[self.root],
            'candidates':{},'branches':{self.root:{'status':'queued'}},'pages':{}}
        self.conversation=(collector.store.get(self.root) or {}).get('conversation_id')
        self.retry_media_ids=list(self.state['nodes']) if self.path.exists() else []
        if job.get('refresh'):
            for ident in self.state['branches']:self.state['branches'][ident]={'status':'queued'}

    def save(self):
        self.state['updated_at']=now();atomic_json(self.path,self.state)

    def load_page(self,ref):
        with gzip.open(self.c.store.root/ref,'rt',encoding='utf-8') as f:env=json.load(f)
        env['raw_ref']=ref
        return env

    async def accept(self,env):
        tweets,_=raw_objects(env['response'])
        ref=env['raw_ref']
        self.state['pages'].setdefault(ref,{'processed':[]})
        self.state['pages'][ref]['tweet_ids']=[i for i,r in tweets.items() if not reference_only(r)]
        for ident,raw in tweets.items():
            if reference_only(raw):continue
            legacy={**raw,**(raw.get('legacy') or {})};parent=legacy.get('in_reply_to_status_id_str')
            if ident==self.root or not parent:continue
            conversation=legacy.get('conversation_id_str')
            if conversation and self.conversation and conversation!=self.conversation:continue
            self.state['candidates'][ident]={'parent_id':str(parent),'conversation_id':conversation}
        known=set(self.state['nodes'])
        while True:
            found={i for i,r in self.state['candidates'].items() if i not in known and
                   (r['parent_id'] in known or (self.conversation==self.root and r['conversation_id']==self.root))}
            if not found:break
            known.update(found)
        self.state['nodes']=sorted(known)
        for ident in known:self.state['branches'].setdefault(ident,{'status':'queued'})
        # Missing parents in a root conversation are explicitly requested too.
        if self.conversation==self.root:
            for ident in known-{self.root}:
                parent=self.state['candidates'][ident]['parent_id']
                self.state['branches'].setdefault(parent,{'status':'queued'})
        self.save()  # Raw page and discovered branches durable before media requests.
        # Reconsider earlier pages: a child's parent can appear on a later page.
        for page_ref,record in self.state['pages'].items():
            if 'tweet_ids' in record and not (set(record['tweet_ids']) & known)-set(record['processed']):
                continue
            page=env if page_ref==ref else self.load_page(page_ref)
            objects,_=raw_objects(page['response'])
            record['tweet_ids']=[i for i,r in objects.items() if not reference_only(r)]
            ids={i for i in objects if i in known and i not in record['processed'] and not reference_only(objects[i])}
            if not ids:continue
            digest=hashlib.sha256(','.join(sorted(ids)).encode()).hexdigest()[:16]
            derived={**page,'page_id':page['page_id']+'_discussion_'+digest,'mode':'manual_discussion'}
            await self.c.ingest(derived,selected_ids=ids,discussion_root=self.root)
            for ident in ids:
                row=self.c.store.get(ident)
                if row:
                    destination=self.c.store.root/'posts'/ident
                    atomic_json(destination/'tweet.json',row)
                    atomic_json(destination/'snapshots'/(derived['page_id']+'.json'),row)
            record['processed']=sorted(set(record['processed'])|ids)
            self.save()

    def export(self,status):
        rows=[self.c.store.get(i) for i in self.state['nodes']]
        rows=[r for r in rows if r]
        available={r['tweet_id'] for r in rows};parents={r['tweet_id']:r.get('reply_to_tweet_id') for r in rows}
        edges=[]
        for ident in sorted(available-{self.root}):
            current=ident;seen=set();depth=0
            while current!=self.root and current in parents and current not in seen:
                seen.add(current);current=parents[current];depth+=1
            edges.append({'parent_id':parents[ident],'child_id':ident,
                          'depth':depth if current==self.root else None,'parent_missing':parents[ident] not in available})
        media_failures=sum(bool(not m.get('download',{}).get('download_success') or
            (m.get('thumbnail_url') and not m.get('thumbnail_download',{}).get('download_success')))
            for r in rows for m in r.get('media',[]))
        metadata_missing=sum(not r.get('author_id') or r.get('record_completeness')!='payload_present' for r in rows)
        incomplete=[i for i,b in self.state['branches'].items() if b['status']!='pagination_ended']
        missing=[e['parent_id'] for e in edges if e['parent_missing']]
        unlinked=[e['child_id'] for e in edges if e['depth'] is None]
        if status!='running' and (incomplete or unlinked or media_failures or metadata_missing):status='partial'
        result={'root_id':self.root,'status':status,'observed_at':now(),'exhaustive':False,
                'coverage_note':'Only responses returned by X; finished pagination does not prove completeness.',
                'reply_count_archived':len(available-{self.root}),'branches_total':len(self.state['branches']),
                'branches_finished':len(self.state['branches'])-len(incomplete),'branches_incomplete':incomplete,
                'missing_parent_ids':sorted(set(missing)),'media_failures':media_failures,'metadata_missing':metadata_missing,
                'unresolved_depth_ids':unlinked,
                'max_depth_observed':max((e['depth'] for e in edges if e['depth'] is not None),default=0),
                'path':str(self.folder.relative_to(self.c.store.root)).replace('\\','/')}
        atomic_json(self.folder/'tweets.json',rows)
        atomic_json(self.folder/'edges.json',edges)
        atomic_json(self.folder/'summary.json',result)
        if self.progress:self.progress(result)
        return result

    async def run(self,seed):
        await self.accept(seed)
        if self.c.downloader:
            for ident in self.retry_media_ids:
                row=self.c.store.get(ident)
                if row and any(not m.get('download',{}).get('download_success') or
                    (m.get('thumbnail_url') and not m.get('thumbnail_download',{}).get('download_success')) for m in row.get('media',[])):
                    await self.c.downloader.enrich(row)
                    atomic_json(self.c.store.root/'posts'/ident/'tweet.json',self.c.store.get(ident))
            self.c.export()
        attempted=set()
        self.export('running')
        try:
            while True:
                pending=[i for i,b in self.state['branches'].items() if b['status']!='pagination_ended' and i not in attempted]
                if not pending:break
                ident=pending[0];attempted.add(ident)
                branch={'status':'fetching','started_at':now(),'page_count':0}
                self.state['branches'][ident]=branch;self.save()
                try:
                    # Request pages ourselves: the library's reply iterator only follows
                    # ShowMoreThreads and can silently discard terminal empty pages.
                    pending_cursors=[None];seen=set();repeated=False
                    seen_reply_ids=set();pages_without_new_replies=0
                    while pending_cursors:
                        cursor=pending_cursors.pop(0);seen.add(cursor)
                        kv={'referrer':'tweet','includePromotedContent':False}
                        if cursor is not None:kv['cursor']=cursor
                        rep=await self.c.api.tweet_details_raw(int(ident),kv=kv)
                        if rep is None:raise RuntimeError('response_unavailable')
                        payload=rep.json()
                        env=self.c.store.archive(payload,'tweet_replies:'+ident,'manual_discussion',True,selection=self.job['selection'])
                        branch['page_count']+=1;branch['last_raw_ref']=env['raw_ref']
                        if rep.status_code!=200 or payload.get('errors'):raise RuntimeError('x_page_error')
                        tokens=continuations(payload)
                        fresh=[t for t in tokens if t not in seen and t not in pending_cursors]
                        if tokens and not fresh and not pending_cursors:repeated=True
                        pending_cursors.extend(fresh)
                        branch['pending_cursor_count']=len(pending_cursors)
                        branch['cursor_repeated']=repeated
                        await self.accept(env)
                        objects,_=raw_objects(payload)
                        reply_ids=(set(objects)&set(self.state['nodes']))-{self.root}
                        if reply_ids-seen_reply_ids:
                            pages_without_new_replies=0
                        else:
                            pages_without_new_replies+=1
                        seen_reply_ids.update(reply_ids)
                        branch['pages_without_new_replies']=pages_without_new_replies
                        if pending_cursors and pages_without_new_replies>=3:
                            # X can issue endlessly changing cursors for repeated/filtered pages.
                            # Stop conservatively, keep the raw pages and mark coverage uncertain.
                            branch['stop_reason']='three_pages_without_new_replies'
                            repeated=True
                            pending_cursors.clear()
                        if pending_cursors:await asyncio.sleep(self.c.config.get('pause_seconds',2))
                    branch['status']='pagination_unverified' if repeated else 'pagination_ended'
                except asyncio.CancelledError:
                    branch['status']='interrupted';self.save();raise
                except Exception as exc:
                    branch.update(status='retry',error_class=type(exc).__name__)
                    self.save()
                    # A quota/auth/network error affects subsequent branches too. Resume on request.
                    break
                branch['finished_at']=now();self.save();self.export('running')
                await asyncio.sleep(self.c.config.get('pause_seconds',2))
        finally:
            self.save();self.export('partial')
        return self.export('finished_available_pages')
