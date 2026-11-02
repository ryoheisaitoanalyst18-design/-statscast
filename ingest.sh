#!/usr/bin/env bash
# 試合データ取込アプリを起動してブラウザで開く。
#   ./ingest.sh          → http://127.0.0.1:8787
#   INGEST_PORT=9000 ./ingest.sh
# Windows デスクトップの「statscast 取込」アイコンからも起動される (wsl.exe 経由)。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="http://127.0.0.1:${INGEST_PORT:-8787}"

open_browser() {
  for o in wslview xdg-open open; do
    command -v "$o" >/dev/null && { "$o" "$URL" >/dev/null 2>&1; return; }
  done
  # WSL: Windows 側の既定ブラウザで開く
  command -v cmd.exe >/dev/null && cmd.exe /c start "" "$URL" >/dev/null 2>&1 || true
}

# 既に起動中ならブラウザを開くだけ (アイコンを二度押ししてもポート衝突しない)
if curl -s -o /dev/null --max-time 1 "$URL"; then
  echo "取込アプリは起動済みです: $URL"
  open_browser
  exit 0
fi

( sleep 1; open_browser ) &

exec python3 "$DIR/scripts/ingest_server.py"
