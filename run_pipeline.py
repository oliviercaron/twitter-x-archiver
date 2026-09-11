#!/usr/bin/env python3
"""Launch smoke -> query probes -> collection; persist phase and child PID.

Optional --after-probe-pid resumes after an already-running probe on Linux/WSL.
No cookies or other secrets are passed as arguments.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
import yaml
from archive import atomic_json, now

ROOT = Path(__file__).resolve().parent


def probe_ready(data, expected_queries):
    con = sqlite3.connect('file:'+str(data/'collection.db')+'?mode=ro',uri=True)
    try:
        states = [json.loads(x[0]) for x in con.execute("SELECT doc FROM state WHERE key LIKE 'query:probe_%'")]
        by_query = {s['base_query']:s for s in states}
        allowed = {'sampled_not_complete','exhausted_returned_results','provisional'}
        return all(q in by_query and by_query[q]['status'] in allowed for q in expected_queries)
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--after-probe-pid',type=int)
    args = parser.parse_args()
    cfg = yaml.safe_load((ROOT/'config.yaml').read_text(encoding='utf-8'))
    data = ROOT/cfg['data_dir']
    state = {'pid':os.getpid(),'started_at':now(),'status':'running','phase':'starting'}
    def save(**changes):
        state.update(changes,updated_at=now())
        atomic_json(data/'pipeline_status.json',state)
    save()
    try:
        if args.after_probe_pid:
            if sys.platform != 'linux':
                raise RuntimeError('Waiting for an existing process requires Linux/WSL')
            save(phase='waiting_for_existing_probe',child_pid=args.after_probe_pid)
            expected = b'collect_zevent2026.py'
            while True:
                try:
                    cmd = Path(f'/proc/{args.after_probe_pid}/cmdline').read_bytes()
                except FileNotFoundError:
                    break
                if expected not in cmd or b'probe' not in cmd:
                    break
                time.sleep(5)
            if not probe_ready(data,cfg['queries']):
                save(status='failed',phase='probe_incomplete',child_pid=None)
                return 3
        else:
            for mode in ('smoke','probe'):
                child = subprocess.Popen([sys.executable,str(ROOT/'collect_zevent2026.py'),'--mode',mode],cwd=ROOT)
                save(phase=mode,child_pid=child.pid)
                code = child.wait()
                if code:
                    save(status='failed',exit_code=code)
                    return code
        child = subprocess.Popen([sys.executable,str(ROOT/'collect_zevent2026.py')],cwd=ROOT)
        save(phase='collect',child_pid=child.pid)
        code = child.wait()
        save(status='finished' if code==0 else 'failed',exit_code=code,child_pid=None)
        return code
    except BaseException as exc:
        save(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',error_class=type(exc).__name__)
        raise


if __name__=='__main__':
    sys.exit(main())
