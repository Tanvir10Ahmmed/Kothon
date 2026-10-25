#!/usr/bin/env bash
# One-time setup for macOS (Intel or Apple Silicon). Safe to run again.
set -euo pipefail
cd "$(dirname "$0")"
echo "== Kothon setup =="

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew nei. Age install koro:  https://brew.sh   (tarpor ei script abar chalao)"
  exit 1
fi

PY=""
for c in python3.12 python3.11; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "-> Python 3.12 install hocche..."
  brew install python@3.12
  PY="$(brew --prefix python@3.12)/bin/python3.12"
fi
echo "Python: $($PY --version)"

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "-> ffmpeg install hocche..."
  brew install ffmpeg
fi

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

echo
python scripts/doctor.py
echo
echo "Setup sesh. Ekhon chalao:  ./run.sh"
echo "(Optional) model age-theke download:  .venv/bin/python scripts/download_model.py"
