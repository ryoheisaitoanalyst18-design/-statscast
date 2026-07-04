# 東京六大学野球 StatCast

TrackMan 計測データを基に、打者・投手の Statcast 風指標・3D 軌道可視化・モデル出力（Stuff+, xwOBA）を公開する分析サイト。

**公開 URL**: https://ryoheisaitoanalyst18-design.github.io/-statscast/

## 主な機能

### 打者・投手リーダーボード
- 打者: AVG / OBP / SLG / wOBA / wRC+ / K% / BB% / HardHit% / xwOBA 等
- 投手: Whiff% / CSW% / Zone% / FIP / WHIP / Stuff+ 等
- 球速・回転数・変化量（IVB / HB）

### 選手詳細ページ
- コース別・カウント別の成績分布
- 打球軌道 / 投球軌道の 3D 可視化（神宮球場モデル）

### チーム傾向分析
- ゾーン別打率・空振り率（対右・対左）
- カウント別投球戦略

### 高度モデル
- **Stuff+**: 投球の質を 100 標準化したスコア（球種・球速・変化量を統合）
- **xwOBA / xBA / xSLG**: 打球品質ベースの期待値指標

## ローカル確認

```bash
python3 -m http.server 8080
# ブラウザで http://localhost:8080 を開く
```

## 指標の計算式

[METRICS_FORMULAS.md](METRICS_FORMULAS.md) を参照。

## リポジトリ構成

このリポジトリはビルド成果物（`assets/`）と公開データ（`data/`）の置き場です。
フロントエンドのソースコードとデータパイプラインはそれぞれ別リポジトリで管理されており、
`main` への push が GitHub Pages への本番デプロイになります。

`<meta name="robots" content="noindex, nofollow">` は意図的な設定（検索非掲載）です。
