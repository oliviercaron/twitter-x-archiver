#!/usr/bin/env python3
"""Archive one X/Twitter post URL per line, without keyword/date filtering."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
import httpx
from . import app_paths
from .archive import atomic_json, now
from .collector import config_load, single_writer
from .manual_archive import Jobs, ManualWorker, canonical_url

ROOT = Path(__file__).resolve().parent


def read_urls(path):
    accepted,invalid,seen = [],[],set()
    with Path(path).open(encoding='utf-8-sig') as f:
        for n,line in enumerate(f,1):
            value = line.strip()
            if not value or value.startswith('#'):continue
            try:
                ident,url = canonical_url(value)
            except ValueError:
                invalid.append({'line':n,'error':'invalid_post_url'})
                continue
            if ident not in seen:
                seen.add(ident);accepted.append({'url':url,'line':n})
    return accepted,invalid


def bridge_available():
    try:
        with httpx.Client(trust_env=False,timeout=2) as c:
            r=c.get('http://127.0.0.1:18765/health')
            return r.status_code==200 and r.json().get('service')=='zevent-manual'
    except (httpx.HTTPError,ValueError):return False


async def run_direct(config,jobs):
    jobs.recover()
    worker=ManualWorker(config,jobs)
    try:await worker.drain()
    finally:await worker.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path,help='UTF-8 text, one post URL per line')
    parser.add_argument('--config',default=None)
    parser.add_argument('--refresh',action='store_true',help='New metric snapshot even when already manually archived')
    parser.add_argument('--inclure-replies','--include-replies',dest='include_replies',action='store_true',help='Archive available replies and their reply chains')
    args=parser.parse_args()
    config=config_load(args.config or app_paths.ensure_config())
    data=(config['_root']/config['data_dir']).resolve() if args.config else app_paths.resolve_data_dir(config)
    config['data_dir']=str(data)
    data.mkdir(parents=True,exist_ok=True)
    urls,invalid=read_urls(args.file)
    if not urls:
        print('No valid post URLs. Invalid lines:',len(invalid));return 2
    jobs=Jobs(data)
    server=bridge_available()
    ids=[]
    if server:
        secret=json.loads((data/'secrets_bridge.json').read_text())['token']
        with httpx.Client(trust_env=False,timeout=15,headers={'Authorization':'Bearer '+secret}) as client:
            for item in urls:
                r=client.post('http://127.0.0.1:18765/api/archive',json={'url':item['url'],'mode':'manual_txt',
                    'source':{'file':str(args.file.resolve()),'line':item['line']},'refresh':args.refresh,'include_replies':args.include_replies})
                r.raise_for_status();ids.append(r.json()['tweet_id'])
    else:
        with single_writer(data):
            for item in urls:
                job=jobs.enqueue(item['url'],mode='manual_txt',source={'file':str(args.file.resolve()),'line':item['line']},refresh=args.refresh,include_replies=args.include_replies)
                ids.append(job['tweet_id'])
            asyncio.run(run_direct(config,jobs))
    summary={'observed_at':now(),'source_file':str(args.file.resolve()),'valid_unique_urls':len(urls),'invalid_lines':invalid,
             'submitted_to_running_server':server,'jobs':[jobs.get(i) for i in ids]}
    path=data/'manual_imports'/(now().replace(':','-')+'.json')
    atomic_json(path,summary)
    print(f"{len(ids)} unique URLs submitted; {len(invalid)} invalid lines.")
    print('Status:', {state:sum(j['status']==state for j in summary['jobs']) for state in sorted({j['status'] for j in summary['jobs']})})
    print('Report:',path)
    if server:print('The local service is processing the queue. Follow progress: http://127.0.0.1:18765')
    return 1 if not server and any(j['status']!='done' for j in summary['jobs']) else 0


if __name__=='__main__':
    try:sys.exit(main())
    except KeyboardInterrupt:print('Interrupted; the queue has been saved.');sys.exit(130)
    except Exception as exc:print('Failed:',type(exc).__name__,'; no credentials shown.');sys.exit(1)
