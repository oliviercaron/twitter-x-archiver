#!/usr/bin/env python3
"""Hote de messagerie native : demarre le serveur d'archivage a la demande.

Chrome interdit a une extension de lancer un programme. Le seul pont autorise
est cet hote, declare dans le registre et restreint a l'identifiant de notre
extension. L'extension lui envoie « ping » ou « start », il repond.

L'hote ne fait que lancer : le serveur est demarre en processus detache, il
survit donc a la fermeture de Chrome, qui tue ses hotes natifs a la
deconnexion. Il n'archive rien lui-meme et n'ouvre jamais la base.

Protocole : longueur sur 4 octets en little-endian, puis JSON UTF-8.
"""
import json
import os
import struct
import sys
from pathlib import Path

# Depuis un executable, tout est deja embarque. Depuis les sources, le dossier
# du projet doit rejoindre le chemin de recherche.
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server_control                                    # noqa: E402


def read_message():
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        return None
    length = struct.unpack('<I', header)[0]
    if not 0 < length <= 1_000_000:      # une commande depasse rarement quelques octets
        return None
    try:
        return json.loads(sys.stdin.buffer.read(length).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return {'type': 'invalid'}


def write_message(value):
    payload = json.dumps(value, ensure_ascii=False).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('<I', len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def handle(message):
    kind = (message or {}).get('type')
    if kind == 'ping':
        return {'ok': True, **server_control.status()}
    if kind == 'start':
        return server_control.start()
    return {'ok': False, 'error': 'unknown_command'}


def main():
    if os.name == 'nt':
        import msvcrt
        for stream in (sys.stdin, sys.stdout):
            msvcrt.setmode(stream.fileno(), os.O_BINARY)
    while True:
        message = read_message()
        if message is None:
            return 0
        try:
            try:result=handle(message)
            except server_control.app_paths.StoragePathError as exc:
                result={'ok':False,'error':'storage_unavailable','message':str(exc)}
            write_message(result)
        except OSError:
            return 0


if __name__ == '__main__':
    sys.exit(main())
