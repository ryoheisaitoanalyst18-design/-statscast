# BACKLOG — statscast 公開リポジトリ

実装粒度まで仕様化済みのタスク。上から優先。CLAUDE.md の「絶対に守ること」を先に読むこと。
夜間エージェントは1タスク=1PR、mainへ直pushしない。

## #1 players/*_detail の残骸ファイル掃除スクリプト

**背景**: 名前正規化がプロセス毎にPYTHONHASHSEEDで姓名順反転するため、`data/players/pitcher_detail/`・`batter_detail/` に過去runの姓名順違い残骸が蓄積している(2026-06時点で投手408中60が残骸)。残骸はフロント未参照だが、新キー(twoStrike等)が無く「データ欠落」に見える事故源。

**仕様**: `scripts/cleanup_stale_details.py`
- 正: 全 `data/yearData_*.json` のリーダーボードに現れる選手名(=現行正規名)の和集合
- `*_detail/*.json` のうち正に無いファイルを残骸と判定
- **既定はdry-run**(削除対象の一覧と件数を表示するのみ)。`--delete` で実削除
- 安全ガード: 削除前に `scripts/validate_data.py data --skip-html` を実行し、削除後にも再実行してFAIL 0を確認。1件でもFAILしたら中断
- 出力: 削除対象リストを `scripts/cleanup_report_{date}.txt` に保存

## #2 docs/OPERATIONS.md — 運用知識のリポジトリ内完結化

**背景**: デプロイの事故履歴・落とし穴がローカルマシンのメモリ依存で、別環境のエージェント/人間に伝わらない。

**仕様**: CLAUDE.mdより詳しい運用書 `docs/OPERATIONS.md` を新設。含めるべき既知事項: ①assetsは丸ごと同期(3D遅延チャンクのハッシュ問題) ②404.htmlはindexとペアでsed(ハッシュは別値のことがある) ③push前に必ず fetch+ff(夜間PRマージでローカルが遅れる) ④noindexは意図的(削除禁止) ⑤private化禁止(無料プランはPages停止、復旧はPages API再有効化+run rerun) ⑥ `data/dates_2026.json`・`data/players/batter_zones/` は公開除外(再生成で復活したら再削除) ⑦assets孤立ファイルの掃除はHTML起点の到達closure方式 ⑧検証ゲート件数はデータ依存で可変、基準は「FAIL/WARN=0」。

## #3 リモートの夜間PRブランチの棚卸し(人間判断)

`auto/fix-20260704-update-readme` と `auto/fix-20260705-validate-bundle-hash-parity` がリモートに未マージで滞留。レビューしてマージ/クローズする。以後も夜間PRは週1で棚卸しする。

## #4 検証ゲートへの追加チェック

`scripts/validate_data.py` に追加: ①index.html/404.htmlが参照する `assets/index-*.js/css` の実在+両HTMLのハッシュ一致 ②noindexメタの存在(両HTML) ③ `batter_zones/`・`dates_2026.json` が紛れ込んでいないこと。※#3のPR(validate-bundle-hash-parity)と重複する可能性があるため、着手前にそのPRの内容を確認し、重複なら差分だけ実装。
