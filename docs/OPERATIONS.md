# OPERATIONS.md — 運用書 (事故履歴と落とし穴)

CLAUDE.md の「絶対に守ること」の**根拠と復旧手順**をリポジトリ内で完結させるための文書。
別環境のエージェント・人間が、ローカルマシンのメモリに頼らず安全に運用できることが目的。
まず CLAUDE.md を読み、詳細が必要になったらここを読む。

## 大原則

- 変更は必ず `./update_and_deploy.sh` 経由。手作業での assets/ 差し替え・data/ 直編集はしない。
- 何か変えたら必ず `python3 scripts/validate_data.py`。**基準は「FAIL 0 / WARN 0」**。
- main への push = 本番デプロイ (GitHub Pages)。push する前に必ず fetch する。

## 落とし穴カタログ

### ① assets/ は丸ごと同期する (部分コピー禁止)

フロントは Vite のコード分割により、3D 表示 (神宮球場モデル等) を**遅延チャンク**として
実行時に動的 import する。`index-*.js` だけを差し替えると、その中に埋め込まれた
遅延チャンクのハッシュ参照と実ファイルが食い違い、**トップは表示されるのに 3D だけ壊れる**。
必ず `assets/` ディレクトリ全体を同期する (update_and_deploy.sh は rsync --delete で実施)。

### ② 404.html は index.html とペアで更新する

404.html は GitHub Pages 上で SPA ディープリンク (選手詳細 URL の直接アクセス等) の
フォールバックとして index.html と同じバンドルを読む。index.html だけハッシュを差し替えると
**ディープリンクだけ古いバンドルで開く**。JS/CSS のハッシュは別値のことがあるため、
sed は JS と CSS を個別に両ファイルへ適用する。

事故履歴: 2026-06-27〜07-03、デプロイスクリプトの同期漏れにより夜間エージェントが
同じ修正 PR を 7 夜連続で作成 (PR #14〜#20)。07-04 のスクリプト全面書き直しで根治し、
validate_data.py の「バンドルハッシュ一致」チェックが再発を FAIL で検知する。

### ③ push 前に必ず fetch + fast-forward

夜間エージェントの PR マージや GitHub 上での直編集で、ローカル main が
**リモートより遅れていることがある**。気づかず push すると non-ff で拒否され、
ここで force push すると リモート側の変更 (マージ済み PR 等) を消し飛ばす。
`git fetch` → `git pull --ff-only` → 作業 → push の順を守る (update_and_deploy.sh は
preflight で自動実施)。force push は原則禁止。

### ④ noindex は意図的な設定 (削除禁止)

index.html / 404.html の `<meta name="robots" content="noindex, nofollow">` は
**検索エンジン非掲載という運用方針**による意図的な設定。「SEO 改善」と誤認して
削除しない。validate_data.py が両ファイルの noindex 存在を FAIL で検知する。

### ⑤ リポジトリの private 化禁止

無料プランでは private リポジトリで GitHub Pages が使えない。private に切り替えると
**Pages が即座に停止しサイトが真っ白になる** (2026-06 に実際に発生)。

復旧手順:
1. リポジトリを public に戻す
2. Pages を API で再有効化:
   `gh api repos/{owner}/{repo}/pages -X POST -f build_type=workflow` (状態により PUT)
3. 最新のデプロイ workflow run を rerun: `gh run rerun <run-id>` (または空コミット push)

### ⑥ 公開除外物 (コミット禁止のパイプライン出力)

以下はパイプラインが生成しローカル data/ に存在してよいが、**公開リポジトリには
コミットしない** (フロントが参照しておらず、公開データを最小化する方針のため):

- `data/players/batter_zones/` — ローダーだけ存在しコンポーネント未使用
- `data/dates_2026.json` — フロントのバンドルに参照なし (日付フィルタは yearData 側から取得)

いずれも .gitignore 済み。**再生成や rsync でローカルに復活するのは正常**で、
消す必要はない — コミットに混ざることだけが事故。validate_data.py の
「公開除外チェック」が、追跡されている/ignore が外れている場合に FAIL する。
もし過去に紛れ込んでいたら `git rm --cached <path>` で追跡だけ解除する。

### ⑦ assets/ の孤立ファイル掃除は「到達 closure」方式で

遅延チャンクは index.html からは参照されず、**JS の中から動的 import される**。
そのため「index.html に名前が出てこない = 不要」は誤りで、grep 一致だけで消すと
3D が壊れる (①と同根)。掃除するときは:

1. index.html / 404.html が参照する JS/CSS を起点集合にする
2. 起点 JS の中身から `assets/xxx-HASH.js` 形式の参照を再帰的に辿る
3. こうして到達できる closure に**含まれない**ファイルだけが孤立候補

確信が持てなければ消さない。ビルドし直して assets/ を丸ごと同期する方が安全。

### ⑧ 検証ゲートの読み方

`python3 scripts/validate_data.py [DATA_DIR] [--skip-html]`

- **チェック件数はデータ依存で可変** (年度数・日別ファイル数・モデル有無で増減する)。
  「以前は 61 件だった」という件数比較に意味はなく、基準は常に **FAIL 0 / WARN 0**。
- `--skip-html` はパイプライン出力側 (リポジトリ外の data/) を検証するとき用。
  公開リポジトリの検証では付けない (HTML 整合と公開除外チェックが省かれるため)。
- FAIL があるのに push しない。デプロイスクリプトはゲート失敗で自動中断する。

## 定常メンテナンス

### 残骸詳細ファイルの掃除

名前正規化が PYTHONHASHSEED の影響でプロセス毎に姓名順反転しうるため、過去の
パイプライン実行の残骸 JSON が `data/players/*_detail/` に溜まることがある。
残骸はフロント未参照だが「データ欠落」に見える事故源。

```bash
python3 scripts/cleanup_stale_details.py [DATA_DIR]            # dry-run: 対象一覧と件数
python3 scripts/cleanup_stale_details.py [DATA_DIR] --delete   # 実削除 (前後で validate 自動実行)
```

正解集合は「全 yearData_*.json のリーダーボード名の和集合」。選手 JSON を調査するときも
必ずリーダーボード名から辿ること (ディレクトリ全走査は残骸に惑わされる)。

**重要: 必ずパイプライン出力側 (`~/statscast/data`) も掃除する。** データ同期は
パイプライン→リポジトリの `rsync --delete` ミラーのため、リポジトリ側だけ削除しても
パイプライン側に残骸が残っていると**次回のデータデプロイで復活し再コミットされる**。
手順: `DATA_DIR` にパイプライン側を指定して dry-run→--delete → リポジトリ側も同様に実行。

### 夜間エージェント PR の棚卸し (週 1)

夜間エージェントは毎晩 1 タスク = 1 PR を作る。**マージもクローズもされない PR を放置すると、
同じ問題が直っていないと判定されて同種 PR が毎晩積まれる** (実例: PR #14〜#20)。
週 1 回レビューし、必ずマージかクローズで消化する。判断基準:

- 内容が正しく現 main に適用可能 → squash マージ + ブランチ削除
- 根本原因が既に解消済み / 古い main へのパッチ → 理由をコメントしてクローズ
- 迷ったら: 現 main で問題が再現するかを検証ゲートで確かめてから判断

### データ投入・再生成

新しい試合 CSV の投入や球種修正後の再生成はパイプライン側リポジトリの仕事。
このリポジトリ側では必ず update_and_deploy.sh を使う (バックアップ・東都混在チェック・
検証ゲート込み)。data/ の JSON を手で直さない — 直すのはパイプライン、ここは成果物置き場。

### 試合動画の紐づけ (新しい試合を追加するとき)

球のポップアップから YouTube の試合動画に飛ぶ機能。動画は**限定公開**でアップロードし、
動画IDだけを公開データに載せる (動画実体はこのリポジトリに入れない)。

```bash
# 0. 前提: Watson1 (~/diamondlab) 側でイニング別クリップに球をタグ付け済みであること
python3 scripts/video/concat_game_video.py --dry-run     # 構成と尺の確認
python3 scripts/video/concat_game_video.py               # 連結 → ~/.diamondlab/videos/upload/
# 1. (任意) TrackMan データを映像に焼き込む
python3 scripts/video/make_overlay.py --game-uid <UID> --duration 300   # まず試し焼き
python3 scripts/video/make_overlay.py --game-uid <UID>                  # 全編 (約24分/試合)
# 2. {試合UID}.mp4 (焼き込むなら {試合UID}_overlay.mp4) を YouTube に「限定公開」でアップロード
# 3. 動画IDを scripts/video/youtube_ids.json に記入
python3 scripts/video/export_video_links.py              # → data/videos/links.json
./update_and_deploy.sh --data-only
```

**焼き込み版は元の連結動画と尺・タイムラインが完全に同一** (トリムせず再エンコードするだけ)。
`links.json` の再生位置はそのまま使えるので、焼き込み版を上げればサイトからのジャンプ先も
データ付き映像になる。プレーン版を別に上げる必要はない。
**ただし `--start` / `--duration` を付けた出力は先頭がズレる**ので、試し焼き用と割り切ること。

落とし穴:

- **1試合1本に連結してからアップする**。Watson1 の紐づけはイニット別クリップ単位だが、
  17本を個別にアップすると動画IDの収集が現実的でない。連結マニフェストが
  「クリップ内秒数 → 通し秒数」の変換を持つので、連結を挟まないと再生位置が出せない。
- **1試合100分超**。YouTube の15分制限を外すにはアカウントの電話番号確認が必要。
- **鍵は TrackMan の生値そのもの** (`日付|投手|投球位置` と `日付|打球速度|角度|飛距離`)。
  公開JSONに GameUID も PitchNo も無いための方式で、パイプラインが単位換算や丸めを
  変えると**無言で全滅する**。検証ゲートが実データ照合で到達率を見ているので、
  `videos/links.json: 球キーが公開データに当たらない` が出たら export を再実行する。
- **`direction` を鍵に使わない**。公開側で座標変換されていて Watson1 の値と一致しない
  (`ev`/`la`/`distance` は生値が素通りしている)。
- `data/videos/` は rsync の `--delete` 対象外なので、データ再生成では消えない。
