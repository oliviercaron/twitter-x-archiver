import csv
import hashlib
import json
import sqlite3
from pathlib import Path
import pytest
from twitter_x_archiver.archive import atomic_json
from twitter_x_archiver.export_results import export_results,cell,tables,output_path,validate_csv_snapshot,display_path
from twitter_x_archiver.export_service import ExportManager


def make_data(tmp_path,corrupt=False):
    data=tmp_path/'data';data.mkdir();(data/'media/images').mkdir(parents=True);(data/'raw').mkdir()
    content=b'fixture-media-bytes'
    (data/'media/images/photo.jpg').write_bytes(b'corrupt' if corrupt else content)
    (data/'raw/page.json.gz').write_bytes(b'fixture-raw-source')
    ids=['2097298426828324969','2097298426828324970','2097298426828324971']
    rows=[{'tweet_id':ids[0],'conversation_id':ids[0],'reply_to_tweet_id':None,'raw_content':'Texte; "cité"\nÉmoji 😬',
        'author_username':'demo','manual_selections':[{'mode':'manual_url'}],'discussion_roots':[ids[0]],
        'empty':'','missing':None,'literal_null':'\\N','like_count':0,'raw_tweet':{'extra_future_field':['a',None,3]},
        'media':[{'media_type':'photo','media_id':'9','source_tweet_id':ids[0],'download':{'local_media_path':'media/images/photo.jpg','sha256':hashlib.sha256(content).hexdigest(),'download_success':True}}],
        'metrics_snapshots':[{'observed_at':'2026-09-08T10:00:00Z','like_count':1,'author_metrics':{'author_followers_count':4}},
                             {'observed_at':'2026-09-08T11:00:00Z','like_count':2,'media_metrics':[{'media_id':'9','media_view_count':3}]}],
        'raw_response_refs':['raw/page.json.gz']},
        {'tweet_id':ids[1],'reply_to_tweet_id':ids[0],'conversation_id':ids[0],'discussion_roots':[ids[0]],'raw_content':'Réponse',
         'media':[],'metrics_snapshots':[{'observed_at':'2026-09-08T11:01:00Z','like_count':None}],'raw_response_refs':[]},
        {'tweet_id':ids[2],'raw_content':'Old automatic result','media':[],'metrics_snapshots':[]}]
    with sqlite3.connect(data/'collection.db') as db:
        db.execute('CREATE TABLE tweets(id TEXT PRIMARY KEY,doc TEXT)')
        db.executemany('INSERT INTO tweets VALUES(?,?)',[(r['tweet_id'],json.dumps(r,ensure_ascii=False)) for r in rows])
    (data/'accounts.db').write_bytes(b'SECRET_COOKIE')
    (data/'secrets_bridge.json').write_text('SECRET_BRIDGE')
    return data,rows


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f,delimiter=';'))


def test_consolidated_export_preserves_values_history_tree_and_files(tmp_path):
    data,source=make_data(tmp_path)
    result=export_results(data,tmp_path/'chosen')
    dest=Path(result['path']);rows=read_csv(dest/'tweets.csv');history=read_csv(dest/'observations.csv')
    assert result['tweets']==2 and len(history)==3 and result['copied_files']==2
    assert rows[0]['tweet_id']==source[0]['tweet_id'] and rows[0]['raw_content']==source[0]['raw_content']
    assert rows[0]['like_count']=='0' and rows[0]['missing']=='\\N' and rows[0]['empty']==''
    assert rows[0]['literal_null']=='\\\\N'
    assert json.loads(rows[0]['raw_tweet'])==source[0]['raw_tweet']
    assert json.loads(rows[0]['media'])==source[0]['media']
    assert rows[1]['reply_to_tweet_id']==rows[0]['tweet_id'] and rows[1]['reply_depth_from_conversation_root']=='1'
    assert rows[0]['metrics_snapshot_count']=='2' and 'metrics_snapshots' not in rows[0]
    assert history[1]['like_count']=='2' and history[2]['like_count']=='\\N'
    assert json.loads(history[1]['media_metrics'])==source[0]['metrics_snapshots'][1]['media_metrics']
    assert (dest/rows[0]['media_01_local_media_path']).read_bytes()==b'fixture-media-bytes'
    assert not list(dest.rglob('accounts.db')) and not list(dest.rglob('secrets*'))
    assert {p.name for p in dest.glob('*.csv')}=={'tweets.csv','observations.csv'}
    assert not (dest/'edges.csv').exists()
    assert (dest/'tweets.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    again=export_results(data,tmp_path/'chosen',include_automatic=True)
    assert again['path']!=result['path'] and again['tweets']==3
    assert (dest/'tweets.csv').exists()


def test_missing_asset_is_explicit_and_corruption_rejects_export(tmp_path):
    data,_=make_data(tmp_path)
    (data/'raw/page.json.gz').unlink()
    result=export_results(data,tmp_path/'results')
    assert result['status']=='partial' and result['missing_files']==['raw/page.json.gz']
    assert json.loads(read_csv(Path(result['path'])/'tweets.csv')[0]['export_missing_files'])==['raw/page.json.gz']
    (data/'media/images/photo.jpg').write_bytes(b'changed')
    with pytest.raises(ValueError,match='Empreinte'):export_results(data,tmp_path/'broken')
    assert not list((tmp_path/'broken').glob('archive_X_*'))


def test_source_path_escape_rejected(tmp_path):
    data,rows=make_data(tmp_path)
    rows[0]['raw_response_refs']=['../accounts.db']
    with sqlite3.connect(data/'collection.db') as db:db.execute('UPDATE tweets SET doc=? WHERE id=?',(json.dumps(rows[0]),rows[0]['tweet_id']))
    with pytest.raises(ValueError,match='Chemin'):export_results(data,tmp_path/'results')


def test_missing_parent_and_cycle_do_not_invent_depth():
    rows=[{'tweet_id':'1','conversation_id':'9','reply_to_tweet_id':'2'},
          {'tweet_id':'2','conversation_id':'9','reply_to_tweet_id':'1'},
          {'tweet_id':'3','conversation_id':'9','reply_to_tweet_id':'8'}]
    result,_=tables(rows)
    assert result[0]['reply_chain_status']=='cycle' and result[0]['reply_depth_from_conversation_root'] is None
    assert result[2]['reply_chain_status']=='parent_missing'
    with pytest.raises(ValueError):output_path('relative/folder')


def test_background_export_persists_chosen_destination(tmp_path):
    data,_=make_data(tmp_path);manager=ExportManager(data)
    manager.start(str(tmp_path/'chosen'),False);manager.thread.join(timeout=10)
    view=manager.view();assert view['export']['status']=='done'
    restored=ExportManager(data).view()
    assert restored['destination']==view['destination'] and restored['export']['tweets']==2


def test_cli_export_is_visible_from_the_dashboard(tmp_path):
    """Les deux chemins d'export ecrivent le meme statut."""
    from twitter_x_archiver.export_results import run_cli
    data,_=make_data(tmp_path)
    manager=ExportManager(data)                       # tableau de bord deja ouvert
    assert manager.view()['export']['status']=='idle'

    result=run_cli(data,str(tmp_path/'en_ligne_de_commande'),False)
    assert result['status']=='done' and result['tweets']==2

    view=manager.view()                               # sans redemarrage du serveur
    assert view['export']['status']=='done'
    assert view['export']['tweets']==2
    assert view['export']['path']==result['path']
    assert view['destination']==display_path(tmp_path/'en_ligne_de_commande')


def test_deleted_export_folder_is_flagged(tmp_path):
    import shutil
    data,_=make_data(tmp_path);manager=ExportManager(data)
    manager.start(str(tmp_path/'chosen'),False);manager.thread.join(timeout=10)
    assert 'folder_missing' not in manager.view()['export']
    shutil.rmtree(manager.view()['export']['path'])
    assert manager.view()['export']['folder_missing'] is True


def test_cli_refuses_while_a_dashboard_export_runs(tmp_path):
    from twitter_x_archiver.export_results import run_cli
    data,_=make_data(tmp_path)
    atomic_json(Path(data)/'export_status.json',{'status':'running'})
    with pytest.raises(ValueError,match='déjà en cours'):
        run_cli(data,str(tmp_path/'refuse'),False)
    assert not (tmp_path/'refuse').exists()


def test_dashboard_export_still_wins_over_a_stale_file(tmp_path):
    # Pendant son propre export, le service ne doit pas relire un fichier tiers.
    data,_=make_data(tmp_path)
    manager=ExportManager(data)
    manager.start(str(tmp_path/'chosen'),False)
    atomic_json(Path(data)/'export_status.json',{'status':'idle','message':'ecrit par un tiers'})
    manager.thread.join(timeout=10)
    assert manager.view()['export']['status']=='done'


def test_csv_validation_detects_changed_source_value(tmp_path):
    data,source=make_data(tmp_path);result=export_results(data,tmp_path/'results')
    folder=Path(result['path']);path=folder/'tweets.csv';rows=read_csv(path)
    rows[0]['raw_content']='Altered text'
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),delimiter=';');writer.writeheader();writer.writerows(rows)
    with pytest.raises(ValueError,match='Valeur source'):validate_csv_snapshot(folder,source[:2])
