# CLAUDE.md — ROKUDAI STATCAST 公開リポジトリ

東京六大学野球の Statcast 風分析サイト。**このリポジトリはビルド成果物と公開データの置き場**であり、ソースコードはここには無い。main への push で GitHub Pages に自動デプロイされる。

## 全体構成 (3リポジトリ構成)

```
/home/analyst18/tokyo-baseball/     … データパイプライン (Python)。update_season.py が本体
/home/analyst18/rokudai_statcast/   … フロントエンドソース (Vite + React, client/)
/home/analyst18/-statscast/         … ★このリポジトリ。data/ + assets/ + index.html を公開
```

マスターデータ: `/home/analyst18/ubuntu_data/trackman_data.csv` (~146MB、TrackMan全投球)。
パイプライン出力先は `/home/analyst18/statscast/data` (ダッシュ無し)。ここへは rsync で同期される。

## 絶対に守ること

1. **assets/ を手で編集しない**。ビルド成果物。差し替えは必ず `./update_and_deploy.sh` 経由
   (index-*.js だけコピーすると 3D 遅延チャンクのハッシュ不整合でサイトが壊れる)。
2. **index.html と 404.html はペア**。バンドルハッシュは両方同時に差し替える
   (404.html は SPA ディープリンクのフォールバックで同じバンドルを読む)。
3. **`<meta name="robots" content="noindex, nofollow">` は意図的な設定** (検索非掲載)。
   SEO 改善と誤認して削除しない。
4. **無料プランのため private 化禁止** (private にすると Pages が落ちてサイトが真っ白になる)。
5. data/ の JSON は手で直さない。修正はパイプライン側 (tokyo-baseball) で行い再生成する。
6. コミット前に必ず `git fetch` してリモートとの乖離を確認する
   (リモート直編集や夜間PRで local が behind になっていることがある)。

## 検証 (何か変更したら必ず実行)

```bash
python3 scripts/validate_data.py          # データ+HTML の整合性ゲート (stdlib のみ)
```

- 指標の不変条件 / リーグwOBA / 選手詳細ファイル実在 / モデル健全性 / HTML参照アセット / noindex
- デプロイスクリプトが自動実行するが、単体でも動く。**FAIL があるのに push しない**。

## デプロイ

```bash
./update_and_deploy.sh /path/to/new.csv [--clean]  # 新CSVマージ→全再生成→デプロイ
./update_and_deploy.sh --regenerate                # マスターから全再生成→デプロイ
./update_and_deploy.sh --data-only                 # データ同期のみ
./update_and_deploy.sh --frontend-only             # フロントのみ
# いずれも --no-push でドライラン
```

本番: https://ryoheisaitoanalyst18-design.github.io/-statscast/

## data/ の構成

| パス | 中身 | 形式 |
|---|---|---|
| `yearData_{year}.json` | 年度別リーダーボード+チーム集計 | tRPCラップ (`[{result:{data:{json:…}}}]`) |
| `yearData_2026_{league,fresh}.json` | 2026大会別 (cutoff 6/2) | tRPCラップ |
| `yearData_2026_YYYY-MM-DD.json` | 日別 (日付フィルタで使用中、消さない) | tRPCラップ |
| `players/{batter,pitcher}_detail/` | 選手詳細 (名前.json) | 生JSON |
| `teams/tendencies_{scope}.json` | チーム傾向 (ゾーン/カウント/強み弱み) | 生JSON |
| `models/stuffplus_{scope}.json` | Stuff+ リーダーボード | 生JSON |
| `models/xwoba_{scope}.json` | xwOBA/xBA/xSLG (batters+pitchers) | 生JSON |
| `models/xwoba_grid.json` / `models_meta.json` | ランドスケープ / 方法論・検証値 | 生JSON |
| `run_expectancy.json` / `defenseData.json` | RE行列 / 守備 (手動系、消さない) | — |

`players/batter_zones/` はフロント未使用のため公開しない (.gitignore 済み)。

## 既知の注意点

- 選手詳細ファイルには姓名順の揺れによる**残骸**が混ざりうる。選手JSONを調べるときは
  必ず yearData のリーダーボード名から辿る (全走査すると残骸に惑わされる)。
- TrackMan CSV には東都大学リーグの試合が混在することがある。マージは必ず
  update_and_deploy.sh (バックアップ+--clean) 経由で。
- 詳細な運用メモ・事故履歴はローカルの Claude メモリ (`statscast-deploy-workflow` 等) にある。

## 夜間エージェント (自動改善) への指針

- 変更は**1件だけ**、作業ブランチ+PR。main 直 push・デプロイ・シークレット操作は禁止。
- まず `python3 scripts/validate_data.py` を実行し、FAIL があればその修正を最優先。
- 次点: index/404 のハッシュ整合、参照切れアセット、リンク切れ、docs の誤り。
- data/ の数値の「修正」はしない (パイプライン側の仕事)。
