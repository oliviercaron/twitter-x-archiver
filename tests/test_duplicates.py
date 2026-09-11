from detect_duplicates import defects, on_disk, perceptual, shared_binaries, structural


def row(tweet_id, author, media, created='2026-09-06T12:00:00+00:00'):
    return {'tweet_id':tweet_id, 'author_username':author, 'created_at_utc':created,
            'tweet_url':f'https://x.com/{author}/status/{tweet_id}', 'raw_content':'texte',
            'view_count':10, 'media':media}


def video(sha, relation, source, phash='0000000000000000', path=None, duration=12.0, index=0):
    return {'media_index':index, 'media_type':'video', 'media_relation':relation,
            'source_tweet_id':source,
            'download':{'sha256':sha, 'local_media_path':path or f'media/videos/{sha}.mp4',
                        'download_success':True, 'actual_duration_seconds':duration},
            'thumbnail_download':{'phash':phash, 'dhash':phash}}


def test_no_duplicates_on_clean_archive(tmp_path):
    rows = [row('1', 'a', [video('aa', 'own', '1', phash='ffffffffffffffff')]),
            row('2', 'b', [video('bb', 'own', '2', phash='0000000000000000')])]
    s = structural(rows, tmp_path/'absent.db')
    assert s['duplicate_tweet_ids'] == [] and s['duplicate_urls'] == []
    assert s['queue_jobs'] is None                      # file absente : indetermine, pas un defaut
    assert on_disk(rows)['contents_under_several_paths'] == {}
    b = shared_binaries(rows)
    assert b['files_on_several_tweets'] == [] and b['quote_copy_surplus'] == 0
    assert perceptual(rows, 10)['groups'] == []
    assert not defects({'structural':s, 'on_disk':on_disk(rows)})


def test_quote_copy_is_shared_but_not_a_reupload(tmp_path):
    # La ligne citante porte une copie du media du post cite.
    rows = [row('1', 'source', [video('aa', 'own', '1')]),
            row('2', 'citant', [video('aa', 'quote', '1')])]
    b = shared_binaries(rows)
    assert len(b['files_on_several_tweets']) == 1
    assert b['independent_reuploads'] == []             # un seul porteur en propre
    assert b['quote_copy_surplus'] == 1
    assert b['relations'] == {'own':1, 'quote':1}
    assert not defects({'structural':structural(rows, tmp_path/'absent.db'), 'on_disk':on_disk(rows)})


def test_independent_reupload_is_reported():
    rows = [row('1', 'premier', [video('aa', 'own', '1')]),
            row('2', 'second', [video('aa', 'own', '2')])]
    reuploads = shared_binaries(rows)['independent_reuploads']
    assert len(reuploads) == 1
    assert reuploads[0]['same_author'] is False
    assert {t['tweet_id'] for t in reuploads[0]['tweets']} == {'1', '2'}


def test_same_content_under_two_paths_is_a_defect(tmp_path):
    rows = [row('1', 'a', [video('aa', 'own', '1', path='media/videos/one.mp4')]),
            row('2', 'b', [video('aa', 'own', '2', path='media/videos/two.mp4')])]
    d = on_disk(rows)
    assert list(d['contents_under_several_paths']) == ['aa']
    assert defects({'structural':structural(rows, tmp_path/'absent.db'), 'on_disk':d})


def test_perceptual_groups_distinct_files_and_ignores_identical_ones():
    # 'aa' et 'bb' sont deux fichiers differents dont les vignettes se ressemblent
    # a 1 bit ; 'cc' est visuellement distant.
    rows = [row('1', 'a', [video('aa', 'own', '1', phash='00000000000000ff')]),
            row('2', 'b', [video('bb', 'own', '2', phash='00000000000000fe')]),
            row('3', 'c', [video('cc', 'own', '3', phash='ffffffffffffffff')])]
    p = perceptual(rows, 10)
    assert p['close_pairs'] == 1 and len(p['groups']) == 1
    assert {i['tweet_id'] for i in p['groups'][0]} == {'1', '2'}

    # Deux entrees du meme fichier ne forment jamais un groupe perceptuel :
    # elles relevent du niveau 3.
    same = [row('1', 'a', [video('aa', 'own', '1')]), row('2', 'b', [video('aa', 'quote', '1')])]
    assert perceptual(same, 10)['groups'] == []


def test_threshold_widens_the_groups():
    rows = [row('1', 'a', [video('aa', 'own', '1', phash='0000000000000000')]),
            row('2', 'b', [video('bb', 'own', '2', phash='000000000000ffff')])]
    assert perceptual(rows, 10)['groups'] == []
    assert len(perceptual(rows, 16)['groups']) == 1
