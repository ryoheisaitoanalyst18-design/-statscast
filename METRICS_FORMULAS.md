# 指標 計算式リファレンス

出典: `/home/analyst18/tokyo-baseball/update_season.py`（デプロイで実際に使われる集計スクリプト）
元データ: Trackman CSV（1行 = 1球）

**集計前の除外 (`data_integrity.py`, 2026-09-29〜)**: 同じ投球の重複 (PitchUID/PitchNo が同じで中身も同じ・
物理量の完全一致)、同じ試合の二重登録、**判定 (PitchCall) の無い行** を除いてから集計する。
判定なしの行は全量監査で「判定付きの行の物理量コピー」「イニング間の投球練習」「判定漏れ」の3種と判明し、
打席結果を持つものは無かった。除去後に重複が残っていればパイプラインが停止する。

---

## 0. 前提となる行レベル判定フラグ

各球（行）に対して以下のフラグを立て、選手・年度ごとに合計する。

| フラグ | 定義 |
|---|---|
| `is_strike` | PitchCall ∈ {StrikeCalled, StrikeSwinging, FoulBall, InPlay, FoulBallNotFieldable, FoulBallFieldable, StrikeSwinging(no out)} |
| `is_swing` | PitchCall ∈ {StrikeSwinging, InPlay, FoulBall, FoulBallNotFieldable, FoulBallFieldable, StrikeSwinging(no out)} |
| `is_whiff` | PitchCall ∈ {StrikeSwinging, StrikeSwinging(no out)} |
| `is_csw` | PitchCall ∈ {StrikeCalled, StrikeSwinging, StrikeSwinging(no out)} |
| `is_contact` | is_swing かつ not is_whiff |
| `is_hbp` | PitchCall == HitByPitch |
| `is_inplay` (=BBE) | PitchCall == InPlay |
| `is_pa` | PlayResult ∈ {Single,Double,Triple,HomeRun,Out,Error,Sacrifice,FieldersChoice} **または** KorBB ∈ {Strikeout,Walk} **または** is_hbp |
| `is_hit` | PlayResult ∈ {Single,Double,Triple,HomeRun} |
| `is_k` | KorBB == Strikeout |
| `is_bb` | KorBB == Walk |
| `is_sac` | PlayResult == Sacrifice **または**（TaggedHitType==Bunt かつ PlayResult==Out かつ アウトカウント≦1） |
| `is_sf` (犠飛) | is_sac かつ 打球タイプ (TaggedHitType、無ければ AutoHitType) が FlyBall / LineDrive / Popup |
| `is_sh` (犠打) | is_sac かつ not is_sf |
| `is_ab` | is_pa かつ not is_bb かつ not is_hbp かつ not is_sac |
| `outs_rec` | その球の間に記録されたアウト数 = 次の球の Outs − この球の Outs（ハーフ最後の球は 3 − Outs、試合の最終ハーフの最後の球はそのプレーのアウト = OutsOnPlay + 三振） |
| `is_hard_hit` | ExitSpeed ≧ 閾値（下記注記参照） |
| `is_sweet_spot` | 打球角度 Angle ∈ [8°, 32°] |
| `is_zone` | PlateLocHeight ∈ [0.48, 1.09] m かつ PlateLocSide ∈ [-0.253, 0.253] m |

`safe_div(a,b)` = `a/b`（b>0 のとき）、それ以外は 0。

---

## 1. 打者指標（batterLeaderboard / teamBatting / 対右・対左）

### カウント系
- **PA** = Σ is_pa（打席）
- **AB** = Σ is_ab（打数）
- **H** = Σ is_hit ＝ 1B + 2B + 3B + HR
- **1B / 2B / 3B / HR** = 各 PlayResult の合計
- **BB** = Σ is_bb、**HBP** = Σ is_hbp、**K** = Σ is_k、**SAC** = Σ is_sac（= **SF** 犠飛 + **SH** 犠打）
- **TB（塁打）** = 1B + 2×2B + 3×3B + 4×HR
- **BBE（インプレー打球数）** = Σ is_inplay

### 率系
| 指標 | 計算式 |
|---|---|
| **AVG（打率）** | H / AB |
| **OBP（出塁率）** | (H + BB + HBP) / (PA − SH) ＝ (H+BB+HBP)/(AB+BB+HBP+SF)（MLB 定義） |
| **SLG（長打率）** | TB / AB |
| **OPS** | OBP + SLG |
| **ISO** | SLG − AVG |
| **BABIP** | (H − HR) / (AB − K − HR + SF) |
| **K%** | K / PA × 100 |
| **BB%** | BB / PA × 100 |
| **HardHit%** | （インプレー中の is_hard_hit 数）/ BBE × 100 |
| **SwSp%（スイートスポット率）** | （Angle 8〜32°の打球数）/ BBE × 100 |
| **Barrel%（バレル率）** | （バレル打球数）/ BBE × 100。バレル判定は MLB Statcast 定義に準拠し、ExitSpeed ≥ 98mph かつ打球角度が速度依存の帯に入る球（98/99/100/116mph を 26-30°/25-31°/24-33°/8-50° で線形補間、116mph 以上は 8-50°固定）。ExitSpeed が km/h の年度は mph に換算してから判定する。 |

### wOBA（線形ウェイト・定数）
```
wOBA = (0.692·BB + 0.73·HBP + 0.865·1B + 1.334·2B + 1.725·3B + 2.065·HR) / (PA − SH)
```

### wRC+
```
wRC+ = round( wOBA / リーグ平均wOBA × 100 )
```
- リーグ平均wOBA = その集計範囲 (年度/大会/日) の **6大学の全打席を合算した wOBA**（打席数加重）。
  全打者の wRC+ を打席数で加重平均するとほぼ 100 になる（検証ゲートで確認）。
  旧定義（規定打席以上の選手の wOBA の単純平均）は少打席の選手に引っ張られ、平均が 104〜106 にずれていた。
- 簡易版（パークファクター・得点環境の補正なし = wOBA+ 相当）。リーグ平均が 0 のとき 100

### 打球計測系（インプレー打球のみ対象）
| 指標 | 計算式 |
|---|---|
| **AvgEV** | インプレー打球の ExitSpeed 平均 |
| **MaxEV** | ExitSpeed 最大 |
| **AvgLA** | 打球角度 Angle の平均 |
| **EV50** | ExitSpeed を降順に並べ、上位50%（=速い半分）の平均 |
| **Runs** | その打者の打席で記録された RunsScored の合計（≈打点。サイト表記「RBI*/打点(推定)」）。チーム打撃の Runs は暴投・盗塁等を含む全投球の得点 |

---

## 2. 投手指標（pitcherLeaderboard / teamPitching / 対右・対左 / 球種別）

### カウント系
- **TotalPitches** = 投球数（行数）
- **TBF（対戦打者数）** = Σ is_pa
- **AB_against** = Σ is_ab
- **H_against** = Σ is_hit、**BB_pitcher** = Σ is_bb、**HBP_pitcher** = Σ is_hbp、**K_pitcher** = Σ is_k、**HR_against** = Σ is_hr
- **アウト数 (Outs)** = Σ outs_rec（Outs 列の推移。犠打・併殺・牽制死・盗塁死も含む）
- **IP（投球回）** = アウト数 / 3 ※表示は小数1桁に丸め（注記参照）
  - 旧定義（PlayResult ∈ {Out, FieldersChoice} または三振 を1アウト）は犠打・併殺を落とし、1試合のアウトが中央値48（本来51〜54）＝投球回が約1割過小だった。リーグ全体の アウト数/対戦打者 が 0.66〜0.74 に入ることを検証ゲートで確認している

### 率系
| 指標 | 計算式 |
|---|---|
| **AVG_against（被打率）** | H_against / AB_against |
| **WHIP** | (H_against + BB_pitcher) / IP |
| **FIP** | (13·HR + 3·(BB + HBP) − 2·K) / IP + 3.2 |
| **K_BB** | K / BB |
| **Strike%** | strikes / TotalPitches × 100（TotalPitches は判定付きの投球のみ） |
| **Swing%** | swings / TotalPitches × 100 |
| **Whiff%** | whiffs / **swings** × 100 |
| **CSW%** | csw / TotalPitches × 100 |
| **Contact%** | contacts / **swings** × 100 |
| **Zone%** | （is_zone の球数）/（コースが計測された投球数）× 100 |
| **K%** | K / TBF × 100 |
| **BB%** | BB / TBF × 100 |
| **HardHit%_against** | （被インプレーの is_hard_hit）/ インプレー数 × 100 |

### 球質・計測系
- **AvgVelo / MaxVelo** = RelSpeed の平均 / 最大
- **AvgSpinRate** = SpinRate 平均（整数丸め）
- **AvgIVB** = InducedVertBreak 平均、**AvgHB** = HorzBreak 平均
- **AvgEV_against** = 被インプレー打球 ExitSpeed 平均
- **Runs_against（失点）** = 登板中の**全投球**の RunsScored 合計（暴投・捕逸・盗塁等の得点を含む。自責点ではない）
- **AvgSpinEff（平均回転効率）** = SpinEff が算出できた投球の平均（%）。物理域外として棄却された球は分母から除外する。SpinEff = 変化に寄与する有効回転成分（Magnus力を生む回転）/ SpinRate × 100。0〜100% の範囲で、高いほど変化量につながる回転の割合が多い。全球種混合の中央値は概ね 60〜70%。

---

## 3. カウント分析（countAnalysis / countByPitch）

ボールカウント別に集計（キーは整数化した "B-S"。旧実装は "1-2" と "1.0-2.0" に割れて同じカウントが2行になる年があった）：
- **Strike%** = strikes / 総球数 × 100
- **Swing%** = swings / 総球数 × 100
- **Whiff%** = swing-miss / swings × 100
- **Contact%** = contacts / swings × 100
- **InPlay** = インプレー数

---

## 4. 重要な注意点・前提（数字の解釈に影響）

1. **IP（投球回）の表示**
   IP = アウト数 / 3 の正確な値を **小数1桁に丸めて表示**している。
   そのため `0.2`(=⅔=0.667) → 表示 `0.7`、`6.1`(=6⅓) → 表示 `6.3` のように、表示IPは実数の三分割を丸めた値。
   **WHIP / FIP は丸める前の正確なIPから計算**されているので、表示IPで割り直すと一致しないことがあるが、これは正常。

2. **ハードヒット閾値（単位混在対策）**
   データのExitSpeed中央値が 60 未満なら閾値 **95（mph相当）**、それ以上なら **148（km/h相当）** を自動採用。
   ExitSpeed ≧ 185 は外れ値として除外。

3. **OBP・wOBA の分母は `PA − SH`**（犠打だけを除き、犠飛は含める = MLB 定義。2026-09-29 に `PA − SAC` から変更）。

4. **AB の定義** = is_pa から BB・HBP・SAC を除いたもの。よって `PA = AB + BB + HBP + SAC` が成立する
   （検証ゲートで全選手について確認）。3ボールからの死球で TrackMan が `KorBB='Walk'` も立てる行（45球）は
   `is_bb = KorBB=='Walk' かつ not is_hbp` で死球として1回だけ数える（2026-08 修正済み）。

5. **ストライクゾーン定義** = 高さ 0.48〜1.09 m、横 ±0.253 m（ホームベース幅 43.2cm + ボール半径）。
   TrackMan の PlateLocSide は**正が三塁側**（右打者側）。サイトのコース図・九分割はすべて投手目線で左が一塁側。

6. **スイートスポット** = 打球角度 8〜32°。

7. **規定打席/規定打者数**: 成功モデル（上位/下位20人比較）は PA≧30・TBF≧30。wRC+ の基準は規定打席に依存しない（上記）。

8. **単位**: 2019年の21試合は TrackMan がインペリアル設定（mph / inch / ft）のまま納品されている。試合単位で検出し、
   球速・変化量・位置・**飛距離・9P軌道・打球/投球軌道係数** まで全てメトリクに換算してから集計する（`unit_normalize.py`）。

---

## 5. 結果球（resultPitches — 選手詳細の「結果球の分析」「2ストライク後の成績」）

**結果球** = 打席を決着させた1球。`is_pa` が立つ球（インプレー・三振・四死球）そのもので、
結果カテゴリは 1B/2B/3B/HR/out/K_swing/K_look/BB/HBP/**sac（犠打）/sf（犠飛）**。
1打席につき必ず1球。したがって `len(resultPitches)` = その選手の全年度 PA。

- 生成: `update_season.py` の `pa_result_pitches()` → `players/*_detail/*.json` の `resultPitches`
- 「2ストライク後」ビューはその **`strikes == 2` の部分集合**
  （ストライクは2で頭打ちになり以後維持されるため 終端球 strikes==2 ⟺ 2ストライク到達）
- `balls`/`strikes` は **その球を投じる前**のカウント（TrackMan の値そのまま）
- `ev`/`la` は打球になった球にだけ持たせる（「打ったコース」の打球質表示用）

フロント側の集計（ダッシュボードとは分母が異なるので注意）:
- **打数** = 安打 + 凡打 + 三振（四死球・犠打は除外。§1 の AB と同じ定義）
- **打率** = 安打 / 打数、**長打率** = 塁打 / 打数
- **出塁率** = (安打 + 四球 + 死球) / (打席 − 犠打)（犠飛は分母に残る = ダッシュボードと同じ）
- **三振率 / 四球率** = 三振 or 四球 / 打席
- **インプレー率** = (安打 + 凡打) / 打席、**初球決着率** = 0-0 で終わった打席 / 打席
- **投げたコース** = 結果球全球の散布（投手目線、色=結果カテゴリ・形=球種）
- **打ったコース** = 打球になった結果球のみを 5×5 ゾーン集計。内側3×3=ストライクゾーン9分割、
  外周1マス=ボールゾーン（描画レンジ外の球は外周へクランプして落とさない）。
  打数2未満のマスは打率を出さない（1打数の .000/1.000 を色にしないため）。

---

## 6. 選手詳細ページの「九分割コース分析」（`client/src/lib/zoneStats.ts`）

選択中の年度・日付・大会・対戦左右で絞った投球と打球から、ブラウザ側で集計する
（投手ページは旧実装だと全期間の集計を固定表示していた）。
- **空振り率** = 空振り / スイング（スイング = 空振り・ファウル・インプレー）
- **打率** = 安打 / 打数（打数 = インプレーのうち犠打・犠飛を除く）
- **ゴロ率・HH率** = インプレー打球に対する割合（ゴロ = TaggedHitType、HH = 打球速度 148km/h 以上）

## 7. Run Value（`run_expectancy.json`、/run-value ページ）

パイプラインが毎回生成する（旧ファイルは 2026-05 から更新が止まっていた）。
- **RE Matrix** = カウント×アウト（塁状況なし）から半回終了までの平均得点
- **1球の Run Value** = カウント別の打席期待値の変化（打席終端は線形ウェイト − 現カウントの期待値）。打者視点で負 = 投手有利。Stuff+ の目的変数と同じ定義
- 投手ランキングは全年度・6大学の投手

## 8. 守備分析（`defenseData.json`、/defense ページ）

TrackMan のポジショニング CSV（`~/ubuntu_data/positioning/`）と投球データを PitchUID で突き合わせた
インプレー打球。**1打球1レコード**（旧ファイルは 11,377件中 4,067件が重複していた）。
