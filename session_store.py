"""Session X deposee par l'extension : reception, validation, lecture.

L'extension lit deux cookies nommes sur x.com, `auth_token` et `ct0`, et les
depose ici. Rien d'autre ne transite : ni les autres cookies, ni les autres
sites. Le fichier reste local, restreint au compte utilisateur, et ses valeurs
ne sont jamais journalisees, renvoyees par l'API, ni incluses dans un export.

Un cookie de session X equivaut a etre connecte au compte : il est traite
comme un mot de passe.
"""
import json
import os
import re
from pathlib import Path

NAME = 'session.json'
# Assez large pour les formats successifs de X, assez etroit pour exclure tout
# separateur d'en-tete ou caractere de controle.
VALUE = re.compile(r'^[A-Za-z0-9%._~-]{10,300}$')


def path(root):
    return Path(root) / NAME


def valid(value):
    return isinstance(value, str) and bool(VALUE.fullmatch(value))


def write_session(root, auth_token, ct0):
    """Enregistre la session apres validation. Leve ValueError si le format ne va pas."""
    if not (valid(auth_token) and valid(ct0)):
        raise ValueError('cookies X invalides')
    target = path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.partial')
    tmp.write_text(json.dumps({'auth_token': auth_token, 'ct0': ct0}), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass                       # les ACL Windows gouvernent le dossier utilisateur
    os.replace(tmp, target)
    return True


def read_session(root):
    try:
        data = json.loads(path(root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if valid(data.get('auth_token')) and valid(data.get('ct0')):
        return {'auth_token': data['auth_token'], 'ct0': data['ct0']}
    return None


def session_present(root):
    """Etat expose a l'interface : jamais les valeurs, seulement leur presence."""
    return read_session(root) is not None


def forget_session(root):
    try:
        path(root).unlink()
        return True
    except OSError:
        return False
