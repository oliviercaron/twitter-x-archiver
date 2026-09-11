#!/usr/bin/env python3
"""Native messaging bridge used by the browser to start the local application.

Messages use a four-byte little-endian length followed by UTF-8 JSON. The bridge
starts a detached service so archiving can continue after the browser exits.
It does not fetch posts or open the archive database itself."""
import json
import os
import struct
import sys
from pathlib import Path

from .. import server_control                                    # noqa: E402


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
