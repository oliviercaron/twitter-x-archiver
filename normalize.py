"""Lossless raw + parsed preservation, conservative analytical extraction."""
import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

METRICS = {'reply_count':'reply_count', 'retweet_count':'retweet_count',
           'repost_count':'retweet_count', 'like_count':'favorite_count',
           'quote_count':'quote_count', 'bookmark_count':'bookmark_count'}


def at(obj, path, default=None):
    for key in path.split('.'):
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj


def first(*values):
    return next((v for v in values if v is not None), None)


def integer(value):
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def stamp(value):
    if not value:
        return None
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        try:
            d = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def walk(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def flatten(obj):
    return {**(obj.get('legacy') or {}), **{k:v for k,v in obj.items() if k != 'legacy'}}


def reference_only(obj):
    """X may return a typed Tweet containing only its identity, not a payload."""
    return not any(k not in {'__typename','rest_id','id_str','id'} for k in obj)


def unwrap(result):
    """X may wrap an embedded tweet in a visibility envelope."""
    if isinstance(result, dict) and result.get('__typename') == 'TweetWithVisibilityResults':
        return result.get('tweet') or {}
    return result if isinstance(result, dict) else {}


def raw_objects(payload):
    """Keep original objects; include quoted/reposted context with explicit provenance.

    X wraps a post whose replies are restricted in `TweetWithVisibilityResults`.
    The real post sits under `tweet` and carries no `__typename` of its own, so
    it must be unwrapped explicitly or it stays invisible to the whole pipeline.
    """
    tweets, users = {}, {}
    for obj in walk(payload):
        kind = obj.get('__typename')
        if kind == 'TweetWithVisibilityResults':
            inner = unwrap(obj)
            if not inner.get('rest_id'):
                continue
            obj, kind = inner, 'Tweet'
        ident = first(obj.get('rest_id'), obj.get('id_str'))
        if kind in ('Tweet', 'User') and ident:
            target=tweets if kind=='Tweet' else users
            def richness(candidate):
                flat=flatten(candidate)
                has_payload=('full_text' in flat or 'created_at' in flat) if kind=='Tweet' else bool(flat.get('screen_name') or at(candidate,'core.screen_name'))
                return (not reference_only(candidate),has_payload,len(json.dumps(candidate,ensure_ascii=False)))
            previous=target.get(str(ident))
            if previous is None or richness(obj)>richness(previous):
                target[str(ident)]=obj
    return tweets, users


def media_items(raw, parsed=None):
    flat = flatten(raw)
    source_id = str(first(raw.get('rest_id'), flat.get('id_str'), ''))
    items = at(flat, 'extended_entities.media') or at(flat, 'entities.media') or []
    result = []
    for i, item in enumerate(items):
        info = item.get('video_info') or {}
        variants = info.get('variants') or []
        mp4 = [v for v in variants if v.get('content_type') == 'video/mp4' and v.get('url')]
        best = max(mp4, key=lambda v: v.get('bitrate') or 0, default={})
        size = item.get('original_info') or at(item, 'sizes.large') or {}
        typ = item.get('type')
        result.append({
            'source_tweet_id': source_id, 'media_index': i, 'media_relation': 'own',
            'media_id': first(item.get('id_str'), str(item['id']) if item.get('id') else None),
            'media_key': item.get('media_key'), 'media_type': typ,
            'media_url': first(item.get('media_url_https'), item.get('media_url')),
            'media_expanded_url': item.get('expanded_url'),
            'width': first(size.get('width'), size.get('w')),
            'height': first(size.get('height'), size.get('h')),
            'alt_text': item.get('ext_alt_text'), 'duration_ms': info.get('duration_millis'),
            'aspect_ratio': info.get('aspect_ratio'),
            'thumbnail_url': item.get('media_url_https') if typ != 'photo' else None,
            'preview_image_url': item.get('media_url_https') if typ != 'photo' else None,
            'video_view_count': integer(at(item, 'mediaStats.viewCount')),
            'media_view_count': integer(at(item, 'mediaStats.viewCount')),
            'media_variants': variants, 'n_video_variants': len(variants),
            'max_video_bitrate': max((v['bitrate'] for v in variants if v.get('bitrate') is not None), default=None),
            'best_video_url': best.get('url'), 'raw_media': item,
        })
    # Parsed/card media supplement raw entities only if not already represented.
    if parsed:
        groups = parsed.get('media') or {}
        extras = [(p, 'photo') for p in groups.get('photos', [])]
        extras += [(p, 'video') for p in groups.get('videos', [])]
        extras += [(p, 'animated_gif') for p in groups.get('animated', [])]
        card = parsed.get('card') or {}
        extras += [(card[k], typ) for k,typ in [('photo','photo'),('video','video')] if card.get(k)]
        for p, typ in extras:
            url = first(p.get('url'), p.get('thumbnailUrl'))
            if any(m['media_url'] == url for m in result):
                continue
            variants = [{'content_type':v.get('contentType'), 'bitrate':v.get('bitrate'), 'url':v.get('url')} for v in p.get('variants', [])]
            if p.get('videoUrl'):
                variants.append({'content_type':'video/mp4', 'url':p['videoUrl']})
            artificial = {'rest_id':source_id, 'extended_entities':{'media':[{'type':typ,
                'media_url_https':url, 'video_info':{'variants':variants, 'duration_millis':p.get('duration')},
                'mediaStats':{'viewCount':p.get('views')}}]}}
            m = media_items(artificial)[0]
            m['media_index'] = len(result)
            m['metadata_source'] = 'parsed_or_card_fallback'
            result.append(m)
    return result


def relation_id(flat, relation):
    prefix = 'quoted' if relation == 'quote' else 'retweeted'
    return first(flat.get(prefix + '_status_id_str'), at(flat, prefix + '_status_result.result.rest_id'),
                 at(flat, prefix + '_status_result.result.tweet.rest_id'))


RELATIONS = [('quoted', 'quoted_status_result'), ('reposted', 'retweeted_status_result')]


def related_context(flat, key, prefix):
    """Analytical columns for the embedded quoted/reposted object.

    Absence is preserved: a reference-only or missing object yields
    `<prefix>_context_available=False` and nothing else, never zeros.
    The complete object stays in `raw_tweet`.
    """
    obj = unwrap(at(flat, key + '.result'))
    if not obj or reference_only(obj):
        return {prefix + '_context_available': False}
    q = flatten(obj)
    q_user = at(obj, 'core.user_results.result') or {}
    qu = flatten(q_user)
    qcore = qu.get('core') or {}
    text = first(at(q, 'note_tweet.note_tweet_results.result.text'), q.get('full_text'))
    created = stamp(q.get('created_at'))
    media = media_items(obj)
    nested = relation_id(q, 'quote')
    out = {
        prefix+'_context_available':True,
        prefix+'_tweet_id_observed':str(first(obj.get('rest_id'), q.get('id_str')) or '') or None,
        prefix+'_author_id':str(first(q_user.get('rest_id'), qu.get('id_str')) or '') or None,
        prefix+'_author_username':first(qu.get('screen_name'), qcore.get('screen_name')),
        prefix+'_author_display_name':first(qu.get('name'), qcore.get('name')),
        prefix+'_author_followers_count':integer(first(at(qu,'relationship_counts.followers'), qu.get('followers_count'))),
        prefix+'_author_verified':first(at(qu,'verification.verified'), qu.get('verified')),
        prefix+'_author_blue':qu.get('is_blue_verified'),
        prefix+'_text':text,
        prefix+'_text_length':len(text) if text is not None else None,
        prefix+'_language':q.get('lang'),
        prefix+'_created_at':q.get('created_at'),
        prefix+'_created_at_utc':created,
        prefix+'_created_at_paris':datetime.fromisoformat(created).astimezone(ZoneInfo('Europe/Paris')).isoformat() if created else None,
        prefix+'_conversation_id':q.get('conversation_id_str'),
        prefix+'_reply_to_tweet_id':q.get('in_reply_to_status_id_str'),
        prefix+'_is_reply':bool(q.get('in_reply_to_status_id_str')),
        prefix+'_nested_quoted_tweet_id':str(nested) if nested else None,
        prefix+'_view_count':integer(first(at(q,'views.count'), at(q,'ext_views.count'))),
        prefix+'_media_count':len(media),
        prefix+'_photo_count':sum(m['media_type'] == 'photo' for m in media),
        prefix+'_video_count':sum(m['media_type'] == 'video' for m in media),
        prefix+'_gif_count':sum(m['media_type'] == 'animated_gif' for m in media),
    }
    out.update({f'{prefix}_{name}':integer(q.get(src)) for name, src in METRICS.items()})
    return out


def normalize(raw, users, parsed, observed, query, version, aliases, raw_ref, parse_error=None):
    t = flatten(raw)
    uid = first(t.get('user_id_str'), at(t, 'core.user_results.result.rest_id'))
    u_raw = first(users.get(str(uid)), at(t, 'core.user_results.result'), {})
    u = flatten(u_raw)
    core = u.get('core') or {}
    ident = str(first(raw.get('rest_id'), t.get('id_str')))
    date = stamp(t.get('created_at'))
    name = first(u.get('screen_name'), core.get('screen_name'))
    content = first(at(t, 'note_tweet.note_tweet_results.result.text'), t.get('full_text'))
    entities = t.get('entities') or {}
    note_entities = at(t, 'note_tweet.note_tweet_results.result.entity_set') or {}
    def entity(key):
        # JSON key dedup keeps complete original dictionaries.
        return list({json.dumps(x, sort_keys=True): x for x in entities.get(key, []) + note_entities.get(key, [])}.values())
    quote, repost = relation_id(t, 'quote'), relation_id(t, 'repost')
    row = {
        'tweet_id':ident, 'tweet_url':f'https://x.com/{name}/status/{ident}' if name else f'https://x.com/i/status/{ident}',
        'created_at':t.get('created_at'), 'created_at_utc':date,
        'created_at_paris':datetime.fromisoformat(date).astimezone(ZoneInfo('Europe/Paris')).isoformat() if date else None,
        'conversation_id':t.get('conversation_id_str'), 'language':t.get('lang'), 'source':t.get('source'),
        'raw_content':content, 'rendered_content':(parsed or {}).get('renderedContent'),
        'reply_to_tweet_id':t.get('in_reply_to_status_id_str'), 'reply_to_user_id':t.get('in_reply_to_user_id_str'),
        'quoted_tweet_id':str(quote) if quote else None, 'reposted_tweet_id':str(repost) if repost else None,
        'retweeted_tweet_id':str(repost) if repost else None,
        'is_reply':bool(t.get('in_reply_to_status_id_str')), 'is_quote':bool(quote or t.get('is_quote_status')),
        'is_repost':bool(repost), 'hashtags':[x.get('text') for x in entity('hashtags')],
        'cashtags':[x.get('text') for x in entity('symbols')], 'mentions':entity('user_mentions'),
        'urls':entity('urls'), 'links':(parsed or {}).get('links', []),
        'view_count':integer(first(at(t, 'views.count'), at(t, 'ext_views.count'))),
        'author_id':str(uid) if uid else None, 'author_username':name,
        'author_display_name':first(u.get('name'),core.get('name')),
        'author_description':first(at(u, 'profile_bio.description'),u.get('description')),
        'author_location':at(u,'location.location') if isinstance(u.get('location'),dict) else u.get('location'),
        'author_created_at':first(u.get('created_at'), core.get('created_at')),
        'author_followers_count':integer(first(at(u,'relationship_counts.followers'),u.get('followers_count'))),
        'author_friends_count':integer(first(at(u,'relationship_counts.following'),u.get('friends_count'))),
        'author_statuses_count':integer(first(at(u,'tweet_counts.tweets'),u.get('statuses_count'))),
        'author_favourites_count':integer(first(at(u,'action_counts.favorites_count'),u.get('favourites_count'))),
        'author_listed_count':integer(u.get('listed_count')),
        'author_media_count':integer(first(at(u,'tweet_counts.media_tweets'),u.get('media_count'))),
        'author_verified':first(at(u,'verification.verified'),u.get('verified')),
        'author_blue':u.get('is_blue_verified'), 'author_blue_type':first(at(u,'verification.verified_type'),u.get('verified_type')),
        'author_protected':first(at(u,'privacy.protected'),u.get('protected')),
        'media':media_items(raw, parsed), 'raw_tweet':raw, 'raw_author':u_raw, 'parsed_tweet':parsed,
        'parse_error':parse_error, 'raw_response_refs':[raw_ref],
        'collector':'twscrape', 'twscrape_version':version,
        'collection_timestamp':observed, 'collection_query':query, 'collection_queries':[query],
        'first_collected_at':observed, 'last_collected_at':observed, 'metrics_observed_at':observed,
        'tweet_currently_available':None if reference_only(raw) else True, 'availability_observed_at':observed,
        'record_completeness':'reference_only' if reference_only(raw) else 'payload_present',
        'zevent_keyword_match':bool(re.search(r'z\s*event', content or '', re.I)),
        'streamer_name_match':[a for a in aliases if re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)', content or '', re.I)],
    }
    row.update({out:integer(t.get(src)) for out,src in METRICS.items()})
    row['is_original_tweet'] = not any(row[k] for k in ['is_reply','is_quote','is_repost'])
    for prefix, key in RELATIONS:
        row.update(related_context(t, key, prefix))
    # Self-quote is only meaningful when both identities were actually returned.
    row['is_self_quote'] = (row['quoted_author_id'] == row['author_id']
                            if row.get('quoted_author_id') and row.get('author_id') else None)
    row['author_following_count'] = row['author_friends_count']
    row['author_tweet_count'] = row['author_statuses_count']
    row['author_created_at_utc'] = stamp(row['author_created_at'])
    row['author_created_at_paris'] = (datetime.fromisoformat(row['author_created_at_utc']).astimezone(ZoneInfo('Europe/Paris')).isoformat() if row['author_created_at_utc'] else None)
    counts(row)
    return row


def counts(row):
    media = row.get('media', [])
    for kind,label in [('photo','photo'),('video','video'),('animated_gif','gif')]:
        row[label+'_count'] = sum(m['media_type'] == kind for m in media)
        row['has_'+label] = row[label+'_count'] > 0
    row['media_count'] = len(media)
    row['has_media'] = bool(media)
    row['media_variants'] = [m.get('media_variants',[]) for m in media]
    row['tweet_text_length'] = len(row['raw_content']) if row.get('raw_content') is not None else None
    for label, key in [('hashtag','hashtags'),('mention','mentions'),('url','urls')]:
        row[f'tweet_{label}_count'] = len(row.get(key, []))
    vals = [row.get(k) for k in ['like_count','repost_count','reply_count','quote_count']]
    row['total_interactions'] = sum(vals) if all(v is not None for v in vals) else None
    views, total = row.get('view_count'), row['total_interactions']
    row['engagement_per_view'] = total/views if total is not None and views is not None and views > 0 else None
    for dst, src in [('log1p_author_followers','author_followers_count'), ('log1p_tweet_views','view_count')]:
        v = row.get(src)
        row[dst] = math.log1p(v) if v is not None and v >= 0 else None
    created, author = row.get('created_at_utc'), row.get('author_created_at_utc')
    row['author_account_age_days'] = (datetime.fromisoformat(created)-datetime.fromisoformat(author)).total_seconds()/86400 if created and author else None


def snapshot(row):
    return {'observed_at':row['metrics_observed_at'],
            **{k:row.get(k) for k in [*METRICS,'view_count']},
            'media_metrics':[{'media_id':m.get('media_id'),'source_tweet_id':m.get('source_tweet_id'),
                              'media_index':m['media_index'],'video_view_count':m.get('video_view_count'),
                              'media_view_count':m.get('media_view_count')} for m in row['media']],
            'author_metrics':{k:v for k,v in row.items() if k.startswith('author_') and k.endswith('_count')}}
