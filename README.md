# 東京六大学野球 StatCast

TrackMan データを使った打者成績ビジュアライゼーションサイト。

## 使い方

### データの読み込み方法

**方法1: Claude Code 経由（推奨）**
1. TrackMan CSVファイルをClaudeに渡す
2. Claudeが `data/data.csv` として保存
3. サイトを開くと自動で読み込まれる

**方法2: ブラウザからアップロード**
1. サイトを開き、CSVファイルをドラッグ＆ドロップ
2. データはブラウザに保存される

### ローカルでの起動

```bash
cd statscast
python3 -m http.server 8080
# ブラウザで http://localhost:8080 を開く
```

## 表示内容

### トップページ
- 打者一覧（チーム別フィルター・名前検索）
- 打数・安打・打率・三振・四球

### 打者詳細ページ（打者名をクリック）
- **コース別打率**: そのコースの安打 ÷ そのコースへの結果球（打数）
- **空振り率**: そのコースの空振り ÷ そのコースへの全投球
- **ゴロ率**: そのコースのゴロ ÷ そのコースで打った打球全体
- 球種フィルター（全球種 / ストレート / スライダー など）
- **投球散布図**: 見逃し・空振り・安打を選択表示（ボールは非表示）

## TrackMan CSVフォーマット

TrackManのエクスポートCSVをそのまま使用可能。
主要カラム: `Batter`, `BatterTeam`, `PitchCall`, `KorBB`, `PlayResult`, `TaggedHitType`, `TaggedPitchType`, `PlateLocSide`, `PlateLocHeight`
