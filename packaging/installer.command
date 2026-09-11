#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Installez Python 3.12 depuis python.org, puis relancez ce fichier.'
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11 minimum (3.12 recommandé)"'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python install_native_host.py
printf '\nInstallation terminée. Consultez INSTALLATION_MULTINAVIGATEUR.md pour charger votre extension.\n'
