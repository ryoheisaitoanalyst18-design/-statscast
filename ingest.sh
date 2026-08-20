#!/usr/bin/env bash
# 試合データ取込アプリを起動してブラウザで開く。
#   ./ingest.sh          → http://127.0.0.1:8787
#   INGEST_PORT=9000 ./ingest.sh
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="http://127.0.0.1:${INGEST_PORT:-8787}"

( sleep 1
  for o in xdg-open wslview open; do
    command -v "$o" >/dev/null && { "$o" "$URL" >/dev/null 2>&1; break; }
  done ) &

exec python3 "$DIR/scripts/ingest_server.py"
