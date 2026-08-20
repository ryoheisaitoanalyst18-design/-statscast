# 指標 計算式リファレンス

出典: `/home/analyst18/tokyo-baseball/update_season.py`（デプロイで実際に使われる集計スクリプト）
元データ: Trackman CSV（1行 = 1球）

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
| `is_ab` | is_pa かつ not is_bb かつ not is_hbp かつ not is_sac |
| `is_hard_hit` | ExitSpeed ≧ 閾値（下記注記参照） |
| `is_sweet_spot` | 打球角度 Angle ∈ [8°, 32°] |
| `is_zone` | PlateLocHeight ∈ [1.5, 3.5] かつ PlateLocSide ∈ [-0.83, 0.83] |

`safe_div(a,b)` = `a/b`（b>0 のとき）、それ以外は 0。

---

## 1. 打者指標（batterLeaderboard / teamBatting / 対右・対左）

### カウント系
- **PA** = Σ is_pa（打席）
- **AB** = Σ is_ab（打数）
- **H** = Σ is_hit ＝ 1B + 2B + 3B + HR
- **1B / 2B / 3B / HR** = 各 PlayResult の合計
- **BB** = Σ is_bb、**HBP** = Σ is_hbp、**K** = Σ is_k、**SAC** = Σ is_sac
- **TB（塁打）** = 1B + 2×2B + 3×3B + 4×HR
- **BBE（インプレー打球数）** = Σ is_inplay

### 率系
| 指標 | 計算式 |
|---|---|
| **AVG（打率）** | H / AB |
| **OBP（出塁率）** | (H + BB + HBP) / (PA − SAC) |
| **SLG（長打率）** | TB / AB |
| **OPS** | OBP + SLG |
| **ISO** | SLG − AVG |
| **BABIP** | (H − HR) / (AB − K − HR) |
| **K%** | K / PA × 100 |
| **BB%** | BB / PA × 100 |
| **HardHit%** | （インプレー中の is_hard_hit 数）/ BBE × 100 |
| **SwSp%（スイートスポット率）** | （Angle 8〜32°の打球数）/ BBE × 100 |

### wOBA（線形ウェイト・定数）
```
wOBA = (0.692·BB + 0.73·HBP + 0.865·1B + 1.334·2B + 1.725·3B + 2.065·HR) / (PA − SAC)
```

### wRC+
```
wRC+ = round( wOBA / リーグ平均wOBA × 100 )
```
- リーグ平均wOBA = 規定打席（年度別は PA≧20、All集計は PA≧2）を満たす打者の wOBA の単純平均
- 算出不能時は 100、リーグ平均が 0 のとき 100

### 打球計測系（インプレー打球のみ対象）
| 指標 | 計算式 |
|---|---|
| **AvgEV** | インプレー打球の ExitSpeed 平均 |
| **MaxEV** | ExitSpeed 最大 |
| **AvgLA** | 打球角度 Angle の平均 |
| **EV50** | ExitSpeed を降順に並べ、上位50%（=速い半分）の平均 |
| **Runs** | その打者の打席で記録された RunsScored の合計 |

---

## 2. 投手指標（pitcherLeaderboard / teamPitching / 対右・対左 / 球種別）

### カウント系
- **TotalPitches** = 投球数（行数）
- **TBF（対戦打者数）** = Σ is_pa
- **AB_against** = Σ is_ab
- **H_against** = Σ is_hit、**BB_pitcher** = Σ is_bb、**HBP_pitcher** = Σ is_hbp、**K_pitcher** = Σ is_k、**HR_against** = Σ is_hr
- **アウト数** = Σ is_out（is_out = PlayResult ∈ {Out, FieldersChoice} または is_k）
- **IP（投球回）** = アウト数 / 3 ※表示は小数1桁に丸め（注記参照）

### 率系
| 指標 | 計算式 |
|---|---|
| **AVG_against（被打率）** | H_against / AB_against |
| **WHIP** | (H_against + BB_pitcher) / IP |
| **FIP** | (13·HR + 3·(BB + HBP) − 2·K) / IP + 3.2 |
| **K_BB** | K / BB |
| **Strike%** | strikes / TotalPitches × 100 |
| **Swing%** | swings / TotalPitches × 100 |
| **Whiff%** | whiffs / **swings** × 100 |
| **CSW%** | csw / TotalPitches × 100 |
| **Contact%** | contacts / **swings** × 100 |
| **Zone%** | （is_zone の球数）/ TotalPitches × 100 |
| **K%** | K / TBF × 100 |
| **BB%** | BB / TBF × 100 |
| **HardHit%_against** | （被インプレーの is_hard_hit）/ インプレー数 × 100 |

### 球質・計測系
- **AvgVelo / MaxVelo** = RelSpeed の平均 / 最大
- **AvgSpinRate** = SpinRate 平均（整数丸め）
- **AvgIVB** = InducedVertBreak 平均、**AvgHB** = HorzBreak 平均
- **AvgEV_against** = 被インプレー打球 ExitSpeed 平均
- **Runs_against** = 対戦打席の RunsScored 合計

---

## 3. カウント分析（countAnalysis / countByPitch）

ボールカウント別に集計：
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

3. **OBPの分母は `PA − SAC`**（犠打を除く。一般的なMLB式の SF とは扱いが異なる独自定義）。

4. **AB の定義** = is_pa から BB・HBP・SAC を除いたもの。よって本来 `PA = AB + BB + HBP + SAC` が成立する。

   ⚠️ **All集計の33選手でこの等式が1だけ崩れる**（2026年単体では0件）。原因は
   **3ボールからの死球** で、TrackMan が `PitchCall='HitByPitch'` と同時に `KorBB='Walk'` も
   立てている行が全期間で **45球** ある（2026-08 調査。全て Balls=3）。
   `is_bb` と `is_hbp` が同じ行で両方 True になるため、
   - `BB` と `HBP` の**両方に1ずつ**計上される（→ BB がリーグ全体で45だけ過大）
   - **OBP の分子** `H+BB+HBP` と **wOBA の分子**（0.692 と 0.73 の両方）で**二重計上**される
   - `AB` は行単位フラグなので正しい（AVG/SLG には影響なし）

   実事象は死球（PitchCall が事象そのもの）。選手詳細の**結果球ビューは死球としてのみ**
   数えている（§5）ので、該当選手のOBPはリーダーボードより 0.004〜0.008 低く出る。
   リーダーボード側を直すと BB / OBP / OPS / wOBA / wRC+ / 投手の与四球・FIP が全体で動くため、
   **修正はユーザー判断待ち**（旧バージョンのJSON残骸が原因という以前の記述は誤りだった）。

5. **ストライクゾーン定義** = 高さ 1.5〜3.5 ft、横 ±0.83 ft。

6. **スイートスポット** = 打球角度 8〜32°。

7. **規定打席/規定打者数**: wRC+ などのリーグ基準は年度別 PA≧20・TBF≧30、All集計 PA≧2 など、集計単位で閾値が異なる。

---

## 5. 結果球（resultPitches — 選手詳細の「結果球の分析」「2ストライク後の成績」）

**結果球** = 打席を決着させた1球。`is_pa` が立つ球（インプレー・三振・四死球）そのもので、
1打席につき必ず1球。したがって `len(resultPitches)` = その選手の全年度 PA。

- 生成: `update_season.py` の `pa_result_pitches()` → `players/*_detail/*.json` の `resultPitches`
- 「2ストライク後」ビューはその **`strikes == 2` の部分集合**
  （ストライクは2で頭打ちになり以後維持されるため 終端球 strikes==2 ⟺ 2ストライク到達）
- `balls`/`strikes` は **その球を投じる前**のカウント（TrackMan の値そのまま）
- `ev`/`la` は打球になった球にだけ持たせる（「打ったコース」の打球質表示用）

フロント側の集計（ダッシュボードとは分母が異なるので注意）:
- **打数** = 安打 + 凡打 + 三振（四死球・犠打は除外。§1 の AB と同じ定義）
- **打率** = 安打 / 打数、**長打率** = 塁打 / 打数
- **出塁率** = (安打 + 四球 + 死球) / (打席 − 犠打)
- **三振率 / 四球率** = 三振 or 四球 / 打席
- **インプレー率** = (安打 + 凡打) / 打席、**初球決着率** = 0-0 で終わった打席 / 打席
- **投げたコース** = 結果球全球の散布（投手目線、色=結果カテゴリ・形=球種）
- **打ったコース** = 打球になった結果球のみを 5×5 ゾーン集計。内側3×3=ストライクゾーン9分割、
  外周1マス=ボールゾーン（描画レンジ外の球は外周へクランプして落とさない）。
  打数2未満のマスは打率を出さない（1打数の .000/1.000 を色にしないため）。
