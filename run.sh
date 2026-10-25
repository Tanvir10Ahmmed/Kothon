#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then echo "Age ./setup.sh chalao"; exit 1; fi
# shellcheck disable=SC1091
. .venv/bin/activate
HOST="${KOTHON_HOST:-127.0.0.1}"
PORT="${KOTHON_PORT:-8000}"
URL="http://$HOST:$PORT"
echo "Kothon cholche: $URL   (bondho korte Ctrl+C)"
( sleep 2; command -v open >/dev/null 2>&1 && open "$URL" ) >/dev/null 2>&1 &
# one worker process on purpose: jobs + the loaded model live in this process
exec python -m uvicorn app.main:create_app --factory --host "$HOST" --port "$PORT"
