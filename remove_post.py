#!/usr/bin/env python3
"""Supprimer integralement un post archive : base, medias, archives brutes, file.

Rien ne doit subsister d'un post retire. Le script inventorie d'abord tout ce
qui le concerne, affiche le detail, puis supprime.

Deux garde-fous, parce que « tout supprimer » ne doit jamais vouloir dire
« abimer un autre post » :

- un fichier media partage avec un autre post (meme SHA-256 ou meme chemin,
  cas frequent d'une citation ou d'un re-upload) n'est pas supprime ;
- une archive brute qui sert de provenance a une autre ligne n'est pas
  supprimee : une reponse GraphQL contient souvent plusieurs tweets.

Ces cas sont signales explicitement et font sortir en code 2. `--force-shared`
les supprime quand meme, apres avoir liste ce qui sera perdu au passage.

Ecrire dans la base pendant que le serveur tourne la corrompt (verrous POSIX
de WSL invisibles depuis Windows). Le script refuse donc de s'executer si le
serveur est actif ; `--stop-server` l'arrete et le relance autour.
"""
import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent

# Regles d'URL identiques a manual_archive.canonical_url, reproduites ici pour
# que l'outil de maintenance reste utilisable meme sans la pile de collecte.
HOSTS = {'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com', 'mobile.x.com'}
POST = re.compile(r'^/(?:[A-Za-z0-9_]{1,50}|i/web)/status/(\d{1,20})(?:/(?:photo|video)/\d+)?/?$')


def post_id(value):
    value = str(value).strip()
    if value.isdigit():
        return value
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or p.hostname not in HOSTS or p.username or p.password or p.port:
        raise ValueError('URL de post X/Twitter attendue')
    m = POST.fullmatch(p.path)
    if not m or not 0 < int(m.group(1)) < 2 ** 64:
        raise ValueError('URL de post X/Twitter attendue')
    return m.group(1)


# --------------------------------------------------------------------------- serveur

import server_control                                    # noqa: E402


def server_pid(data):
    """PID du serveur s'il tourne vraiment, sinon None."""
    state = server_control.status(data)
    return state['pid'] if state['running'] and state['pid'] else None


def stop_server(pid):
    return server_control.stop()


def start_server():
    return server_control.start()


# --------------------------------------------------------------------------- inventaire

def load(db):
    return {i: json.loads(d) for i, d in db.execute('SELECT id, doc FROM tweets')}


def media_paths(row):
    """Chemins et empreintes des fichiers rattaches a une ligne."""
    for item in (row.get('media') or []):
        for holder in ('download', 'thumbnail_download'):
            d = item.get(holder) or {}
            if d.get('local_media_path'):
                yield d['local_media_path'], d.get('sha256'), item.get('media_relation')


def plan(data, ident):
    db = sqlite3.connect(f'file:{data/"collection.db"}?mode=ro', uri=True)
    rows = load(db)
    row = rows.get(ident)
    others = {i: r for i, r in rows.items() if i != ident}

    mine_paths = {p: (sha, rel) for p, sha, rel in media_paths(row)} if row else {}
    other_paths, other_sha = set(), set()
    for r in others.values():
        for p, sha, _ in media_paths(r):
            other_paths.add(p)
            if sha:
                other_sha.add(sha)

    media, shared_media = [], []
    for path, (sha, relation) in mine_paths.items():
        target = shared_media if (path in other_paths or (sha and sha in other_sha)) else media
        target.append({'path': path, 'sha256': sha, 'relation': relation})

    raw, shared_raw = [], []
    for ref in (row.get('raw_response_refs') or []) if row else []:
        users = [i for i, r in others.items() if ref in (r.get('raw_response_refs') or [])]
        (shared_raw if users else raw).append({'ref': ref, 'used_by': users})

    receipts = [k for k, doc in db.execute('SELECT key, doc FROM files')
                if (json.loads(doc).get('local_media_path') or '') in mine_paths]
    pages = [Path(r['ref']).name.replace('.json.gz', '') for r in raw + shared_raw]
    db.close()

    job = None
    queue = data / 'manual_queue.db'
    if queue.exists():
        q = sqlite3.connect(f'file:{queue}?mode=ro', uri=True)
        found = q.execute('SELECT status FROM jobs WHERE tweet_id=?', (ident,)).fetchone()
        job = found[0] if found else None
        q.close()

    parquet = data / 'zevent2026_tweets.parquet'
    in_parquet = False
    if parquet.exists():
        import pyarrow.parquet as pq
        in_parquet = ident in set(pq.read_table(parquet, columns=['tweet_id'])
                                  .column('tweet_id').to_pylist())

    # Fichiers nommes d'apres le post mais qu'aucune ligne ne reference plus.
    # Tous les sous-dossiers de media sont balayes : images, videos, thumbnails
    # et tout dossier ajoute plus tard.
    stray = []
    for f in (data / 'media').rglob(f'{ident}_*'):
        if not f.is_file():
            continue
        rel = f.relative_to(data).as_posix()
        if rel not in mine_paths and rel.removesuffix('.json') not in mine_paths:
            stray.append(rel)

    exports = []
    for csv in sorted((ROOT / 'resultats').glob('*/tweets.csv')):
        if ident in csv.read_text(encoding='utf-8-sig', errors='ignore'):
            exports.append(csv.parent.name)

    return {'ident': ident, 'row': row, 'job': job, 'media': media, 'shared_media': shared_media,
            'raw': raw, 'shared_raw': shared_raw, 'receipts': receipts, 'pages': pages,
            'posts_dir': (data / 'posts' / ident), 'in_parquet': in_parquet,
            'stray': sorted(stray), 'exports': exports}


def sharers(data, ident):
    """Autres posts qui utilisent un fichier de celui-ci."""
    db = sqlite3.connect(f'file:{data/"collection.db"}?mode=ro', uri=True)
    try:
        rows = load(db)
    finally:
        db.close()
    mine = {p for p, _, _ in media_paths(rows.get(ident, {}))}
    if not mine:
        return set()
    return {other for other, row in rows.items() if other != ident
            and any(p in mine for p, _, _ in media_paths(row))}


def render(p, data):
    ident = p['ident']
    print(f'=== {ident} ===')
    if p['row']:
        r = p['row']
        print(f"  ligne     @{r.get('author_username')} | {r.get('created_at_utc')} | "
              f"{(r.get('raw_content') or '')[:60]!r}")
    else:
        print('  ligne     absente de la base')
    print(f"  job       {p['job'] or 'absent de la file'}")
    print(f"  parquet   {'present' if p['in_parquet'] else 'absent'}")
    print(f"  dossier   {'posts/' + ident if p['posts_dir'].exists() else 'aucun'}")
    print(f"  recus     {len(p['receipts'])} entrees de la table files")
    for m in p['media']:
        exists = (data / m['path']).exists()
        print(f"  media     {m['path']}  [{m['relation']}] {'' if exists else '(fichier absent)'}")
    for s in p['stray']:
        print(f"  orphelin  {s}  (nomme d'apres le post, plus reference)")
    for r in p['raw']:
        print(f"  brut      {r['ref']}")
    for m in p['shared_media']:
        print(f"  PARTAGE   {m['path']}  -> egalement utilise par un autre post")
    for r in p['shared_raw']:
        print(f"  PARTAGE   {r['ref']}  -> provenance de {', '.join(r['used_by'])}")
    if p['exports']:
        print(f"  exports   present dans : {', '.join(p['exports'])}")


def apply(p, data, force_shared, backup=True):
    """Supprime. Sans sauvegarde, l'operation est definitive : c'est le mode
    utilise par l'interface, ou l'utilisateur a confirme explicitement."""
    ident = p['ident']
    if backup:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        for name in ('collection.db', 'manual_queue.db'):
            if (data / name).exists():
                shutil.copy2(data / name, data / f'{Path(name).stem}.{stamp}.avant_suppression.db')
        print(f'  sauvegardes : data/*.{stamp}.avant_suppression.db')

    db = sqlite3.connect(data / 'collection.db')
    db.execute('DELETE FROM tweets WHERE id=?', (ident,))
    try:
        import summary_index
        summary_index.drop(db, ident)      # sinon le post survivrait dans la consultation
    except sqlite3.Error:
        pass
    for key in p['receipts']:
        db.execute('DELETE FROM files WHERE key=?', (key,))
    pages = [Path(r['ref']).name.replace('.json.gz', '') for r in p['raw']]
    if force_shared:
        pages += [Path(r['ref']).name.replace('.json.gz', '') for r in p['shared_raw']]
    for page in pages:
        db.execute('DELETE FROM pages WHERE id=?', (page,))
    db.commit()
    print('  integrite :', db.execute('PRAGMA integrity_check').fetchone()[0])
    db.close()

    if p['job'] is not None:
        q = sqlite3.connect(data / 'manual_queue.db')
        q.execute('DELETE FROM jobs WHERE tweet_id=?', (ident,))
        q.commit(); q.close()
        print('  job retire de la file')

    targets = [data / m['path'] for m in p['media']]
    targets += [data / (m['path'] + '.json') for m in p['media']]
    targets += [data / s for s in p['stray']]
    targets += [data / r['ref'] for r in p['raw']]
    if force_shared:
        targets += [data / m['path'] for m in p['shared_media']]
        targets += [data / (m['path'] + '.json') for m in p['shared_media']]
        targets += [data / r['ref'] for r in p['shared_raw']]
    targets.append(p['posts_dir'])

    removed = 0
    for t in targets:
        if t.is_dir():
            shutil.rmtree(t); removed += 1; print(f'  supprime : {t.relative_to(data)}/')
        elif t.exists():
            # Sous Windows, un fichier encore lu par un lecteur video resiste un
            # court instant. Quelques tentatives valent mieux qu'un echec sec.
            for attempt in range(5):
                try:
                    t.unlink(); removed += 1; print(f'  supprime : {t.relative_to(data)}')
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.3)
    print(f'  {removed} fichiers/dossiers supprimes')

    if p['in_parquet']:
        import pyarrow as pa, pyarrow.parquet as pq, pyarrow.compute as pc, os
        src = data / 'zevent2026_tweets.parquet'
        t = pq.read_table(src)
        keep = pc.invert(pc.is_in(t.column('tweet_id'), value_set=pa.array([ident])))
        out = t.filter(keep).replace_schema_metadata(t.schema.metadata)
        pq.write_table(out, str(src) + '.partial', compression='zstd')
        os.replace(str(src) + '.partial', src)
        print(f'  parquet : {t.num_rows} -> {out.num_rows} lignes')


BACKUP_SUFFIXES = ('.avant_suppression.db', '.bak.db', '.CORROMPUE.db', '.corrompue_ecartee')


def is_backup(path):
    """Les sauvegardes contiennent le post par construction : ce ne sont pas des residus."""
    name = path.name
    return any(name.endswith(s) for s in BACKUP_SUFFIXES)


def verify(data, ident):
    """Aucun residu ne doit subsister apres coup, hors sauvegardes."""
    left = []
    db = sqlite3.connect(f'file:{data/"collection.db"}?mode=ro', uri=True)
    if db.execute('SELECT 1 FROM tweets WHERE id=?', (ident,)).fetchone():
        left.append('ligne en base')
    if [k for k, doc in db.execute('SELECT key, doc FROM files')
            if ident in (json.loads(doc).get('local_media_path') or '')]:
        left.append('recus files')
    db.close()
    queue = data / 'manual_queue.db'
    if queue.exists():
        q = sqlite3.connect(f'file:{queue}?mode=ro', uri=True)
        if q.execute('SELECT 1 FROM jobs WHERE tweet_id=?', (ident,)).fetchone():
            left.append('job en file')
        q.close()
    left += [str(f.relative_to(data)) for f in data.rglob(f'*{ident}*') if not is_backup(f)]
    return left


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('post', help="URL du post X, ou son identifiant numerique")
    ap.add_argument('--data', default=str(ROOT / 'data'))
    ap.add_argument('--dry-run', action='store_true', help='inventorier sans rien supprimer')
    ap.add_argument('--stop-server', action='store_true',
                    help="arreter le serveur d'archivage et le relancer ensuite")
    ap.add_argument('--force-shared', action='store_true',
                    help='supprimer aussi les fichiers partages avec un autre post')
    args = ap.parse_args()

    data = Path(args.data)
    ident = post_id(args.post)

    pid = server_pid(data)
    if pid and not args.dry_run:
        if not args.stop_server:
            print(f"Le serveur d'archivage tourne (pid {pid}). Ecrire dans la base pendant "
                  f"qu'il tourne la corrompt.\nRelancer avec --stop-server, ou l'arreter "
                  f"manuellement.")
            return 3
        print(f'  arret du serveur (pid {pid})')
        if not stop_server(pid):
            print("  le serveur ne s'est pas arrete, abandon"); return 3

    p = plan(data, ident)
    render(p, data)
    if not p['row'] and p['job'] is None and not p['media'] and not p['stray']:
        print('\nRien a supprimer pour ce post.')
        if pid and args.stop_server and not args.dry_run:
            start_server()
        return 0

    blocked = p['shared_media'] + p['shared_raw']
    if blocked and not args.force_shared:
        print('\nARRET : des elements sont partages avec un autre post (voir PARTAGE ci-dessus).')
        print('Les supprimer abimerait cet autre post. Relancer avec --force-shared pour '
              'les supprimer quand meme.')
        if pid and args.stop_server:
            start_server()
        return 2

    if args.dry_run:
        print('\nSimulation. Relancer sans --dry-run pour supprimer.')
        return 0

    print()
    apply(p, data, args.force_shared)

    left = verify(data, ident)
    print()
    if left:
        print('RESIDUS :', left)
    else:
        print(f'Aucun residu : plus aucune trace de {ident}.')
    backups = sorted(f.name for f in data.glob('*.avant_suppression.db'))
    if backups:
        print(f"Les sauvegardes d'avant suppression contiennent encore ce post, "
              f"c'est leur role : {', '.join(backups[-2:])}")
        print('Les effacer une fois le resultat verifie.')

    if pid and args.stop_server:
        # start() attend deja que /health reponde : pas de boucle a refaire ici.
        if start_server().get('ok'):
            print('  serveur relance')
        else:
            print('  ATTENTION : le serveur ne semble pas etre reparti, le relancer a la main')
    return 1 if left else 0


if __name__ == '__main__':
    sys.exit(main())
