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
#   7. 同時に 1 本だけ実行 (取り込み画面と夜間の動画パイプラインが重なっても順番待ち)
#   8. この commit の Actions が 1 本でも失敗したら失敗で終わる (データだけの更新でも)
#   9. マスターの自動バックアップは新しい 10 本 + 月ごとの 1 本だけ残す (scripts/prune_master_backups.py)
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

# 同時実行の防止: 取り込み (ingest.sh) と夜間の動画パイプライン (--data-only) が同時に走ると、
# 片方の再生成中にもう片方がデータを同期・push しうる。後から来た方は前の実行が終わるまで待つ
LOCK_FILE="${STATSCAST_DEPLOY_LOCK:-$HOME/.cache/statscast_deploy.lock}"
mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "別の update_and_deploy.sh が実行中。終わるまで待ちます (最大 60 分)..."
  flock -w 3600 9 || { echo "エラー: 60 分待っても前の実行が終わらない ($LOCK_FILE)"; exit 1; }
fi

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
  if [ "$AHEAD" -gt 0 ]; then
    # 手元の commit (スクリプト・docs の修正など) とリモート (PR のマージ等) の両方が進んでいる → 手元を上に積み直す
    git rebase origin/main || { git rebase --abort; echo "エラー: origin/main への積み直しで衝突。手で解消してから再実行"; exit 1; }
    echo "origin/main の上に手元の $AHEAD commit を積み直し完了"
  else
    git merge --ff-only origin/main
    echo "origin/main へ fast-forward 完了"
  fi
fi

# ============================================================
if [ "$MODE" = "csv" ] || [ "$MODE" = "regenerate" ]; then
  step "1/7 データパイプライン ($MODE)"
  if [ "$MODE" = "csv" ]; then
    [ -f "$CSV_PATH" ] || { echo "エラー: $CSV_PATH がない"; exit 1; }
    BACKUP="${MASTER_CSV%.csv}.backup_$(date +%Y%m%d_%H%M%S).csv"
    cp "$MASTER_CSV" "$BACKUP"
    echo "マスターCSVバックアップ: $BACKUP"
    python3 "$REPO_DIR/scripts/prune_master_backups.py" --delete || echo "(古いバックアップの間引きに失敗。取り込みは続行)"
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
  # teams/ と models/ と runexp/ はパイプライン完全所有なので --delete で残骸を残さない。
  # players/batter_zones はフロント未使用 (2026-07確認: ローダー定義のみ残存・
  # バンドル参照ゼロ) のため公開しない。パイプラインは生成し続けるが同期除外。
  rsync -a --exclude 'players/batter_zones' "$PIPELINE_DATA/" "$REPO_DIR/data/"
  # パイプライン完全所有のサブディレクトリは --delete で残骸 (姓名揺れの旧ファイル等) を掃除
  for SUB in teams models runexp players/batter_detail players/pitcher_detail; do
    [ -d "$PIPELINE_DATA/$SUB" ] && rsync -a --delete "$PIPELINE_DATA/$SUB/" "$REPO_DIR/data/$SUB/"
  done
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
git status -s | { grep -E "^[MADR]" || true; } | head -8 || true  # head早期closeのSIGPIPE(141)をpipefailから守る
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
SHA=$(git rev-parse HEAD)
git push origin main
echo "push 完了。この commit の GitHub Actions を待機..."

if command -v gh >/dev/null; then
  # この commit の run だけを見る (直前の run を拾うと「前回の成功」を見て偽の成功/失敗になる。2026-09-29 実例)。
  # Pages は deploy.yml と pages-build-deployment の 2 本が走るので、全部の完了を待って 1 本でも失敗なら止める
  # (以前は結論を表示するだけで、データだけの更新では失敗しても「デプロイ完了」と出ていた)
  RUNS=""
  for i in $(seq 1 30); do
    RUNS=$(gh run list --commit "$SHA" --json databaseId -q '.[].databaseId' 2>/dev/null || echo "")
    [ -n "$RUNS" ] && break
    sleep 5
  done
  [ -n "$RUNS" ] || { echo "エラー: この commit ($SHA) の Actions が 150 秒たっても始まらない"; exit 1; }
  for i in $(seq 1 60); do
    PENDING=$(gh run list --commit "$SHA" --json status -q '[.[] | select(.status != "completed")] | length' 2>/dev/null || echo 1)
    [ "$PENDING" = "0" ] && break
    sleep 10
  done
  gh run list --commit "$SHA" --json name,conclusion -q '.[] | "  Actions: \(.name) → \(.conclusion)"' 2>/dev/null || true
  FAILED=$(gh run list --commit "$SHA" --json conclusion -q '[.[] | select(.conclusion != "success")] | length' 2>/dev/null || echo 1)
  [ "$FAILED" = "0" ] || { echo "エラー: GitHub Actions が成功していない (gh run list --commit $SHA で確認)"; exit 1; }

  CURR_JS=$(grep -o 'index-[A-Za-z0-9_-]*\.js' index.html | head -1)
  # Pages の配信切替は Actions 完了から数十秒遅れることがあるので、新バンドルが出るまで最大3分待つ。
  # ?v=<commit> を付けて CDN のキャッシュを避ける
  LIVE_JS=""
  for i in $(seq 1 18); do
    LIVE_JS=$(curl -s --max-time 15 "$LIVE_BASE/?v=$SHA" | grep -o 'index-[A-Za-z0-9_-]*\.js' | head -1)
    [ "$LIVE_JS" = "$CURR_JS" ] && break
    sleep 10
  done
  for u in "/" "/assets/$CURR_JS" "/data/years.json"; do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" --retry 2 --max-time 15 "$LIVE_BASE$u")
    printf "  %-30s -> %s\n" "$u" "$CODE"
    [ "$CODE" = "200" ] || { echo "エラー: 本番URL異常 ($u)"; exit 1; }
  done
  [ "$LIVE_JS" = "$CURR_JS" ] && echo "✅ 本番が新バンドル ($LIVE_JS) を配信中" \
    || { echo "エラー: 本番JS不一致 live=$LIVE_JS expected=$CURR_JS"; exit 1; }
  # データの更新が本番に出たか: この commit で変わったデータを 1 本、中身で照合する
  DATA_FILE=$(git diff --name-only HEAD~1 HEAD -- data | grep '\.json$' | head -1 || true)
  if [ -n "$DATA_FILE" ]; then
    WANT=$(sha256sum "$DATA_FILE" | cut -c1-16)
    GOT=""
    for i in $(seq 1 18); do
      GOT=$(curl -s --max-time 30 "$LIVE_BASE/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$DATA_FILE")?v=$SHA" | sha256sum | cut -c1-16)
      [ "$GOT" = "$WANT" ] && break
      sleep 10
    done
    [ "$GOT" = "$WANT" ] && echo "✅ 本番のデータも更新済み ($DATA_FILE)" \
      || { echo "エラー: 本番のデータが古いまま ($DATA_FILE)"; exit 1; }
  fi
else
  echo "gh CLI なし: 1-2分後に $LIVE_BASE を手動確認してください"
fi

echo ""; echo "=============================================="
echo " デプロイ完了! $LIVE_BASE"
echo "=============================================="
