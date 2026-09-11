"""Resume d'un post archive, pour la liste de l'interface.

La liste ne sert a rien si elle n'affiche que des identifiants. Compte, date
d'archivage, compteurs et vignette suffisent a se rappeler de quoi il s'agit.

Les documents complets pesent une vingtaine de kilo-octets chacun, et les
relire a chaque rafraichissement couterait plusieurs secondes. Le resultat est
donc garde en memoire tant que la base ne bouge pas.
"""
import html
import json
import re
import sqlite3
from pathlib import Path

import categories

_cache = {'signature': None, 'value': {}}

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
TCO = re.compile(r'\s*https://t\.co/\w+\s*$')


def readable(text):
    """Texte pour l'oeil seulement. `raw_content` reste intact dans la base.

    X echappe les chevrons et l'esperluette, et colle un lien t.co en fin de
    message pour le media deja affiche a cote.
    """
    text = html.unescape(text or '')
    while TCO.search(text):
        text = TCO.sub('', text)
    return text.strip()


def thumbnail(row):
    """Vignette a montrer : celle d'une video, ou l'image elle-meme."""
    for item in (row.get('media') or []):
        candidate = (item.get('thumbnail_download') or {}).get('local_media_path')
        if candidate:
            return candidate
        own = (item.get('download') or {}).get('local_media_path')
        if own and Path(own).suffix.lower() in IMAGE_SUFFIXES:
            return own
    return None


def playable(row):
    """Chemin et duree de la premiere video lisible. Sans cela, aucune source
    a donner au lecteur : `thumb` ne montre qu'une image fixe."""
    for item in (row.get('media') or []):
        if item.get('media_type') not in ('video', 'animated_gif'):
            continue
        download = item.get('download') or {}
        path = download.get('local_media_path')
        if path and download.get('download_success'):
            return path, download.get('actual_duration_seconds')
    return None, None


def kind_of(media):
    """Le type d'un post mixte est video des qu'une video s'y trouve : classer
    sur le premier media seulement rangerait mal un post photo + video."""
    types = {item.get('media_type') for item in media}
    for candidate in ('video', 'animated_gif', 'photo'):
        if candidate in types:
            return 'video' if candidate == 'animated_gif' else candidate
    return None


def summarise(row):
    media = row.get('media') or []
    video, duration = playable(row)
    return {'video': video,
            'duration': round(duration, 1) if isinstance(duration, (int, float)) else None,
            'author': row.get('author_username'),
            'name': row.get('author_display_name'),
            'posted': row.get('created_at_paris'),
            'archived': row.get('last_collected_at'),
            'likes': row.get('like_count'),
            'views': row.get('view_count'),
            'replies': row.get('reply_count'),
            'reposts': row.get('repost_count'),
            'text': readable(row.get('raw_content'))[:90],
            'category': categories.of(row),
            'kind': kind_of(media),
            'media': len(media),
            'thumb': thumbnail(row)}


def signature(path):
    try:
        stat = Path(path).stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def summaries(data):
    """Un resume par post archive, relu seulement quand la base a change."""
    path = Path(data) / 'collection.db'
    mark = signature(path)
    if mark is None:
        return {}
    if _cache['signature'] == mark:
        return _cache['value']
    result = {}
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            for ident, doc in db.execute('SELECT id, doc FROM tweets'):
                try:
                    result[ident] = summarise(json.loads(doc))
                except ValueError:
                    continue           # une ligne abimee ne doit pas vider la liste
        finally:
            db.close()
    except sqlite3.Error:
        return _cache['value']
    _cache.update(signature=mark, value=result)
    return result
