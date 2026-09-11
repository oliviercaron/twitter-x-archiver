"""Une categorie par post : le rangement que l'archivage propose.

Un post archive par erreur, ou garde pour un autre angle, n'a pas a etre
detruit pour autant. La categorie le range ailleurs, et la portee d'un export
s'y appuie pour que le corpus de these ne se melange pas au reste.

Le nom est libre. Il est seulement mis au propre : espaces reduits, longueur
bornee, et la categorie par defaut prend le relais quand rien n'est choisi.
"""
DEFAUT = 'ZEVENT 2026'
LONGUEUR = 60


def clean(value):
    """Nom de categorie utilisable, ou la categorie par defaut."""
    text = ' '.join(str(value or '').split())[:LONGUEUR].strip()
    return text or DEFAUT


def of(row):
    """Categorie d'un post archive, meme ancien : rien n'est sans categorie."""
    return clean((row or {}).get('category'))
