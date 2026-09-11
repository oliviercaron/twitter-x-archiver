#!/usr/bin/env python3
"""Loopback-only bridge between the unpacked Chrome extension and the archive."""
import asyncio
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import subprocess
from contextlib import ExitStack
from .storage_manager import StorageManager
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs,unquote,urlsplit
from .archive import atomic_json, now
from .collector import config_load,single_writer
from .session_store import forget_session, session_present, write_session
from .summaries import summaries
from . import categories
from . import summary_index
from . import remove_post
from .manual_archive import Jobs,ManualWorker
from .export_service import ExportManager
from .export_results import display_path
from . import app_paths

# Depuis un executable, l'interface embarquee et les donnees de l'utilisateur
# ne sont pas au meme endroit.
def server_platform():
    """Windows, WSL ou POSIX : un PID ne se cherche pas au meme endroit."""
    if os.name == 'nt':
        return 'windows'
    try:
        if 'microsoft' in Path('/proc/version').read_text(errors='ignore').lower():
            return 'wsl'
    except OSError:
        pass
    return 'posix'


ROOT=app_paths.home()
ASSETS=app_paths.assets()
ADDRESS=('127.0.0.1',18765)
EXTENSION_ORIGIN=re.compile(
    r'^(?:chrome-extension://[a-p]{32}|(?:moz-extension|safari-web-extension)://'
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$', re.I)


def account_status(data):
    """Expose quota timing only; never load cookies, headers or credentials."""
    path=Path(data)/'accounts.db'
    if not path.exists():return {'account_active':None,'quota_wait':False}
    try:
        with sqlite3.connect(path) as db:
            rows=db.execute('SELECT active,locks FROM accounts').fetchall()
        active=[locks for enabled,locks in rows if enabled]
        reset=[]
        for value in active:
            lock=json.loads(value or '{}').get('TweetDetail')
            if lock:
                date=datetime.fromisoformat(lock)
                if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
                reset.append(date)
        # The project uses a single account. Missing locks mean no recorded wait.
        until=min(reset) if reset and len(reset)==len(active) else None
        return {'account_active':bool(active),'quota_wait':bool(until and until>datetime.now(timezone.utc)),
                'quota_reset_at':until.isoformat() if until else None}
    except (sqlite3.Error,ValueError,TypeError,AttributeError):
        return {'account_active':None,'quota_wait':False}


def secret_token(data):
    path=data/'secrets_bridge.json'
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))['token']
    token=secrets.token_urlsafe(32)
    atomic_json(path,{'token':token,'created_at':now()})
    try:path.chmod(0o600)
    except OSError:pass  # Windows ACLs govern the mounted NTFS directory.
    return token


MEDIA_TYPES={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp',
            '.gif':'image/gif','.mp4':'video/mp4','.webm':'video/webm','.m4a':'audio/mp4'}
CHUNK=256*1024


def make_handler(jobs,token,stop=None,storage=None,gate=None,pause=None):
    gate = gate or threading.RLock()
    exports=ExportManager(jobs.path.parent)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def allowed_host(self):
            return self.headers.get('Host') in ('127.0.0.1:18765','localhost:18765')

        def allowed_origin(self):
            origin=self.headers.get('Origin')
            return not origin or origin in ('http://127.0.0.1:18765','http://localhost:18765') or bool(EXTENSION_ORIGIN.fullmatch(origin))

        def authorized(self):
            return self.allowed_host() and self.allowed_origin() and hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token)

        def respond(self,code,value,ctype='application/json; charset=utf-8'):
            content=value if isinstance(value,bytes) else value.encode('utf-8') if isinstance(value,str) else json.dumps(value,ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type',ctype)
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            origin=self.headers.get('Origin')
            if origin and self.allowed_origin():
                self.send_header('Access-Control-Allow-Origin',origin)
                self.send_header('Vary','Origin')
                self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
                self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Private-Network','true')
            self.end_headers();self.wfile.write(content)

        def send_media(self,target,kind):
            """Envoie un fichier, entier ou par intervalle.

            Sans reponse 206, un lecteur video ne peut pas se deplacer dans la
            piste : le navigateur redemande le fichier depuis le debut. Le
            contenu est envoye par tranches, jamais charge entierement en
            memoire, une video pesant plusieurs dizaines de megaoctets.
            """
            size=target.stat().st_size
            start,end=0,size-1
            asked=self.headers.get('Range','')
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',asked.strip()) if asked else None
            if match:
                first,last=match.group(1),match.group(2)
                if first:
                    start=int(first)
                    if last:end=min(int(last),size-1)
                elif last:
                    start=max(0,size-int(last))          # suffixe : les N derniers octets
                if start>end or start>=size:
                    self.send_response(416)
                    self.send_header('Content-Range',f'bytes */{size}')
                    self.send_header('Content-Length','0')
                    return self.end_headers()
            length=end-start+1
            self.send_response(206 if match else 200)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(length))
            self.send_header('Accept-Ranges','bytes')
            if match:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            # Un media archive ne change jamais : le relire a chaque
            # rafraichissement de la liste serait du gaspillage pur.
            self.send_header('Cache-Control','private, max-age=31536000, immutable')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            if self.command=='HEAD':return
            with target.open('rb') as source:
                source.seek(start)
                while length>0:
                    block=source.read(min(CHUNK,length))
                    if not block:break
                    try:
                        self.wfile.write(block)
                    except (BrokenPipeError,ConnectionResetError):
                        return                          # lecteur ferme en cours de route
                    length-=len(block)

        def do_OPTIONS(self):
            self.respond(204,'') if self.allowed_host() and self.allowed_origin() else self.respond(403,{'error':'forbidden'})

        def do_GET(self):
            if urlsplit(self.path).path in ('/health','/','/ui.js','/ui.css','/strings.js'):
                return self.get_content()
            if urlsplit(self.path).path == '/api/storage' and storage is not None:
                if not self.authorized():return self.respond(403,{'error':'pairing_required'})
                state=storage.view()
                if state['status']=='done' and str(storage.source)!=state['target']:state['status']='switching'
                state['path']=display_path(Path(state['path']))
                if state.get('target'):state['target']=display_path(Path(state['target']))
                return self.respond(200,state)
            with gate:
                if storage and ((pause is not None and pause.is_set()) or storage.view()['status'] in ('pending','copying','verifying') or jobs.path.parent != storage.source):
                    return self.respond(503,{'error':'storage_busy'})
                return self.get_content()

        def get_content(self):
            if not self.allowed_host():return self.respond(403,{'error':'invalid_host'})
            path=urlsplit(self.path).path
            if path=='/health':return self.respond(200,{'service':'zevent-manual','protocol':1})
            if path=='/api/paths':
                # L'interface affiche les vrais chemins plutot que ceux d'une machine.
                data_dir=jobs.path.parent
                return self.respond(200,{'project':display_path(ROOT),'data':display_path(data_dir),
                                         'extension':display_path(app_paths.extension_path()),
                                         'posts':display_path(data_dir/'posts')})
            if path=='/':
                html=(ASSETS/'ui/index.html').read_text(encoding='utf-8').replace('__BRIDGE_TOKEN__',token)
                return self.respond(200,html,'text/html; charset=utf-8')
            if path.startswith('/media/'):
                # Le chemin est resolu puis verifie a l'interieur du dossier des
                # medias, jamais concatene, et seuls ces types sortent.
                root=(jobs.path.parent/'media').resolve()
                try:
                    target=(jobs.path.parent/unquote(path[1:])).resolve()
                    target.relative_to(root)
                except (ValueError,OSError):
                    return self.respond(403,{'error':'forbidden'})
                kind=MEDIA_TYPES.get(target.suffix.lower())
                if not kind or not target.is_file():
                    return self.respond(404,{'error':'not_found'})
                return self.send_media(target,kind)
            if path in ('/ui.js','/ui.css','/strings.js'):
                return self.respond(200,(ASSETS/'ui'/path[1:]).read_text(encoding='utf-8'),
                    'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
            if not self.authorized():return self.respond(403,{'error':'pairing_required'})
            if path=='/api/results':return self.respond(200,exports.view())
            if path=='/api/summaries':return self.respond(200,summaries(jobs.path.parent))
            if path=='/api/posts':
                query=parse_qs(urlsplit(self.path).query)
                one=lambda k,d='': (query.get(k) or [d])[0]
                statut=one('statut')
                selected=None
                if statut in ('echec','cours'):
                    # Ces etats vivent dans la file, pas dans l'archive, et sont
                    # rares par nature : la liste d'identifiants reste courte.
                    wanted={'echec':('retry','partial','unavailable'),'cours':('queued','fetching')}[statut]
                    selected=[i for i,st in jobs.statuses().items() if st in wanted]
                try:
                    page=max(1,int(one('page','1')))
                except ValueError:
                    page=1
                with sqlite3.connect(f'file:{jobs.path.parent}/collection.db?mode=ro',uri=True) as db:
                    result=summary_index.search(db,q=one('q'),author=one('compte'),
                                                kind=one('type'),ids=selected,
                                                sort=one('tri','recent'),page=page,
                                                since=one('du'),until=one('au'),
                                                category=one('categorie'))
                    result['authors']=summary_index.authors(db)
                    result['categories']=summary_index.categories(db)
                    result['span']=summary_index.span(db)
                # Le statut vient de la file, pas de l'archive : une recherche
                # dans une table ne peut pas savoir ce qui reste a reprendre.
                known=jobs.statuses()
                for item in result['items']:
                    item['status']=known.get(item['id'],'done')
                return self.respond(200,result)
            if path=='/api/categories':
                with sqlite3.connect(f'file:{jobs.path.parent}/collection.db?mode=ro',uri=True) as db:
                    known=summary_index.categories(db)
                return self.respond(200,{'categories':known,'default':categories.DEFAUT})
            if path=='/api/states':return self.respond(200,jobs.statuses())
            if path=='/api/jobs':return self.respond(200,{'jobs':jobs.all(),'service_status':{**account_status(jobs.path.parent),'session_present':session_present(jobs.path.parent)}})
            if re.fullmatch(r'/api/jobs/\d{1,20}',path):
                job=jobs.get(path.rsplit('/',1)[-1])
                return self.respond(200,job) if job else self.respond(404,{'error':'not_found'})
            return self.respond(404,{'error':'not_found'})

        def do_POST(self):
            if not self.authorized():return self.respond(403,{'error':'pairing_required'})
            if self.path=='/api/shutdown':return self.post_content()
            with gate:
                if storage and ((pause is not None and pause.is_set()) or storage.view()['status'] in ('pending','copying','verifying') or jobs.path.parent != storage.source):
                    return self.respond(409,{'error':'storage_busy'})
                return self.post_content()

        def post_content(self):
            if not self.authorized():return self.respond(403,{'error':'pairing_required'})
            if self.path not in ('/api/archive','/api/results','/api/results/choose-folder','/api/session','/api/session/forget','/api/shutdown','/api/delete','/api/category','/api/storage/open','/api/storage/choose-folder','/api/storage/move'):return self.respond(404,{'error':'not_found'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.respond(415,{'error':'json_required'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=16384:return self.respond(413,{'error':'request_too_large'})
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError()
                if self.path.startswith('/api/storage/'):
                    if storage is None or pause is None:return self.respond(409,{'error':'storage_unavailable'})
                    if self.path=='/api/storage/open':
                        folder=str(storage.source)
                        if os.name=='nt':os.startfile(folder)
                        elif sys.platform=='darwin':subprocess.Popen(['open',folder])
                        elif server_platform()=='wsl':subprocess.Popen(['explorer.exe',display_path(storage.source)])
                        else:subprocess.Popen(['xdg-open',folder])
                        return self.respond(200,{'opened':True})
                    if self.path=='/api/storage/choose-folder':return self.respond(200,exports.choose(storage=True))
                    if exports.thread and exports.thread.is_alive():
                        return self.respond(409,{'error':'export_running','message':'Attendez la fin de l’export avant de changer de dossier.'})
                    state=storage.request(body.get('destination',''))
                    pause.set()
                    return self.respond(202,state)
                if self.path=='/api/delete':
                    ident=str(body.get('tweet_id') or '')
                    if not re.fullmatch(r'\d{1,20}',ident):raise ValueError()
                    data_dir=jobs.path.parent
                    plan=remove_post.plan(data_dir,ident)
                    shared=plan['shared_media']+plan['shared_raw']
                    if shared and not body.get('force'):
                        # Un media ou une reponse brute partages appartiennent
                        # aussi a un autre post : les detruire l'abimerait.
                        # Nommer ces voisins permet a l'utilisateur de decider.
                        others=sorted({o for r in plan['shared_raw'] for o in r['used_by']}
                                      | remove_post.sharers(data_dir,ident))
                        return self.respond(409,{'error':'shared','shared':len(shared),
                                                 'with':others[:5],'others':len(others)})
                    remove_post.apply(plan,data_dir,bool(body.get('force')),backup=False)
                    left=remove_post.verify(data_dir,ident)
                    return self.respond(200,{'deleted':ident,'left':left})
                if self.path=='/api/category':
                    ident=str(body.get('tweet_id') or '')
                    if not re.fullmatch(r'\d{1,20}',ident):raise ValueError()
                    wanted=categories.clean(body.get('category'))
                    # La file d'abord. Un archivage encore en cours ecrit sa
                    # propre categorie en fin de course : sans cette mise a
                    # jour, il effacerait le rangement qu'on vient de faire.
                    job=jobs.get(ident)
                    if job is not None:
                        jobs.update(job,selection={**(job.get('selection') or {}),'category':wanted})
                    # Le document ensuite, s'il existe deja. Le service est seul
                    # a ecrire dans la base : ranger ne passe pas par un outil
                    # exterieur.
                    db=sqlite3.connect(jobs.path.parent/'collection.db',timeout=20)
                    try:
                        db.execute('BEGIN IMMEDIATE')
                        found=db.execute('SELECT doc FROM tweets WHERE id=?',(ident,)).fetchone()
                        if found:
                            row=json.loads(found[0]);row['category']=wanted
                            db.execute('UPDATE tweets SET doc=? WHERE id=?',
                                       (json.dumps(row,ensure_ascii=False,sort_keys=True),ident))
                            summary_index.put(db,ident,row)
                            db.commit()
                        else:
                            db.rollback()
                    finally:
                        db.close()
                    # Ni post archive ni archivage en cours : il n'y a rien a ranger.
                    if not found and job is None:
                        return self.respond(404,{'error':'not_found'})
                    return self.respond(200,{'tweet_id':ident,'category':wanted})
                if self.path=='/api/shutdown':
                    # Sortie propre : le worker termine sa tache, ferme la base
                    # et ecrit son statut. Tuer le processus sauterait tout cela.
                    if stop is None:return self.respond(409,{'error':'shutdown_unavailable'})
                    self.respond(202,{'stopping':True})
                    return stop.set()
                if self.path=='/api/session':
                    # La reponse ne renvoie jamais les valeurs recues.
                    write_session(jobs.path.parent,body.get('auth_token'),body.get('ct0'))
                    return self.respond(200,{'session_present':True})
                if self.path=='/api/session/forget':
                    forget_session(jobs.path.parent)
                    return self.respond(200,{'session_present':session_present(jobs.path.parent)})
                if self.path=='/api/results/choose-folder':return self.respond(200,exports.choose())
                if self.path=='/api/results':return self.respond(202,exports.start(body['destination'],body.get('include_automatic') is True,
                                                                                    body.get('categories') or None))
                job=jobs.enqueue(body['url'],mode=body.get('mode','manual_extension'),note=body.get('note',''),
                    source=body.get('source'),refresh=body.get('refresh',False) is True,
                    include_replies=body.get('include_replies') is True,category=body.get('category',''))
                return self.respond(202,job)
            except OSError:
                return self.respond(400,{'error':'folder_unavailable'})
            except (ValueError,KeyError,TypeError) as exc:
                if self.path.startswith('/api/storage/'):
                    return self.respond(400,{'error':'storage_request_rejected','message':str(exc) if isinstance(exc,ValueError) else 'Dossier invalide.'})
                # Aucun detail : le corps refuse peut contenir des cookies.
                return self.respond(400,{'error':'session_rejected' if self.path.startswith('/api/session') else 'invalid_tweet_id' if self.path=='/api/delete' else 'invalid_post_url' if self.path=='/api/archive' else 'export_request_rejected'})
    return Handler


def main():
    config=config_load(app_paths.ensure_config())
    data=app_paths.resolve_data_dir(config)
    data.mkdir(parents=True,exist_ok=True)
    gate=threading.RLock()
    stop=threading.Event()
    pause=threading.Event()
    # Keep both locks until shutdown; the old archive remains a safe retained copy.
    with ExitStack() as locks:
        locks.enter_context(single_writer(data))
        def commit(target):
            locks.enter_context(single_writer(target))
            app_paths.set_data_dir(target)
        storage=StorageManager(data,preference_writer=commit)
        jobs=Jobs(data);jobs.recover();token=secret_token(data)
        server=ThreadingHTTPServer(ADDRESS,make_handler(jobs,token,stop,storage,gate,pause))
        app_paths.set_data_dir(data)
        server.daemon_threads=True
        threading.Thread(target=server.serve_forever,daemon=True).start()
        started_at=now()
        def status(folder,state):
            atomic_json(folder/'manual_server_status.json',{'pid':os.getpid(),'platform':server_platform(),
                        'updated_at':now(),'started_at':started_at,'address':'http://127.0.0.1:18765','status':state})
        status(data,'running')
        print('Local archive ready: http://127.0.0.1:18765',flush=True)
        class WorkerStop:
            def is_set(self):return stop.is_set() or pause.is_set()
        async def run():
            nonlocal data,jobs
            while not stop.is_set():
                config['data_dir']=str(data)
                worker=ManualWorker(config,jobs)
                try:await worker.drain(WorkerStop())
                finally:await worker.close()
                if stop.is_set():break
                if pause.is_set():
                    # Requests return busy while pending; only progress remains available.
                    await asyncio.to_thread(storage.perform)
                    with gate:
                        if storage.view()['status']=='done':
                            status(data,'stopped')
                            data=Path(storage.view()['target'])
                            storage.activate(data)
                            jobs=Jobs(data);jobs.recover()
                            server.RequestHandlerClass=make_handler(jobs,token,stop,storage,gate,pause)
                            status(data,'running')
                        pause.clear()
        try:asyncio.run(run())
        except KeyboardInterrupt:pass
        finally:
            stop.set();server.shutdown();server.server_close()
            status(data,'stopped')


def dispatch(argv):
    """Un seul programme, trois roles.

    Chrome lance l'hote de messagerie native par un chemin unique, sans pouvoir
    lui passer d'argument : le lanceur ecrit a l'installation ajoute le mode.
    """
    if argv and argv[0]=='--open':
        from . import server_control
        import webbrowser
        result=server_control.start()
        if result.get('ok'):webbrowser.open('http://127.0.0.1:18765')
        else:print('The local app could not start. Check the archive folder and try again.')
        return 0 if result.get('ok') else 1
    if argv and argv[0]=='--self-test':
        from . import runtime_check
        return runtime_check.main()
    if argv and argv[0]=='--native-host':
        from .native_host import host
        return host.main()
    if argv and argv[0] in ('--install','--uninstall'):
        from . import install_native_host, server_control
        import webbrowser
        code=install_native_host.main(['--uninstall'] if argv[0]=='--uninstall' else [])
        if argv[0]=='--install' and code==0:
            print()
            print("One browser setup step remains:")
            print("  1. open chrome://extensions and enable Developer mode")
            print("  2. click Load unpacked")
            print(f"  3. select the folder {app_paths.extension_path()}")
            print()
            print("Starting the local app and opening its page.")
            server_control.start()
            webbrowser.open('http://127.0.0.1:18765')
        return code
    return main()


def cli(argv=None):
    """Start the service or run one of its local maintenance commands."""
    try:
        return dispatch(sys.argv[1:] if argv is None else argv) or 0
    except app_paths.StoragePathError as exc:
        print(str(exc));return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f'The service stopped ({type(exc).__name__}). Close any other running copy and try again.')
        return 1


if __name__=='__main__':
    raise SystemExit(cli())
