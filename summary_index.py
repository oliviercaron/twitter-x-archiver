"""Index consultable des posts archives : filtrer, trier, paginer sans tout relire.

Le resume d'un post tient dans quelques centaines d'octets, le document complet
en pese vingt mille. Garder les champs consultables dans leur propre table
evite de relire et d'analyser toute la base a chaque affichage.

Mesure faite sur des jeux synthetiques : reconstruire un cache memoire coute
240 a 280 microsecondes par post, soit 28 secondes a cent mille posts, pendant
lesquelles l'interface et l'archivage se figent ensemble. Ici une requete
filtree et triee reste sous la milliseconde a la meme taille.

La table vit dans la meme base que les documents : une ligne et son resume
s'ecrivent donc dans la meme transaction, et ne peuvent pas diverger.
"""
import json
import sqlite3

from summaries import summarise

VERSION = 5
SCHEMA = '''
CREATE TABLE IF NOT EXISTS summary(
  id TEXT PRIMARY KEY, author TEXT, name TEXT, posted TEXT, archived TEXT,
  likes INTEGER, views INTEGER, replies INTEGER, reposts INTEGER,
  kind TEXT, media INTEGER, thumb TEXT, video TEXT, duration REAL, text TEXT,
  category TEXT);
CREATE INDEX IF NOT EXISTS summary_archived ON summary(archived DESC);
CREATE INDEX IF NOT EXISTS summary_posted ON summary(posted DESC);
CREATE INDEX IF NOT EXISTS summary_views ON summary(views DESC);
CREATE INDEX IF NOT EXISTS summary_likes ON summary(likes DESC);
CREATE INDEX IF NOT EXISTS summary_reposts ON summary(reposts DESC);
CREATE INDEX IF NOT EXISTS summary_author ON summary(author, archived DESC);
CREATE INDEX IF NOT EXISTS summary_kind ON summary(kind, archived DESC);
CREATE INDEX IF NOT EXISTS summary_category ON summary(category, archived DESC);
CREATE INDEX IF NOT EXISTS summary_day ON summary(substr(posted,1,10));
CREATE VIRTUAL TABLE IF NOT EXISTS summary_fts USING fts5(
  id UNINDEXED, text, author, name, tokenize='unicode61');
'''
COLUMNS = ('id', 'author', 'name', 'posted', 'archived', 'likes', 'views', 'replies',
           'reposts', 'kind', 'media', 'thumb', 'video', 'duration', 'text', 'category')
SORTS = {'recent': 'archived DESC', 'ancien': 'archived ASC',
         'publie': 'posted DESC', 'publie_asc': 'posted ASC',
         'vues': 'views DESC', 'likes': 'likes DESC', 'reposts': 'reposts DESC'}


def ensure(db):
    """Cree la table si besoin, et la reconstruit quand sa forme a change."""
    known = db.execute("SELECT doc FROM state WHERE key='summary_index_version'").fetchone()
    if known and json.loads(known[0]) == VERSION:
        db.executescript(SCHEMA)
        return False
    # Version absente ou differente : la forme des tables ne peut pas etre
    # presumee, elles repartent de zero plutot que d'etre completees a l'aveugle.
    db.executescript('DROP TABLE IF EXISTS summary_fts; DROP TABLE IF EXISTS summary;')
    db.executescript(SCHEMA)
    rebuild(db)
    return True


def row_values(ident, row):
    s = summarise(row)
    return (ident, s['author'], s['name'], s['posted'], s['archived'], s['likes'],
            s['views'], s['replies'], s['reposts'], s['kind'], s['media'],
            s['thumb'], s['video'], s['duration'], s['text'], s['category'])


def put(db, ident, row):
    """Ecrit le resume d'un post. Appele dans la transaction qui ecrit le post."""
    values = row_values(ident, row)
    db.execute('INSERT OR REPLACE INTO summary VALUES(' + ','.join('?' * len(COLUMNS)) + ')', values)
    db.execute('DELETE FROM summary_fts WHERE id=?', (ident,))
    db.execute('INSERT INTO summary_fts(id, text, author, name) VALUES(?,?,?,?)',
               (ident, values[14] or '', values[1] or '', values[2] or ''))


def drop(db, ident):
    db.execute('DELETE FROM summary_fts WHERE id=?', (ident,))
    db.execute('DELETE FROM summary WHERE id=?', (ident,))


def rebuild(db):
    """Repart des documents. Vingt-cinq secondes a cent mille posts, une fois."""
    db.execute('DELETE FROM summary_fts')
    db.execute('DELETE FROM summary')
    for ident, doc in db.execute('SELECT id, doc FROM tweets').fetchall():
        try:
            put(db, ident, json.loads(doc))
        except (ValueError, KeyError, TypeError):
            continue                    # un document abime ne doit pas vider l'index
    db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',
               ('summary_index_version', json.dumps(VERSION)))
    db.commit()


def escape(term):
    """Une requete FTS5 se compose de termes cites : l'utilisateur ecrit du
    texte, pas une syntaxe de recherche, et une apostrophe ne doit rien casser."""
    words = [w.replace('"', '') for w in term.split()]
    return ' '.join(f'"{w}"*' for w in words if w)


def search(db, q='', author='', kind='', ids=None, sort='recent', page=1, size=24,
           since='', until='', category=''):
    """Une page de resultats, avec le total. `ids` restreint aux posts d'une file."""
    where, params = [], []
    if author:
        where.append('summary.author = ?')
        params.append(author)
    if kind:
        where.append('summary.kind = ?')
        params.append(kind)
    if category:
        where.append('summary.category = ?')
        params.append(category)
    # Comparaison sur les dix premiers caracteres, soit la date seule : `posted`
    # porte une heure et un fuseau, qui fausseraient une comparaison directe.
    if since:
        where.append('substr(summary.posted,1,10) >= ?')
        params.append(since)
    if until:
        where.append('substr(summary.posted,1,10) <= ?')
        params.append(until)
    if ids is not None:
        if not ids:
            return {'total': 0, 'page': 1, 'pages': 0, 'size': size, 'items': []}
        where.append('summary.id IN (' + ','.join('?' * len(ids)) + ')')
        params.extend(ids)
    source = 'summary'
    if q and q.strip():
        source = 'summary JOIN summary_fts ON summary_fts.id = summary.id'
        where.append('summary_fts MATCH ?')
        params.append(escape(q))
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM {source}{clause}', params).fetchone()[0]
    pages = max(1, -(-total // size)) if total else 0
    page = min(max(1, page), pages or 1)
    order = SORTS.get(sort, SORTS['recent'])
    # Les valeurs manquantes vont toujours en fin de liste, quel que soit le tri.
    column = order.split()[0]
    rows = db.execute(
        f'SELECT {",".join("summary." + c for c in COLUMNS)} FROM {source}{clause} '
        f'ORDER BY summary.{column} IS NULL, summary.{order} LIMIT ? OFFSET ?',
        params + [size, (page - 1) * size]).fetchall()
    return {'total': total, 'page': page, 'pages': pages, 'size': size,
            'items': [dict(zip(COLUMNS, r)) for r in rows]}


def span(db):
    """Premiere et derniere date de publication, pour borner le choix de dates."""
    row = db.execute('SELECT MIN(substr(posted,1,10)), MAX(substr(posted,1,10)) '
                     'FROM summary WHERE posted IS NOT NULL').fetchone()
    return {'from': row[0], 'to': row[1]} if row else {'from': None, 'to': None}


def authors(db, limit=400):
    """Comptes presents, du plus archive au moins archive, pour la saisie assistee."""
    return [{'author': a, 'count': n} for a, n in db.execute(
        'SELECT author, COUNT(*) n FROM summary WHERE author IS NOT NULL '
        'GROUP BY author ORDER BY n DESC LIMIT ?', (limit,))]


def categories(db):
    """Categories utilisees, de la plus fournie a la moins fournie."""
    return [{'category': c, 'count': n} for c, n in db.execute(
        'SELECT category, COUNT(*) n FROM summary WHERE category IS NOT NULL '
        'GROUP BY category ORDER BY n DESC, category')]
