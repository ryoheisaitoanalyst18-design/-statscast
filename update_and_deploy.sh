#!/usr/bin/env bash
# ============================================================
# update_and_deploy.sh — ROKUDAI STATCAST 更新・デプロイ (完全版)
#
# 使い方:
#   ./update_and_deploy.sh /path/to/new_data.csv [--clean]   # CSVマージ→全再生成→デプロイ
#   ./update_and_deploy.sh --regenerate                      # マスターCSVから全再生成→デプロイ
#   ./update_and_deploy.sh --data-only                       # 再生成せずデータ同期→デプロイ
#   ./update_and_deploy.sh --frontend-only                   # フロントのみビルド→デプロイ
#   いずれも --no-push を付けると commit/push しない (ドライラン)
#
# このスクリプトは過去の運用事故から学んだ以下を全て自動で行う:
#   1. デプロイ前に git fetch + fast-forward (リモート直編集/夜間PRとの乖離防止)
#   2. データ再生成後に検証ゲート (scripts/validate_data.py) — 失敗したら中断
#   3. assets/ は「丸ごと」同期 (index-*.js だけコピーすると3D遅延チャンクが壊れる)
#   4. index.html と 404.html の「両方」のハッシュを差し替え (404はSPAフォールバック)
#   5. noindex メタの保持を検証 (意図的な検索非掲載設定。消えていたら中断)
#   6. 参照アセットの実在検証 → commit → push → Actions完了待ち → 本番URL 200確認
# ============================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE="/home/analyst18/tokyo-baseball"
PIPELINE_DATA="/home/analyst18/statscast/data"
FRONTEND="/home/analyst18/rokudai_statcast"
MASTER_CSV="/home/analyst18/ubuntu_data/trackman_data.csv"
LIVE_BASE="https://ryoheisaitoanalyst18-design.github.io/-statscast"

MODE="csv"; CSV_PATH=""; CLEAN=""; NO_PUSH=0
for arg in "$@"; do
  case "$arg" in
    --regenerate)    MODE="regenerate" ;;
    --data-only)     MODE="data-only" ;;
    --frontend-only) MODE="frontend-only" ;;
    --clean)         CLEAN="--clean" ;;
    --no-push)       NO_PUSH=1 ;;
    -h|--help)       grep '^#' "$0" | head -25; exit 0 ;;
    *)               CSV_PATH="$arg" ;;
  esac
done
if [ "$MODE" = "csv" ] && [ -z "$CSV_PATH" ]; then
  echo "使い方: $0 <new.csv|--regenerate|--data-only|--frontend-only> [--clean] [--no-push]"; exit 1
fi

step() { echo ""; echo "━━━ $1 ━━━"; }

# ============================================================
step "0/7 プリフライト: リモート同期確認"
# ============================================================
cd "$REPO_DIR"
git fetch origin -q
AHEAD=$(git rev-list --count origin/main..main)
BEHIND=$(git rev-list --count main..origin/main)
echo "local vs origin/main: ahead=$AHEAD behind=$BEHIND"
if [ "$BEHIND" -gt 0 ]; then
  if ! git diff --quiet || ! git diff --staged --quiet; then
    echo "エラー: リモートが進んでいるのにローカルに未コミット変更がある。"
    echo "手動で対処してから再実行: git stash → git merge --ff-only origin/main → git stash pop"
    exit 1
  fi
  git merge --ff-only origin/main
  echo "origin/main へ fast-forward 完了"
fi

# ============================================================
if [ "$MODE" = "csv" ] || [ "$MODE" = "regenerate" ]; then
  step "1/7 データパイプライン ($MODE)"
  if [ "$MODE" = "csv" ]; then
    [ -f "$CSV_PATH" ] || { echo "エラー: $CSV_PATH がない"; exit 1; }
    BACKUP="${MASTER_CSV%.csv}.backup_$(date +%Y%m%d_%H%M%S).csv"
    cp "$MASTER_CSV" "$BACKUP"
    echo "マスターCSVバックアップ: $BACKUP"
    python3 "$PIPELINE/update_season.py" "$CSV_PATH" $CLEAN
  else
    python3 "$PIPELINE/update_season.py" --regenerate
  fi
else
  echo ""; echo "━━━ 1/7 データパイプライン: スキップ ($MODE) ━━━"
fi

# ============================================================
if [ "$MODE" != "frontend-only" ]; then
  step "2/7 検証ゲート: パイプライン出力"
  python3 "$REPO_DIR/scripts/validate_data.py" "$PIPELINE_DATA" --skip-html

  step "3/7 データ同期 → 公開リポジトリ"
  # 上位は --delete しない (defenseData等の手動ファイル保護)。
  # teams/ と models/ はパイプライン完全所有なので --delete で残骸を残さない。
  # players/batter_zones はフロント未使用 (2026-07確認: ローダー定義のみ残存・
  # バンドル参照ゼロ) のため公開しない。パイプラインは生成し続けるが同期除外。
  rsync -a --exclude 'players/batter_zones' "$PIPELINE_DATA/" "$REPO_DIR/data/"
  [ -d "$PIPELINE_DATA/teams" ]  && rsync -a --delete "$PIPELINE_DATA/teams/"  "$REPO_DIR/data/teams/"
  [ -d "$PIPELINE_DATA/models" ] && rsync -a --delete "$PIPELINE_DATA/models/" "$REPO_DIR/data/models/"
  echo "データ同期完了"
else
  echo ""; echo "━━━ 2-3/7 データ同期: スキップ (frontend-only) ━━━"
fi

# ============================================================
if [ "$MODE" != "data-only" ]; then
  step "4/7 フロントエンドビルド"
  cd "$FRONTEND"
  npx tsc --noEmit
  BUILD_TARGET=ghpages npx vite build 2>&1 | tail -3
  DIST="$FRONTEND/dist/public"
  NEW_JS=$(grep -o 'index-[A-Za-z0-9_-]*\.js'  "$DIST/index.html" | head -1)
  NEW_CSS=$(grep -o 'index-[A-Za-z0-9_-]*\.css' "$DIST/index.html" | head -1)
  echo "新バンドル: $NEW_JS / $NEW_CSS"

  step "5/7 assets 全体同期 + index/404 ハッシュ差し替え"
  # 「丸ごと --delete」が重要: 3D系(JinguStadium/HitTrajectory3D/OrbitControls/
  # SpinAnalysis3D/PitchTrajectory3D)は遅延ロード別チャンクでビルド毎にハッシュが変わる。
  rsync -a --delete "$DIST/assets/" "$REPO_DIR/assets/"
  cd "$REPO_DIR"
  for f in index.html 404.html; do
    sed -i -E "s|index-[A-Za-z0-9_-]+\.js|$NEW_JS|g; s|index-[A-Za-z0-9_-]+\.css|$NEW_CSS|g" "$f"
  done
else
  echo ""; echo "━━━ 4-5/7 フロントエンド: スキップ (data-only) ━━━"
fi

# ============================================================
step "6/7 最終検証ゲート: 公開リポジトリ全体"
# ============================================================
cd "$REPO_DIR"
python3 scripts/validate_data.py   # HTML検査込み (参照アセット実在 + noindex 保持)

# ============================================================
step "7/7 コミット・プッシュ・本番確認"
# ============================================================
git add data assets index.html 404.html scripts 2>/dev/null || true
if git diff --staged --quiet; then
  echo "変更なし。コミットをスキップ。"; exit 0
fi
git status -s | grep -E "^[MADR]" | head -8
echo "  ... (staged $(git diff --staged --name-only | wc -l) files)"

if [ "$NO_PUSH" = 1 ]; then
  echo ""; echo "--no-push 指定: ここで停止。git reset で取り消すか、手動で commit してください。"
  exit 0
fi

git commit -m "$(cat <<EOF
Update site data and assets ($MODE, $(date +%Y-%m-%d))

- Pipeline output synced, assets rebuilt, hashes updated in index/404.
- Passed validate_data.py gate ($(date +%H:%M)).
EOF
)"
git push origin main
echo "push 完了。GitHub Actions を待機..."

if command -v gh >/dev/null; then
  RID=$(gh run list --limit 1 --json databaseId -q '.[0].databaseId')
  for i in $(seq 1 30); do
    STATUS=$(gh run view "$RID" --json status -q .status 2>/dev/null || echo "")
    [ "$STATUS" = "completed" ] && break
    sleep 10
  done
  echo "Actions: $(gh run view "$RID" --json conclusion -q .conclusion 2>/dev/null || echo '不明')"
  sleep 8
  CURR_JS=$(grep -o 'index-[A-Za-z0-9_-]*\.js' index.html | head -1)
  for u in "/" "/assets/$CURR_JS" "/data/years.json"; do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" --retry 2 --max-time 15 "$LIVE_BASE$u")
    printf "  %-30s -> %s\n" "$u" "$CODE"
    [ "$CODE" = "200" ] || { echo "エラー: 本番URL異常 ($u)"; exit 1; }
  done
  LIVE_JS=$(curl -s --max-time 15 "$LIVE_BASE/" | grep -o 'index-[A-Za-z0-9_-]*\.js' | head -1)
  [ "$LIVE_JS" = "$CURR_JS" ] && echo "✅ 本番が新バンドル ($LIVE_JS) を配信中" \
    || { echo "エラー: 本番JS不一致 live=$LIVE_JS expected=$CURR_JS"; exit 1; }
else
  echo "gh CLI なし: 1-2分後に $LIVE_BASE を手動確認してください"
fi

echo ""; echo "=============================================="
echo " デプロイ完了! $LIVE_BASE"
echo "=============================================="
