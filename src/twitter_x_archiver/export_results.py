"""Portable, lossless CSV results; does not contact X or change collection state."""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime,timezone
from pathlib import Path
from . import categories
from .archive import atomic_json, now

NULL='\\N'
MEDIA_COLUMNS=('media_id','source_tweet_id','media_relation','media_type','width','height','duration_ms',
               'media_url','thumbnail_url','best_video_url','video_view_count','media_view_count','max_video_bitrate')
DOWNLOAD_COLUMNS=('local_media_path','source_url','download_success','download_error','sha256','file_size_bytes',
                  'actual_duration_seconds','actual_width','actual_height','actual_codec','actual_fps')


def output_path(value):
    value=str(value).strip()
    if re.match(r'^[A-Za-z]:[\\/]',value) and os.name!='nt':
        mount=Path('/mnt')/value[0].lower()
        if not mount.is_dir():raise ValueError('Lecteur Windows inaccessible depuis WSL.')
        value=str(mount/value[3:].replace('\\','/'))
    p=Path(value)
    if not p.is_absolute():raise ValueError('Indiquez un chemin absolu, par exemple D:\\Mes resultats.')
    return p.resolve()


def display_path(path):
    text=str(path)
    m=re.match(r'^/mnt/([a-z])(?:/(.*))?$',text)
    return m[1].upper()+':\\'+(m[2] or '').replace('/','\\') if m else text


def cell(value):
    if value is None:return NULL
    if isinstance(value,(list,dict)):
        return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if isinstance(value,bool):return 'true' if value else 'false'
    value=str(value)
    return '\\'+value if value.startswith('\\') else value


def csv_write(path,rows,first):
    keys=list(first)+sorted(set().union(*(r.keys() for r in rows))-set(first)) if rows else list(first)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,keys,delimiter=';',quoting=csv.QUOTE_ALL,lineterminator='\r\n')
        writer.writeheader()
        for row in rows:writer.writerow({k:cell(row.get(k)) for k in keys})
        f.flush();os.fsync(f.fileno())
    return len(keys)


def validate_csv_snapshot(folder,source):
    """Check every original value against the exact in-memory database snapshot."""
    csv.field_size_limit(max(csv.field_size_limit(),2**31-1))
    with (folder/'tweets.csv').open(encoding='utf-8-sig',newline='') as f:
        tweets=list(csv.DictReader(f,delimiter=';'))
    with (folder/'observations.csv').open(encoding='utf-8-sig',newline='') as f:
        observations=list(csv.DictReader(f,delimiter=';'))
    indexed={r['tweet_id']:r for r in tweets}
    history={(r['tweet_id'],str(r['observation_index'])):r for r in observations}
    if len(indexed)!=len(source) or len(tweets)!=len(source):raise ValueError('Nombre de tweets incohérent dans le CSV.')
    expected_count=sum(len(r.get('metrics_snapshots',[])) for r in source)
    if len(history)!=expected_count or len(observations)!=expected_count:raise ValueError('Historique CSV incomplet.')
    for row in source:
        for key,value in row.items():
            if key!='metrics_snapshots' and indexed[row['tweet_id']].get(key)!=cell(value):
                raise ValueError('Valeur source non conservée dans le CSV.')
        for n,snapshot in enumerate(row.get('metrics_snapshots',[]),1):
            for key,value in snapshot.items():
                if history[(row['tweet_id'],str(n))].get(key)!=cell(value):
                    raise ValueError('Valeur historique non conservée dans le CSV.')


def chain(ident,root,by_id):
    current=ident;seen=set();depth=0
    while current!=root:
        if current in seen:return None,'cycle'
        seen.add(current)
        row=by_id.get(current)
        if not row:return None,'parent_missing'
        current=row.get('reply_to_tweet_id')
        if not current:return None,'root_not_reached'
        depth+=1
    return depth,'resolved'


def tables(rows):
    by_id={r['tweet_id']:r for r in rows};tweets=[];observations=[]
    for source in rows:
        row=dict(source);history=row.pop('metrics_snapshots',[])
        row['metrics_snapshot_count']=len(history)
        row['is_manually_selected']=bool(row.get('manual_selections'))
        row['selection_scope']='manual_selection' if row['is_manually_selected'] else 'discussion_reply' if row.get('discussion_roots') else 'automatic_search'
        parent=row.get('reply_to_tweet_id')
        row['reply_parent_in_export']=parent in by_id if parent else None
        origin=row.get('conversation_id')
        depth,state=chain(row['tweet_id'],origin,by_id) if origin else (None,'conversation_unknown')
        row['reply_depth_from_conversation_root']=depth
        row['reply_chain_status']=state
        row['depth_by_selected_root']={root:chain(row['tweet_id'],root,by_id)[0] for root in row.get('discussion_roots',[])}
        for n,media in enumerate(row.get('media',[]),1):
            prefix=f'media_{n:02d}_'
            for key in MEDIA_COLUMNS:row[prefix+key]=media.get(key)
            for key in DOWNLOAD_COLUMNS:row[prefix+key]=media.get('download',{}).get(key)
            row[prefix+'thumbnail_path']=media.get('thumbnail_download',{}).get('local_media_path')
        # Keep the complete media JSON column too: future fields are never discarded.
        tweets.append(row)
        for n,snapshot in enumerate(history,1):
            observation={'tweet_id':row['tweet_id'],'observation_index':n,**snapshot}
            for key,value in snapshot.get('author_metrics',{}).items():observation[key]=value
            for i,media in enumerate(snapshot.get('media_metrics',[]),1):
                for key,value in media.items():observation[f'media_{i:02d}_{key}']=value
            observations.append(observation)
    return tweets,observations


def references(rows):
    refs={}
    def visit(value):
        if isinstance(value,dict):
            path=value.get('local_media_path')
            if path:
                expected=value.get('sha256')
                if path in refs and refs[path] and expected and refs[path]!=expected:
                    raise ValueError('Empreintes contradictoires pour un même fichier média.')
                refs[path]=expected or refs.get(path)
            for v in value.values():visit(v)
        elif isinstance(value,list):
            for v in value:visit(v)
    for row in rows:
        visit(row.get('media',[]))
        for ref in row.get('raw_response_refs',[]):
            if ref:refs.setdefault(ref,None)
    return refs


def export_results(data,destination,include_automatic=False,only=None):
    data=Path(data).resolve();base=output_path(destination)
    with sqlite3.connect((data/'collection.db').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        rows=[json.loads(r[0]) for r in db.execute('SELECT doc FROM tweets ORDER BY id')]
    if not include_automatic:rows=[r for r in rows if r.get('manual_selections') or r.get('discussion_roots')]
    # `only` limite l'export a certaines categories : le corpus de these
    # s'exporte sans les posts gardes pour un autre usage.
    if only:rows=[r for r in rows if categories.of(r) in set(only)]
    root_ids=set(r['tweet_id'] for r in rows if r.get('manual_selections'))
    coverage=[]
    for root in sorted(root_ids):
        f=data/'discussions'/root/'summary.json'
        if f.exists():
            s=json.loads(f.read_text())
            coverage.append(f"{root}: {s.get('status')}, {s.get('reply_count_archived')} réponses, exhaustivité non garantie")
    name='archive_X_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    base.mkdir(parents=True,exist_ok=True)
    staging=base/('.'+name+'.partial');staging.mkdir()
    copied=0;missing=[]
    try:
        for relative,expected in references(rows).items():
            rel=Path(relative)
            if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0] not in ('raw','media'):
                raise ValueError('Chemin de source non autorisé dans les métadonnées.')
            source=(data/rel).resolve()
            if not source.is_relative_to(data):raise ValueError('Source extérieure au dossier de données.')
            if not source.is_file():missing.append(relative);continue
            dest=staging/rel;dest.parent.mkdir(parents=True,exist_ok=True)
            digest=hashlib.sha256()
            with source.open('rb') as src,dest.open('xb') as dst:
                while chunk:=src.read(1024*1024):digest.update(chunk);dst.write(chunk)
                dst.flush();os.fsync(dst.fileno())
            if expected and digest.hexdigest()!=expected:raise ValueError('Empreinte média incorrecte ; export non validé.')
            copied+=1
        tweets,observations=tables(rows)
        for row in tweets:
            row['export_missing_files']=[r for r in references([row]) if r in missing]
        columns=csv_write(staging/'tweets.csv',tweets,['tweet_id','reply_to_tweet_id','conversation_id','raw_content','author_username'])
        csv_write(staging/'observations.csv',observations,['tweet_id','observation_index','observed_at'])
        validate_csv_snapshot(staging,rows)
        note=f'''RÉSULTATS X — export du {datetime.now(timezone.utc).isoformat()}

{len(tweets)} tweets ; {len(observations)} observations de compteurs ; {columns} colonnes dans tweets.csv.
Périmètre : {'toutes les collectes, y compris la recherche automatique' if include_automatic else 'sélections manuelles et réponses associées'}.

DEUX TABLEAUX
- tweets.csv : une ligne par tweet. reply_to_tweet_id est le parent immédiat ; pas de fichier edges séparé nécessaire. Les citations/republications gardent leurs propres colonnes.
- observations.csv : une ligne par tweet et relevé des compteurs. Ne pas confondre ce tableau longitudinal avec le nombre de tweets uniques.
- media/ : fichiers binaires ; chemins relatifs à ce dossier d’export dans les tableaux.
- raw/ : réponses X originales compressées, pour vérifier et réextraire les données. Elles peuvent contenir du contexte non inclus comme lignes du CSV.

LECTURE ET FIDÉLITÉ
CSV UTF-8 avec BOM, séparateur point-virgule, champs entre guillemets. Accents, emojis et retours à la ligne sont conservés.
La valeur absente est représentée par \\N ; une chaîne vide reste vide. Une chaîne commençant par une barre oblique inverse est échappée en doublant sa première barre : enlever une barre lors de la lecture de ces chaînes échappées.
Les objets et listes sont des chaînes JSON, sans troncature. Les colonnes media_01_*, media_02_*… facilitent l’analyse ; la colonne media conserve aussi toutes les métadonnées, y compris les champs non aplatis. Dans tweets.csv, metrics_snapshots est déplacé intégralement dans observations.csv.
Importer TOUS LES IDENTIFIANTS COMME DU TEXTE : Excel arrondit les grands nombres. Utiliser Données → À partir d’un fichier texte/CSV plutôt que le double-clic. Les cellules JSON très longues peuvent dépasser la capacité d’affichage d’Excel ; ne pas réenregistrer depuis Excel pour constituer l’archive de référence.
Exemple pandas : pd.read_csv('tweets.csv', sep=';', dtype=str, keep_default_na=False, na_values=[r'\\N']). Les contenus JSON se lisent ensuite avec json.loads. Les champs échappés commençant par deux barres inverses doivent être déséchappés séparément.

QUALITÉ
Le texte source, les champs bruts, les compteurs absents et toutes les observations stockées sont conservés. Les comptes X et clés de connexion ne sont jamais exportés.
Les empreintes SHA-256 des médias disposant d’une empreinte enregistrée ont été vérifiées pendant la copie.
Les tableaux sont extraits d’un instantané cohérent de SQLite. Une collecte en cours peut continuer après cet instantané. Les bilans de couverture ci-dessous sont indicatifs et lus séparément.
Tous les champs source et tous les relevés de compteurs ont été relus dans les CSV et comparés à cet instantané avant validation de l’export.
{copied} fichiers copiés ; {len(missing)} fichiers référencés absents. Les absences sont aussi indiquées dans export_missing_files.
'''+'\n'.join(coverage)+'\nFichiers absents :\n'+'\n'.join(missing)+'\n'
        (staging/'LIRE_MOI.txt').write_text(note,encoding='utf-8-sig')
        target=base/name;staging.rename(target)
        return {'path':str(target),'display_path':display_path(target),'tweets':len(tweets),'observations':len(observations),
                'columns':columns,'copied_files':copied,'missing_files':missing,'csv_verified':True,'status':'partial' if missing else 'done'}
    except Exception:
        # Keep incomplete output identifiable; do not remove research artifacts automatically.
        raise


def read_status(data):
    path=Path(data)/'export_status.json'
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status':'idle'}
    except (OSError,ValueError):
        return {'status':'idle'}


def record_export(data,destination,include_automatic,state):
    """Ecrit le statut et les reglages lus par le tableau de bord.

    Le service du serveur et la ligne de commande passent tous deux par ici :
    un export lance d'un cote ne doit pas laisser l'autre afficher un resultat
    perime.
    """
    data=Path(data)
    atomic_json(data/'export_settings.json',
                {'destination':str(destination),'include_automatic':include_automatic is True})
    atomic_json(data/'export_status.json',state)
    return state


def run_cli(data,destination,include_automatic):
    target=output_path(destination)
    if read_status(data).get('status')=='running':
        raise ValueError('Un export est déjà en cours ; attendez sa fin ou relancez le serveur.')
    record_export(data,target,include_automatic,
                  {'status':'running','started_at':now(),'destination':display_path(target)})
    try:
        result=export_results(data,target,include_automatic)
    except Exception as exc:
        record_export(data,target,include_automatic,
                      {'status':'error','error_class':type(exc).__name__,
                       'message':'Export non validé. Vérifiez le dossier, l’espace disponible et les sources. '
                                 'Le dossier .partial est conservé pour diagnostic.'})
        raise
    return record_export(data,target,include_automatic,{**result,'finished_at':now()})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--data',default=None)
    parser.add_argument('--include-automatic',action='store_true')
    args=parser.parse_args()
    from . import app_paths
    data=args.data or app_paths.resolve_data_dir()
    print(json.dumps(run_cli(data,args.destination,args.include_automatic),ensure_ascii=False))
