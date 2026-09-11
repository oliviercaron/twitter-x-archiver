#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
  echo 'Exécutez d’abord installer.command.'
  exit 1
fi
.venv/bin/python -c 'import server_control,webbrowser; s=server_control.start(); print("Service prêt" if s.get("ok") else "Échec du démarrage"); webbrowser.open("http://127.0.0.1:18765") if s.get("ok") else None; raise SystemExit(0 if s.get("ok") else 1)'
