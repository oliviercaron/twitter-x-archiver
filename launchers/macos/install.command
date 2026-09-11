#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Install Python 3.12 from python.org, then run this script again.'
  exit 1
fi
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m twitter_x_archiver --install
