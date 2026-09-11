"""Category normalization for user-selected posts."""
DEFAUT = 'ZEVENT 2026'
LONGUEUR = 60


def clean(value):
    """Nom de categorie utilisable, ou la categorie par defaut."""
    text = ' '.join(str(value or '').split())[:LONGUEUR].strip()
    return text or DEFAUT


def of(row):
    """Categorie d'un post archive, meme ancien : rien n'est sans categorie."""
    return clean((row or {}).get('category'))
