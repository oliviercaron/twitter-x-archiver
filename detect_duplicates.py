#!/usr/bin/env python3
"""Report duplicate archiving at four distinct levels.

The levels are not equivalent and must not be summed:

1. Structurel   -- deux lignes pour un meme post. Un defaut : `tweet_id` est
                  cle primaire et `upsert()` fusionne les re-observations.
2. Disque       -- un meme contenu stocke sous deux chemins. Un defaut de
                  deduplication.
3. Binaire      -- un meme fichier rattache a plusieurs posts. Attendu pour
                  les copies de citation (`media_relation='quote'`) ;
                  significatif quand deux posts le portent en propre.
4. Perceptuel   -- vignettes visuellement proches (pHash/dHash deja calcules
                  au telechargement). Rapproche des decoupes differentes d'un
                  meme moment : ce ne sont pas des doublons d'archivage, mais
                  la cle pertinente pour mesurer la circulation d'un moment.

Le pHash porte sur la vignette : il rapproche des plans d'ouverture
identiques. Une decoupe tres differente du meme moment peut lui echapper,
et un habillage commun peut rapprocher deux clips sans rapport. Les groupes
retournes sont des candidats a verifier, pas un verdict.

Lecture seule, aucun acces reseau. Sortie non nulle si un defaut reel
(niveau 1 ou 2) est constate.
"""
import argparse
import collections
import itertools
import json
import sqlite3
import sys


def load(path):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return [json.loads(d) for d, in db.execute('SELECT doc FROM tweets')]
    finally:
        db.close()


def media_entries(rows):
    for row in rows:
        for item in (row.get('media') or []):
            yield row, item


def fingerprint(item):
    """pHash/dHash live on the image itself, or on a video's thumbnail."""
    for holder in ('download', 'thumbnail_download'):
        d = item.get(holder) or {}
        if d.get('phash'):
            return int(d['phash'], 16), int(d.get('dhash') or '0', 16)
    return None


def hamming(a, b):
    return bin(a ^ b).count('1')


def structural(rows, queue_path):
    counts = collections.Counter(r['tweet_id'] for r in rows)
    urls = collections.Counter(r.get('tweet_url') for r in rows if r.get('tweet_url'))
    out = {'rows':len(rows), 'distinct_tweet_ids':len(counts),
           'duplicate_tweet_ids':[k for k, v in counts.items() if v > 1],
           'duplicate_urls':[k for k, v in urls.items() if v > 1],
           'queue_jobs':None, 'queue_duplicate_ids':[]}
    try:
        db = sqlite3.connect(f'file:{queue_path}?mode=ro', uri=True)
        jobs = collections.Counter(i for i, in db.execute('SELECT tweet_id FROM jobs'))
        db.close()
        out['queue_jobs'] = sum(jobs.values())
        out['queue_duplicate_ids'] = [k for k, v in jobs.items() if v > 1]
    except sqlite3.Error:
        pass                                  # File absente : le niveau reste indetermine.
    return out


def on_disk(rows):
    paths = collections.defaultdict(set)
    for _, item in media_entries(rows):
        d = item.get('download') or {}
        if d.get('sha256') and d.get('local_media_path'):
            paths[d['sha256']].add(d['local_media_path'])
    return {'distinct_contents':len(paths),
            'distinct_paths':len({p for v in paths.values() for p in v}),
            'contents_under_several_paths':{h:sorted(v) for h, v in paths.items() if len(v) > 1}}


def shared_binaries(rows):
    groups = collections.defaultdict(list)
    entries = list(media_entries(rows))
    for row, item in entries:
        h = (item.get('download') or {}).get('sha256')
        if h:
            groups[h].append((row, item))
    shared, reuploads = [], []
    for h, members in groups.items():
        if len({r['tweet_id'] for r, _ in members}) < 2:
            continue
        owners = [(r, m) for r, m in members if m.get('media_relation') == 'own']
        record = {'sha256':h,
                  'tweets':[{'tweet_id':r['tweet_id'], 'author':r.get('author_username'),
                             'relation':m.get('media_relation'),
                             'source_tweet_id':m.get('source_tweet_id'),
                             'created_at_utc':r.get('created_at_utc'),
                             'view_count':r.get('view_count'),
                             'text':(r.get('raw_content') or '')[:80]}
                            for r, m in sorted(members, key=lambda x: x[0].get('created_at_utc') or '')]}
        shared.append(record)
        if len(owners) > 1:
            reuploads.append({**record,
                              'same_author':len({r.get('author_username') for r, _ in owners}) == 1,
                              'tweets':[t for t in record['tweets'] if t['relation'] == 'own']})
    return {'media_entries':len(entries), 'distinct_files':len(groups),
            'quote_copy_surplus':len(entries) - len(groups),
            'relations':dict(collections.Counter(m.get('media_relation') for _, m in entries)),
            'files_on_several_tweets':sorted(shared, key=lambda r: -len(r['tweets'])),
            'independent_reuploads':sorted(reuploads, key=lambda r: -len(r['tweets']))}


def perceptual(rows, threshold):
    items = []
    for row, item in media_entries(rows):
        fp = fingerprint(item)
        if fp:
            d = item.get('download') or {}
            items.append({'key':(row['tweet_id'], item.get('source_tweet_id'), item.get('media_index')),
                          'tweet_id':row['tweet_id'], 'author':row.get('author_username'),
                          'relation':item.get('media_relation'),
                          'source_tweet_id':item.get('source_tweet_id'),
                          'sha256':d.get('sha256'), 'duration':d.get('actual_duration_seconds'),
                          'phash':fp[0], 'dhash':fp[1], 'text':(row.get('raw_content') or '')[:80]})
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    pairs = 0
    for a, b in itertools.combinations(items, 2):
        # Identical files belong to level 3; this level compares distinct files.
        if a['sha256'] and a['sha256'] == b['sha256']:
            continue
        if hamming(a['phash'], b['phash']) <= threshold:
            pairs += 1
            parent[find(a['key'])] = find(b['key'])
    index = {i['key']:i for i in items}
    clusters = collections.defaultdict(list)
    for key in list(parent):
        clusters[find(key)].append(index[key])
    groups = [sorted(g, key=lambda i: i['tweet_id']) for g in clusters.values() if len(g) > 1]
    return {'fingerprinted_media':len(items), 'threshold_bits':threshold,
            'close_pairs':pairs, 'groups':sorted(groups, key=len, reverse=True)}


def defects(result):
    s, d = result['structural'], result['on_disk']
    return bool(s['duplicate_tweet_ids'] or s['duplicate_urls'] or s['queue_duplicate_ids']
                or d['contents_under_several_paths'])


def report(result):
    s, d, b, p = (result['structural'], result['on_disk'],
                  result['shared_binaries'], result['perceptual'])
    print('=== 1. Doublons structurels ===')
    print(f"  {s['rows']} lignes | {s['distinct_tweet_ids']} tweet_id distincts")
    print(f"  tweet_id en double : {s['duplicate_tweet_ids'] or 'aucun'}")
    print(f"  tweet_url en double : {s['duplicate_urls'] or 'aucune'}")
    if s['queue_jobs'] is not None:
        print(f"  file : {s['queue_jobs']} jobs, doublons : {s['queue_duplicate_ids'] or 'aucun'}")

    print('\n=== 2. Doublons sur disque ===')
    print(f"  {d['distinct_contents']} contenus | {d['distinct_paths']} chemins")
    for h, paths in d['contents_under_several_paths'].items():
        print(f"  {h[:12]} stocke {len(paths)} fois : {paths}")
    if not d['contents_under_several_paths']:
        print('  aucun contenu stocke plusieurs fois')

    print('\n=== 3. Meme fichier sur plusieurs posts ===')
    print(f"  {b['media_entries']} entrees media | {b['distinct_files']} fichiers | "
          f"surplus de citation : {b['quote_copy_surplus']}")
    print(f"  relations : {b['relations']}")
    print(f"  fichiers rattaches a plusieurs posts : {len(b['files_on_several_tweets'])}")
    print(f"  dont re-uploads independants (>1 post le porte en propre) : "
          f"{len(b['independent_reuploads'])}")
    for rec in b['independent_reuploads']:
        tag = 'MEME COMPTE' if rec['same_author'] else 'comptes differents'
        print(f"\n    {rec['sha256'][:12]}  {tag}")
        for t in rec['tweets']:
            print(f"      {t['tweet_id']} @{str(t['author']):18s} {str(t['created_at_utc'])[:19]}  "
                  f"vues={t['view_count']}")
            print(f"           {t['text']!r}")

    print(f"\n=== 4. Proches visuellement (pHash <= {p['threshold_bits']} bits) ===")
    print(f"  {p['fingerprinted_media']} medias empreintes | {p['close_pairs']} paires | "
          f"{len(p['groups'])} groupes")
    for g in p['groups']:
        durations = sorted({round(i['duration'], 1) for i in g if i['duration']})
        print(f"\n  --- {len(g)} medias / {len({i['tweet_id'] for i in g})} posts / "
              f"durees {durations} ---")
        for i in g:
            mark = 'SOURCE' if i['source_tweet_id'] == i['tweet_id'] else str(i['relation'])
            length = str(round(i['duration'], 1)) if i['duration'] else '?'
            print(f"    {i['tweet_id']} @{str(i['author']):18s} {mark:6s} {length:>6s}s")
            print(f"         {i['text']!r}")

    print('\n' + ('DEFAUT : voir niveaux 1-2.' if defects(result) else
                  "Aucun doublon d'archivage. Les niveaux 3 et 4 sont des observations, "
                  'pas des defauts.'))
    return 1 if defects(result) else 0


def analyse(db_path, queue_path, threshold):
    rows = load(db_path)
    return {'structural':structural(rows, queue_path), 'on_disk':on_disk(rows),
            'shared_binaries':shared_binaries(rows), 'perceptual':perceptual(rows, threshold)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', default='data/collection.db')
    ap.add_argument('--queue', default='data/manual_queue.db')
    ap.add_argument('--phash-distance', type=int, default=10,
                    help='distance de Hamming max entre vignettes (defaut 10 bits sur 64)')
    ap.add_argument('--json', action='store_true', help='sortie machine au lieu du rapport')
    args = ap.parse_args()
    result = analyse(args.db, args.queue, args.phash_distance)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 1 if defects(result) else 0
    return report(result)


if __name__ == '__main__':
    sys.exit(main())
