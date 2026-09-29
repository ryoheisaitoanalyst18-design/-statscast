#!/usr/bin/env python3
"""validate_data.py — 公開データ (data/) の整合性検証ゲート。

標準ライブラリのみで動く (pandas等不要)。用途:
  1. デプロイ前ゲート: update_and_deploy.sh がデータ再生成後に実行し、失敗なら中断
  2. 夜間エージェント: リポジトリ単体で品質チェック (python3 scripts/validate_data.py)

チェック内容:
  - 全JSONがパース可能で必須キーを持つ
  - 打者/投手指標の不変条件 (AVG≤1, H≤AB, AB≤PA, K≤PA, TB≥H, %は0-100 等)
  - リーグ全体のwOBAが常識的範囲
  - リーダーボード上位選手の詳細ファイル実在 (姓名揺れ残骸の検出)
  - 結果球 (resultPitches) の構造・カウント値域・打席数整合
  - チーム傾向 (teams/) の構造
  - モデル (models/) の健全性: Stuff+平均≈100、xwOBA↔wOBAのリーグ整合、グリッド地形
  - index.html / 404.html の参照アセット実在 + バンドルハッシュ一致 + noindex 保持
  - 公開除外物 (batter_zones/, dates_2026.json) がコミットされていないこと

使い方: python3 scripts/validate_data.py [DATA_DIR] [--skip-html]
  DATA_DIR 省略時: スクリプト位置から ../data
  --skip-html: index.html/404.html の検査を省く (パイプライン出力側の検証時に使う。
               あちらのHTMLはデプロイ対象ではない残骸のため)
終了コード: 0=全PASS, 1=FAILあり
"""
import json
import math
import os
import re
import subprocess
import sys

PASS, FAIL, WARN = [], [], []


def ok(name):
    PASS.append(name)


def ng(name, detail=""):
    FAIL.append(f"{name}" + (f" — {detail}" if detail else ""))
    print(f"  ✗ FAIL {name}  {detail}")


def warn(name, detail=""):
    WARN.append(f"{name}" + (f" — {detail}" if detail else ""))
    print(f"  ⚠ WARN {name}  {detail}")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def unwrap(doc):
    """tRPCラップ [{result:{data:{json':...}}}] を剥がす。生JSONはそのまま。"""
    if isinstance(doc, list) and doc and isinstance(doc[0], dict) and "result" in doc[0]:
        return doc[0]["result"]["data"]["json"]
    return doc


VALID_TEAMS = {"東京大学", "立教大学", "慶應義塾大学", "法政大学", "明治大学", "早稲田大学"}


def in_range(v, lo, hi):
    return v is None or (isinstance(v, (int, float)) and lo <= v <= hi)


# ------------------------------------------------------------
def check_year_data(data_dir, year):
    name = f"yearData_{year}.json"
    path = os.path.join(data_dir, name)
    if not os.path.exists(path):
        ng(name, "ファイルなし")
        return None
    try:
        d = unwrap(load(path))
    except Exception as e:
        ng(name, f"パース不能: {e}")
        return None
    for key in ("batterLeaderboard", "pitcherLeaderboard", "teamBatting", "teamPitching"):
        if key not in d:
            ng(f"{name}:{key}", "キー欠落")
            return None

    bad = 0
    for r in d["batterLeaderboard"]:
        checks = [
            r.get("AB", 0) <= r.get("PA", 0),
            r.get("H", 0) <= r.get("AB", 0) + 1e-9,
            r.get("K", 0) <= r.get("PA", 0),
            r.get("TB", 0) >= r.get("H", 0),
            in_range(r.get("AVG"), 0, 1), in_range(r.get("OBP"), 0, 1),
            # wOBAの理論上限は全打席HRの2.065 (PA=1の小サンプルで正当に1超えする)
            in_range(r.get("SLG"), 0, 4), in_range(r.get("wOBA"), 0, 2.07),
            in_range(r.get("K_pct"), 0, 100), in_range(r.get("BB_pct"), 0, 100),
            in_range(r.get("HardHit_pct"), 0, 100), in_range(r.get("Barrel_pct"), 0, 100),
            # wRC+: leagueWOBA=0や計算失敗で無限大になるケースを検知 (全年度実測最大645)
            in_range(r.get("wRC_plus"), 0, 1000),
            in_range(r.get("SwSp_pct"), 0, 100), in_range(r.get("BABIP"), 0, 1),
            # PA = AB + BB + HBP + SAC (行単位フラグが排他である証明)。
            # 3ボールからの死球で is_bb と is_hbp が同時に立っていた頃は All集計の
            # 33選手で1だけ崩れ、OBP/wOBAの分子が二重計上されていた (2026-08 修正)。
            round(r.get("AB", 0) + r.get("BB", 0) + r.get("HBP", 0) + r.get("SAC", 0))
            == round(r.get("PA", 0)),
        ]
        if not all(checks):
            bad += 1
            if bad <= 2:
                ng(f"{name}: 打者不変条件", f"{r.get('Name')} {[i for i,c in enumerate(checks) if not c]}")
    if bad == 0:
        ok(f"{name}: 打者{len(d['batterLeaderboard'])}行の不変条件")

    bad = 0
    for r in d["pitcherLeaderboard"]:
        checks = [
            in_range(r.get("AVG_against"), 0, 1), in_range(r.get("Whiff_pct"), 0, 100),
            in_range(r.get("Strike_pct"), 0, 100), in_range(r.get("CSW_pct"), 0, 100),
            in_range(r.get("Zone_pct"), 0, 100), (r.get("IP", 0) or 0) >= 0,
            r.get("H_against", 0) <= r.get("AB_against", 0) + 1e-9,
            in_range(r.get("K_pct"), 0, 100), in_range(r.get("BB_pct"), 0, 100),
            # 回転効率は 0-100% (100超は物理的にあり得ない → パイプライン側で丸め/棄却済み)
            in_range(r.get("AvgSpinEff"), 0, 100),
        ]
        if not all(checks):
            bad += 1
            if bad <= 2:
                ng(f"{name}: 投手不変条件", f"{r.get('Name')}")
    if bad == 0:
        ok(f"{name}: 投手{len(d['pitcherLeaderboard'])}行の不変条件")

    # Zone% 回帰チェック: is_zone が feet 境界でメートル座標を判定していた頃は
    # 中央値 0.6% だった (2026-07-04 修正)。正常値は 40% 前後。
    zq = [r["Zone_pct"] for r in d["pitcherLeaderboard"]
          if r.get("TotalPitches", 0) >= 200 and r.get("Zone_pct") is not None]
    if len(zq) >= 5:
        zmed = sorted(zq)[len(zq) // 2]
        if 30 <= zmed <= 60:
            ok(f"{name}: Zone%中央値 {zmed:.1f}")
        else:
            ng(f"{name}: Zone%中央値異常", f"{zmed:.1f} (期待30-60)")

    # 回転効率の回帰チェック: 空気密度・Cl(S)・単位換算のいずれかが壊れると
    # 分布ごと平行移動する (全球100%張り付き / 一桁% など)。全球種混合の中央値は 60-70% 前後。
    sq = [r["AvgSpinEff"] for r in d["pitcherLeaderboard"]
          if r.get("TotalPitches", 0) >= 200 and r.get("AvgSpinEff") is not None]
    if len(sq) >= 5:
        smed = sorted(sq)[len(sq) // 2]
        if 40 <= smed <= 90:
            ok(f"{name}: 回転効率中央値 {smed:.1f}%")
        else:
            ng(f"{name}: 回転効率中央値異常", f"{smed:.1f}% (期待40-90)")

    # カウント別分析: 同じカウントが2行に割れていない ("1-2" と "1.0-2.0" に割れて 23-24行になっていた回帰)
    counts = [r.get("Count") for r in d.get("countAnalysis", [])]
    bad_keys = [c for c in counts if not re.fullmatch(r"[0-3]-[0-2]", str(c))]
    if len(counts) != len(set(counts)) or bad_keys or len(counts) > 12:
        ng(f"{name}: カウント別分析のキー異常", f"{len(counts)}行 重複/不正 {bad_keys[:3]}")
    elif counts:
        ok(f"{name}: カウント別分析 {len(counts)}カウント (重複なし)")

    # 投球回の回帰チェック: リーグ全体の アウト数/対戦打者数 は野球の構造上 0.66-0.74 付近。
    # PlayResult ベースで数えて犠打・併殺を落としていた頃は 0.645 だった (2026-09-29 修正)。
    tp = d.get("teamPitching", [])
    outs = sum((r.get("Outs") if r.get("Outs") is not None else r.get("IP", 0) * 3) for r in tp)
    tbf = sum(r.get("TBF", 0) for r in tp)
    if tbf >= 300:
        ratio = outs / tbf
        if 0.66 <= ratio <= 0.74:
            ok(f"{name}: アウト数/対戦打者 {ratio:.3f} (投球回の整合)")
        else:
            ng(f"{name}: アウト数/対戦打者が異常", f"{ratio:.3f} (期待 0.66-0.74) — 投球回の数え方を確認")
    # チームの 打撃の打席合計 = 投手の対戦打者合計 (同じ打席を両側から数えている)
    pa_bat = sum(r.get("PA", 0) for r in d["teamBatting"])
    if tbf and pa_bat and abs(pa_bat - tbf) > max(5, 0.01 * tbf):
        ng(f"{name}: 打席合計の不一致", f"打撃 {pa_bat} vs 投手 {tbf}")

    teams = {t.get("Team") for t in d["teamBatting"]}
    if teams <= VALID_TEAMS and len(teams) >= 2:
        ok(f"{name}: チーム構成")
    else:
        ng(f"{name}: チーム構成", str(teams - VALID_TEAMS))

    # wRC+ の基準 = 打席数加重のリーグ wOBA なので、全打者の打席加重平均はほぼ 100 になる
    # (旧実装は「規定打席以上の選手の単純平均」を基準にしており、加重平均が 100 から数%ずれていた)
    wr = [(r["wRC_plus"], r.get("PA", 0) - r.get("SH", 0)) for r in d["batterLeaderboard"]
          if r.get("wRC_plus") is not None and r.get("PA", 0) > 0]
    wsum = sum(w for _, w in wr)
    if wsum >= 300:
        avg_wrc = sum(v * w for v, w in wr) / wsum
        if 97 <= avg_wrc <= 103:
            ok(f"{name}: wRC+ の打席加重平均 {avg_wrc:.1f} (≈100)")
        else:
            ng(f"{name}: wRC+ の打席加重平均が100から外れている", f"{avg_wrc:.1f} — リーグwOBAの定義を確認")

    qual = [r["wOBA"] for r in d["batterLeaderboard"] if r.get("PA", 0) >= 20 and r.get("wOBA") is not None]
    if qual:
        lg = sum(qual) / len(qual)
        if 0.20 <= lg <= 0.48:
            ok(f"{name}: リーグwOBA {lg:.3f}")
        else:
            ng(f"{name}: リーグwOBA異常", f"{lg:.3f}")
    return d


def check_player_details(data_dir, year_doc):
    """最新年度の上位打者・投手に詳細ファイルがあるか (姓名揺れ・残骸の検出)。"""
    rows = sorted(year_doc["batterLeaderboard"], key=lambda r: -r.get("PA", 0))[:30]
    missing = []
    for r in rows:
        fn = r["Name"].replace("/", "_").replace("\\", "_") + ".json"
        if not os.path.exists(os.path.join(data_dir, "players", "batter_detail", fn)):
            missing.append(r["Name"])
    if missing:
        ng("打者詳細ファイル欠落", f"{len(missing)}人 例: {missing[:3]}")
    else:
        ok(f"打者詳細ファイル: 上位{len(rows)}人分すべて実在")

    rows_p = sorted(year_doc["pitcherLeaderboard"], key=lambda r: -r.get("TotalPitches", 0))[:30]
    missing_p = []
    for r in rows_p:
        fn = r["Name"].replace("/", "_").replace("\\", "_") + ".json"
        if not os.path.exists(os.path.join(data_dir, "players", "pitcher_detail", fn)):
            missing_p.append(r["Name"])
    if missing_p:
        ng("投手詳細ファイル欠落", f"{len(missing_p)}人 例: {missing_p[:3]}")
    else:
        ok(f"投手詳細ファイル: 上位{len(rows_p)}人分すべて実在")


RESULT_CATS = {"1B", "2B", "3B", "HR", "out", "K_swing", "K_look", "BB", "HBP", "sac", "sf"}
RESULT_CONTACT_CATS = {"1B", "2B", "3B", "HR", "out", "sac", "sf"}


def check_result_pitches(data_dir, year_doc):
    """選手詳細の resultPitches (結果球 = 打席を決着させた1球) の構造と整合。

    フロントの「結果球の分析」「2ストライク後の成績」がこの1配列に乗っているので、
    ここが静かに壊れると両方のビューが同時に空になる。
      - 結果球の数 ≧ 当年のPA/TBF (詳細は全年度分、リーダーボードは当年のみ)
      - 結果カテゴリ・コースが定義域内
      - strikes==2 の部分集合が存在する (2ストライク後ビューの回帰検知)
      - ev (打球速度) は打球になった球にだけ付く

    カウント (balls 0-3 / strikes 0-2) の域外は**元CSVに実在する**ため WARN 止まり
    (2026-08 時点で全12万球中14球: 3-3 や 0--1。TrackMan 側の記録ミスで、
     多くは三振行に投球後のカウントが入っている)。率が跳ねたら定義破壊なので FAIL。
    """
    targets = []
    for r in sorted(year_doc.get("batterLeaderboard", []), key=lambda r: -r.get("PA", 0))[:10]:
        fn = r["Name"].replace("/", "_").replace("\\", "_") + ".json"
        targets.append(("打者", r["Name"], os.path.join(data_dir, "players", "batter_detail", fn), r.get("PA")))
    for r in sorted(year_doc.get("pitcherLeaderboard", []), key=lambda r: -r.get("TotalPitches", 0))[:10]:
        fn = r["Name"].replace("/", "_").replace("\\", "_") + ".json"
        targets.append(("投手", r["Name"], os.path.join(data_dir, "players", "pitcher_detail", fn), r.get("TBF")))

    checked = two_strike_total = total_pitches = 0
    problems, odd_counts = [], []
    for kind, name, path, min_pa in targets:
        if not os.path.exists(path):
            continue  # ファイル実在は check_player_details の担当
        try:
            d = load(path)
        except Exception as e:
            problems.append(f"{kind}{name}: パース不能 ({e})")
            continue
        rp = d.get("resultPitches")
        if not isinstance(rp, list) or not rp:
            problems.append(f"{kind}{name}: resultPitches が無い/空")
            continue
        checked += 1
        total_pitches += len(rp)
        two_strike_total += sum(1 for p in rp if p.get("strikes") == 2)
        if min_pa and len(rp) < min_pa:
            problems.append(f"{kind}{name}: 結果球{len(rp)} < 当年の打席{min_pa}")
        for p in rp:
            if p.get("strikes") not in (0, 1, 2, None) or p.get("balls") not in (0, 1, 2, 3, None):
                odd_counts.append(f"{kind}{name} {p.get('balls')}-{p.get('strikes')}")
            if p.get("cat") not in RESULT_CATS:
                problems.append(f"{kind}{name}: 未知の結果カテゴリ {p.get('cat')!r}"); break
            if not in_range(p.get("px"), -3, 3) or not in_range(p.get("pz"), -1, 5):
                problems.append(f"{kind}{name}: コース値域外 {p.get('px')}/{p.get('pz')}"); break
            if p.get("ev") is not None and p.get("cat") not in RESULT_CONTACT_CATS:
                problems.append(f"{kind}{name}: 打球でないのに ev がある ({p.get('cat')})"); break

    odd_rate = (len(odd_counts) / total_pitches * 100) if total_pitches else 0.0
    if checked == 0:
        ng("結果球 (resultPitches)", "上位選手の詳細に1件も無い — パイプライン未再生成?")
        return
    if problems:
        ng(f"結果球 (resultPitches): {len(problems)}件の不整合", " / ".join(problems[:3]))
        return
    if two_strike_total == 0:
        ng("結果球 (resultPitches)", "strikes==2 が1球も無い — 2ストライク後ビューが空になる")
        return
    if odd_rate > 1.0:
        ng(f"結果球: カウント域外が {odd_rate:.1f}% ({len(odd_counts)}球)",
           "元CSVの散発ミスでは説明できない量 — カウントの取り方が壊れていないか確認"
           f" 例: {', '.join(odd_counts[:3])}")
        return
    if odd_counts:
        warn(f"結果球: カウント域外 {len(odd_counts)}球 ({odd_rate:.2f}%)",
             f"元CSVに実在する記録ミス (既知)。例: {', '.join(odd_counts[:3])}")
    ok(f"結果球 (resultPitches): 上位{checked}人 {total_pitches}球の構造・打席数整合 "
       f"(うち2ストライク {two_strike_total}球)")


def check_tendencies(data_dir, scope):
    path = os.path.join(data_dir, "teams", f"tendencies_{scope}.json")
    if not os.path.exists(path):
        warn(f"tendencies_{scope}.json", "なし")
        return
    try:
        d = load(path)
    except Exception as e:
        ng(f"tendencies_{scope}", f"パース不能: {e}")
        return
    teams = d.get("teams", [])
    if set(teams) <= VALID_TEAMS and len(teams) >= 2:
        ok(f"tendencies_{scope}: チーム構成")
    else:
        ng(f"tendencies_{scope}: チーム構成")
    if not teams:
        return
    try:
        t0 = teams[0]
        z = d["data"][t0]["batting"]["zones"]
        if all(k in z for k in ("all", "byPitch", "R", "L")):
            ok(f"tendencies_{scope}: ゾーン構造 (all/byPitch/R/L)")
        else:
            ng(f"tendencies_{scope}: ゾーン構造", str(list(z.keys())))
    except (KeyError, IndexError) as e:
        ng(f"tendencies_{scope}: ゾーン構造 (構造アクセス失敗)", str(e))


def check_stuffplus_scope(data_dir, scope):
    """スコープ別 Stuff+ ファイルの存在・パース・値域チェック (加重平均≈100は全体のみ)。"""
    path = os.path.join(data_dir, "models", f"stuffplus_{scope}.json")
    if not os.path.exists(path):
        warn(f"stuffplus_{scope}.json", "なし")
        return
    try:
        d = load(path)
    except Exception as e:
        ng(f"stuffplus_{scope}.json", f"パース不能: {e}")
        return
    rows = d.get("rows", [])
    if not rows:
        warn(f"stuffplus_{scope}.json", "rows が空")
        return
    out_of_range = [r for r in rows if not (40 <= r.get("stuff", -1) <= 180)]
    if out_of_range:
        ng(f"stuffplus_{scope}.json: Stuff+ 値域外", f"{len(out_of_range)}行")
    else:
        ok(f"stuffplus_{scope}.json: 値域 [40,180] ({len(rows)}行)")


def check_xwoba_scope(data_dir, scope):
    """スコープ別 xwOBA ファイルの存在・パース・diff 整合チェック。"""
    path = os.path.join(data_dir, "models", f"xwoba_{scope}.json")
    if not os.path.exists(path):
        warn(f"xwoba_{scope}.json", "なし")
        return
    try:
        d = load(path)
    except Exception as e:
        ng(f"xwoba_{scope}.json", f"パース不能: {e}")
        return
    bats = d.get("batters", [])
    if not bats:
        warn(f"xwoba_{scope}.json", "batters が空")
        return
    bad = [r for r in bats
           if abs((r.get("wOBA", 0) - r.get("xwOBA", 0)) - r.get("diff", 0)) > 0.0015]
    if bad:
        ng(f"xwoba_{scope}.json: diff列が wOBA-xwOBA と不一致", f"{len(bad)}行")
    else:
        ok(f"xwoba_{scope}.json: diff 整合 ({len(bats)}打者)")


def check_models_meta(data_dir):
    """models_meta.json の存在・構造・モデル品質指標を検証する。"""
    path = os.path.join(data_dir, "models", "models_meta.json")
    if not os.path.exists(path):
        ng("models_meta.json", "なし")
        return
    try:
        d = load(path)
    except Exception as e:
        ng("models_meta.json", f"パース不能: {e}")
        return
    for key in ("unitNormalization", "stuffPlus", "xwoba"):
        if key not in d:
            ng("models_meta.json", f"キー欠落: {key}")
            return
    ok("models_meta.json: 必須キー確認 (unitNormalization/stuffPlus/xwoba)")

    # xwOBAモデルの対数損失がベースライン（定数予測）を下回ることを確認
    ll = d["xwoba"].get("logloss", {})
    if isinstance(ll, dict) and "model" in ll and "baseline" in ll:
        if ll["model"] < ll["baseline"]:
            ok(f"xwOBA logloss: モデル({ll['model']:.3f}) < ベースライン({ll['baseline']:.3f})")
        else:
            ng("xwOBA logloss: モデルがベースライン以上", f"{ll['model']:.3f} >= {ll['baseline']:.3f}")

    # xwOBAのホールドアウト相関が正であることを確認
    hbc = d["xwoba"].get("holdoutBatterCorr", {})
    if isinstance(hbc, dict) and "corr" in hbc:
        if hbc["corr"] > 0:
            ok(f"xwOBA ホールドアウト打者相関 {hbc['corr']:.3f} > 0")
        else:
            ng("xwOBA ホールドアウト打者相関が0以下", f"{hbc['corr']:.3f}")

    # Stuff+ スプリットハーフ信頼性係数が妥当範囲
    sp_val = d["stuffPlus"].get("validation", {})
    shr = sp_val.get("splitHalfReliability")
    if shr is not None:
        if 0 < shr <= 1:
            ok(f"Stuff+ スプリットハーフ信頼性 {shr:.3f} ∈ (0,1]")
        else:
            ng("Stuff+ スプリットハーフ信頼性が範囲外", f"{shr}")


def check_models(data_dir):
    mdir = os.path.join(data_dir, "models")
    check_models_meta(data_dir)
    # Stuff+
    path = os.path.join(mdir, "stuffplus_All.json")
    if os.path.exists(path):
        d = load(path)
        rows = d.get("rows", [])
        if rows:
            wsum = sum(r["stuff"] * r["n"] for r in rows)
            nsum = sum(r["n"] for r in rows)
            mean = wsum / nsum
            if 95 <= mean <= 105:
                ok(f"Stuff+ 加重平均 {mean:.1f} ≈ 100")
            else:
                ng("Stuff+ 加重平均が100から乖離", f"{mean:.1f}")
            if all(40 <= r["stuff"] <= 180 for r in rows):
                ok("Stuff+ 値域 [40,180]")
            else:
                ng("Stuff+ 値域外の行あり")
    else:
        warn("stuffplus_All.json", "なし")
    # xwOBA
    path = os.path.join(mdir, "xwoba_All.json")
    if os.path.exists(path):
        d = load(path)
        bats = [r for r in d.get("batters", []) if r.get("PA", 0) >= 30]
        if bats:
            pa = sum(r["PA"] for r in bats)
            lg_w = sum(r["wOBA"] * r["PA"] for r in bats) / pa
            lg_x = sum(r["xwOBA"] * r["PA"] for r in bats) / pa
            if abs(lg_w - lg_x) < 0.025:
                ok(f"xwOBA↔wOBA リーグ整合 (Δ{lg_w - lg_x:+.3f})")
            else:
                ng("xwOBA↔wOBA リーグ乖離", f"w{lg_w:.3f} x{lg_x:.3f}")
            bad = [r for r in bats if abs((r["wOBA"] - r["xwOBA"]) - r["diff"]) > 0.0015]
            if not bad:
                ok("xwOBA diff列の再計算整合")
            else:
                ng("diff列が wOBA-xwOBA と不一致", f"{len(bad)}行")
    else:
        warn("xwoba_All.json", "なし")
    # grid
    path = os.path.join(mdir, "xwoba_grid.json")
    if os.path.exists(path):
        g = load(path)
        vals = [v for row in g["values"] for v in row]
        if all(0 <= v <= 3 for v in vals):
            ok("xwOBAグリッド値域 [0,3]")
        else:
            ng("xwOBAグリッド値域外")
        ev, la = g["evAxis"], g["laAxis"]

        def band_mean(ev_lo, ev_hi, la_lo, la_hi):
            s, n = 0.0, 0
            for li, l in enumerate(la):
                if la_lo <= l <= la_hi:
                    for ei, e in enumerate(ev):
                        if ev_lo <= e <= ev_hi:
                            s += g["values"][li][ei]
                            n += 1
            return s / max(n, 1)

        hi = band_mean(155, 190, 8, 30)
        lo = band_mean(60, 100, 8, 30)
        if hi > lo + 0.15:
            ok(f"グリッド地形 (強打帯{hi:.2f} > 弱打帯{lo:.2f})")
        else:
            ng("グリッド地形が平坦", f"強打帯{hi:.2f} vs 弱打帯{lo:.2f}")
    # 2026年大会別スコープのモデル健全性 (tendencies と対称にチェック)
    for scope in competition_scopes_2026(data_dir):
        check_stuffplus_scope(data_dir, scope)
        check_xwoba_scope(data_dir, scope)


def check_run_expectancy(data_dir):
    """カウント×アウト数のRun Expectancy行列 (36状態) の構造健全性を確認する。

    ボール 0-3 × ストライク 0-2 × アウト 0-2 の標準36状態すべてが存在するかを確認し、
    想定外のキー（負値を含む外れ値行 等）を WARN で報告する。
    """
    path = os.path.join(data_dir, "run_expectancy.json")
    if not os.path.exists(path):
        warn("run_expectancy.json", "なし")
        return
    try:
        d = load(path)
    except Exception as e:
        ng("run_expectancy.json", f"パース不能: {e}")
        return

    states = d.get("states", {})
    if not states:
        ng("run_expectancy.json", "states キーなし")
        return

    expected = {f"{b}-{s}-{o}" for b in range(4) for s in range(3) for o in range(3)}
    missing = expected - set(states.keys())
    if missing:
        ng("run_expectancy.json: 標準36状態に欠落", f"{len(missing)}件 例: {sorted(missing)[:3]}")
    else:
        ok("run_expectancy.json: 標準36カウント×アウト状態すべて実在")

    bad_re = [k for k, v in states.items() if not (0 <= v.get("re", 0) <= 3.0)]
    if bad_re:
        ng("run_expectancy.json: RE値域外 [0,3]", str(bad_re[:3]))
    else:
        ok("run_expectancy.json: RE値域 [0, 3]")

    anomalous = sorted(k for k in states.keys() if k not in expected)
    if anomalous:
        ng("run_expectancy.json: 想定外の状態キー", f"{len(anomalous)}件: {anomalous[:5]}")

    # 鮮度: パイプラインが毎回作る (2026-09-29〜)。全年度の試合数と一致しなければ古いファイル
    # (旧 build_run_values.py の出力が 2026-05 から更新されずに残っていた)
    rk = d.get("pitcherRankings", [])
    names = [r.get("pitcher") for r in rk]
    if not rk:
        ng("run_expectancy.json: pitcherRankings が空")
    elif len(names) != len(set(names)):
        ng("run_expectancy.json: 投手の重複", str(len(names) - len(set(names))))
    games = (d.get("metadata") or {}).get("totalGames")
    try:
        meta = load(os.path.join(data_dir, "models", "models_meta.json"))
        un = meta.get("unitNormalization", {})
        total_games = un.get("imperial_games", 0) + un.get("metric_games", 0)
    except Exception:
        total_games = None
    if games and total_games and games != total_games:
        ng("run_expectancy.json が古い", f"{games}試合 (現データ {total_games}試合) — パイプラインで再生成されていない")
    elif games:
        ok(f"run_expectancy.json: {games}試合・投手{len(rk)}人 (現データと一致)")


def check_asset_chunks(repo_root, html_refs):
    """main JS バンドルから動的 import される遅延チャンクが assets/ に実在するか (落とし穴① ⑦)。

    check_html() は HTML 直参照ファイルの実在を保証するが、Vite コード分割の
    遅延チャンク (3D コンポーネント等) は HTML に現れないため別途検査が必要。
    欠落すると 3D 表示だけが壊れ、トップページは正常に見える事故になる。
    """
    main_js_set = {r for r in html_refs.get("index.html", set()) if r.endswith(".js")}
    if not main_js_set:
        warn("遅延チャンク確認", "index.html に main JS の参照がない")
        return
    main_js_rel = next(iter(main_js_set))  # "assets/index-XXXX.js"
    main_js_path = os.path.join(repo_root, main_js_rel)
    if not os.path.exists(main_js_path):
        warn("遅延チャンク確認", f"{main_js_rel} が実在しない")
        return

    with open(main_js_path, encoding="utf-8", errors="ignore") as f:
        content = f.read()
    # Vite のチャンク命名: ComponentName-HASH8chars.js (英字始まり・8文字以上のハッシュ)
    chunk_names = set(re.findall(r'[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9_]{8,}\.js', content))
    if not chunk_names:
        warn("遅延チャンク確認", "チャンク参照が main JS に見つからなかった")
        return

    assets_dir = os.path.join(repo_root, "assets")
    missing = sorted(c for c in chunk_names if not os.path.exists(os.path.join(assets_dir, c)))
    if missing:
        ng("遅延チャンク欠落 (3D機能が壊れる)", str(missing))
    else:
        ok(f"遅延チャンク {len(chunk_names)} 件すべて実在")


def check_html(repo_root):
    html_refs = {}
    for fn in ("index.html", "404.html"):
        path = os.path.join(repo_root, fn)
        if not os.path.exists(path):
            warn(fn, "なし (repoルート外で実行?)")
            continue
        html = open(path, encoding="utf-8").read()
        refs = set(re.findall(r"assets/[A-Za-z0-9_.-]+\.(?:js|css)", html))
        missing = [r for r in refs if not os.path.exists(os.path.join(repo_root, r))]
        if missing:
            ng(f"{fn}: 参照アセット欠落", str(missing))
        else:
            ok(f"{fn}: 参照アセット{len(refs)}件すべて実在")
        if "noindex" in html:
            ok(f"{fn}: noindex 保持")
        else:
            ng(f"{fn}: noindex が消えている (意図的設定 — 復元必要)")
        html_refs[fn] = refs

    # index.html と 404.html は必ず同じバンドルを参照しなければならない
    # (404.html は SPA ディープリンクのフォールバックで同じバンドルを読む)
    if "index.html" in html_refs and "404.html" in html_refs:
        if html_refs["index.html"] == html_refs["404.html"]:
            ok("index.html / 404.html バンドルハッシュ一致")
        else:
            only_index = html_refs["index.html"] - html_refs["404.html"]
            only_404 = html_refs["404.html"] - html_refs["index.html"]
            ng(
                "index.html / 404.html バンドルハッシュ不一致",
                f"index のみ: {sorted(only_index)}  404 のみ: {sorted(only_404)}",
            )
    check_asset_chunks(repo_root, html_refs)


def competition_scopes_2026(data_dir):
    """competitions_2026.json から 2026 の大会スコープ名 (2026_autumn 等) を読む。
    大会区分はシーズン進行で増えるので、検証側もハードコードせず実データに従う。"""
    path = os.path.join(data_dir, "competitions_2026.json")
    if not os.path.exists(path):
        return []
    try:
        inner = unwrap(load(path))
    except Exception:
        return []
    order = inner.get("order")
    if not order:
        order = [k for k, v in inner.items() if isinstance(v, dict) and "dates" in v]
    return [f"2026_{k}" for k in order]


def check_competitions(data_dir):
    """competitions_YYYY.json の存在・tRPCラップ構造・大会区分キーを検証する。"""
    import glob as _glob
    files = sorted(_glob.glob(os.path.join(data_dir, "competitions_*.json")))
    if not files:
        warn("competitions_*.json", "なし")
        return
    for path in files:
        name = os.path.basename(path)
        try:
            d = load(path)
        except Exception as e:
            ng(name, f"パース不能: {e}")
            continue
        try:
            inner = unwrap(d)
        except Exception as e:
            ng(name, f"tRPCアンラップ失敗: {e}")
            continue
        missing = [k for k in ("cutoff", "league", "fresh") if k not in inner]
        if missing:
            ng(name, f"キー欠落: {missing}")
            continue
        league_dates = inner["league"].get("dates", [])
        fresh_dates = inner["fresh"].get("dates", [])
        if not league_dates or not fresh_dates:
            ng(name, f"dates が空 (league={len(league_dates)}, fresh={len(fresh_dates)})")
            continue
        ok(f"{name}: 構造確認 (cutoff={inner['cutoff']}, league={len(league_dates)}日, fresh={len(fresh_dates)}日)")


def check_orphaned_assets(repo_root):
    """assets/ の到達閉包チェック: どこからも import されない孤立ファイルを WARN。

    index.html / 404.html から参照される JS/CSS を起点に、JS ファイル内の
    動的 import 参照を再帰的に辿り、到達できないファイルを検出する
    (OPERATIONS.md ⑦「到達 closure 方式」参照)。
    """
    assets_dir = os.path.join(repo_root, "assets")
    if not os.path.isdir(assets_dir):
        return

    reachable = set()
    for html_fn in ("index.html", "404.html"):
        html_path = os.path.join(repo_root, html_fn)
        if not os.path.exists(html_path):
            continue
        html = open(html_path, encoding="utf-8").read()
        for ref in re.findall(r'assets/([A-Za-z0-9_.-]+\.(?:js|css))', html):
            reachable.add(ref)

    to_visit = [f for f in list(reachable) if f.endswith(".js")]
    visited = set(to_visit)
    while to_visit:
        fn = to_visit.pop()
        js_path = os.path.join(assets_dir, fn)
        if not os.path.exists(js_path):
            continue
        content = open(js_path, encoding="utf-8").read()
        for ref in re.findall(r'"assets/([A-Za-z0-9_.-]+\.js)"', content):
            reachable.add(ref)
            if ref not in visited:
                visited.add(ref)
                to_visit.append(ref)

    all_assets = set(os.listdir(assets_dir))
    orphans = sorted(all_assets - reachable)
    if orphans:
        warn("孤立アセット候補 (OPERATIONS.md ⑦)", f"{len(orphans)}件: {orphans}")
    else:
        ok(f"assets/ 到達閉包: 全{len(all_assets)}ファイル到達可能")


def check_competitions_date_files(data_dir):
    """competitions_2026.json の日付リストと yearData 日別ファイルの双方向整合チェック。

    - competitions_2026.json に列挙された各日付に対応する yearData_2026_YYYY-MM-DD.json が存在するか
    - 逆に、data/ にある yearData_2026_YYYY-MM-DD.json が competitions_2026.json に未記載でないか
    """
    path = os.path.join(data_dir, "competitions_2026.json")
    if not os.path.exists(path):
        warn("competitions_2026.json", "なし (2026シーズンデータ未反映?)")
        return
    try:
        d = unwrap(load(path))
    except Exception as e:
        ng("competitions_2026.json", f"パース不能: {e}")
        return

    groups = {k: v for k, v in d.items() if isinstance(v, dict) and "dates" in v}
    if not groups:
        ng("competitions_2026.json", "大会グループが空")
        return

    listed_dates = []
    for key, grp in groups.items():
        if "label" not in grp or "dates" not in grp:
            ng("competitions_2026.json", f"グループ '{key}' に label/dates がない")
            return
        listed_dates.extend(grp["dates"])
    listed_dates = sorted(set(listed_dates))

    missing_files = [
        dt for dt in listed_dates
        if not os.path.exists(os.path.join(data_dir, f"yearData_2026_{dt}.json"))
    ]
    if missing_files:
        ng("competitions_2026.json: 対応 yearData ファイル欠落",
           f"{len(missing_files)}件 例: {missing_files[:3]}")
    else:
        ok(f"competitions_2026.json: {len(groups)}大会/{len(listed_dates)}日分の yearData すべて実在")

    actual_dates = {
        f.replace("yearData_2026_", "").replace(".json", "")
        for f in os.listdir(data_dir)
        if f.startswith("yearData_2026_2026") and f.endswith(".json")
    }
    unlisted = sorted(actual_dates - set(listed_dates))
    if unlisted:
        ng("competitions_2026.json 未記載の yearData 日別ファイルあり",
           f"{len(unlisted)}件 例: {unlisted[:3]}")
    else:
        ok("competitions_2026.json: 日別ファイルと日付リストが完全一致")


def check_defense_data(data_dir):
    """defenseData.json (守備位置・打球データ、手動系) の基本整合性チェック。"""
    path = os.path.join(data_dir, "defenseData.json")
    if not os.path.exists(path):
        warn("defenseData.json", "なし")
        return
    try:
        d = load(path)
    except Exception as e:
        ng("defenseData.json", f"パース不能: {e}")
        return
    if not isinstance(d, list) or len(d) == 0:
        ng("defenseData.json", f"空またはリスト形式でない (type={type(d).__name__})")
        return
    ok(f"defenseData.json: {len(d)}件パース・リスト形式 OK")
    required = {"pitchUID", "year", "pitcher", "batted", "fielders"}
    bad = [f"#{i}" for i, r in enumerate(d[:50]) if required - set(r.keys())]
    if not bad:
        ok("defenseData.json: 必須キー (pitchUID/year/pitcher/batted/fielders) 実在")
    else:
        ng("defenseData.json: 必須キー欠落", ", ".join(bad[:3]))
    # 同じ打球が2回入っていないこと (旧ファイルは 11,377件中 4,067件が重複していた)
    uids = [r.get("pitchUID") for r in d]
    dup = len(uids) - len(set(uids))
    if dup:
        ng("defenseData.json: 同じ打球 (pitchUID) の重複", f"{dup}件")
    else:
        ok(f"defenseData.json: pitchUID 重複なし ({len(uids)}打球)")


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def check_video_links(data_dir):
    """videos/links.json (球↔試合動画の紐づけ) の構造と参照整合。

    このファイルは任意機能なので、無いこと自体は正常 (パイプライン出力側には常に無い)。
    肝は「鍵が公開データに当たり続けているか」。パイプラインの単位換算などが変わると
    値そのものを鍵にしている都合で無言で全滅するため、実データ照合まで行う。
    """
    path = os.path.join(data_dir, "videos", "links.json")
    if not os.path.exists(path):
        ok("動画紐づけ: 未配置 (任意機能)")
        return
    try:
        d = load(path)
    except Exception as e:
        ng("videos/links.json", f"パース不能: {e}")
        return

    games = d.get("games")
    pitches = d.get("pitches")
    batted = d.get("batted")
    if not isinstance(games, list) or not games or not isinstance(pitches, dict):
        ng("videos/links.json", "games(非空リスト)/pitches(辞書) の形式でない")
        return
    if not isinstance(batted, dict):
        ng("videos/links.json", "batted が辞書でない")
        return

    for i, g in enumerate(games):
        missing = {"uid", "date", "away", "home", "youtubeId", "duration"} - set(g)
        if missing:
            ng(f"videos/links.json games[{i}] 必須キー欠落", ", ".join(sorted(missing)))
            return
        if "PLACEHOLDER" in str(g["youtubeId"]):
            ng("videos/links.json: PLACEHOLDER の動画IDが残っている",
               f"{g['uid']} — export_video_links.py を --placeholder 無しで再実行")
            return
        if not YOUTUBE_ID_RE.match(str(g["youtubeId"])):
            ng("videos/links.json: YouTube動画IDの形式が不正",
               f"{g['uid']} → {g['youtubeId']!r} (英数と-_の11文字)")
            return
    ok(f"videos/links.json: 試合 {len(games)}件・動画ID形式 OK")

    for label, table in (("pitches", pitches), ("batted", batted)):
        for k, v in table.items():
            if (not isinstance(v, list) or len(v) != 2
                    or not isinstance(v[0], int) or not isinstance(v[1], (int, float))):
                ng(f"videos/links.json {label} の値形式", f"{k} → {v!r}")
                return
            gi, t = v
            if not 0 <= gi < len(games):
                ng(f"videos/links.json {label} の試合index範囲外", f"{k} → {gi}")
                return
            if not 0 <= t <= games[gi]["duration"]:
                ng(f"videos/links.json {label} の再生位置が動画尺の外",
                   f"{k} → {t}s (尺 {games[gi]['duration']}s)")
                return
    ok(f"videos/links.json: 球 {len(pitches)}件・打球 {len(batted)}件の参照整合 OK")

    # 実データ照合: 鍵から投手名を取り出し、その投手詳細で引けるか確かめる
    detail_dir = os.path.join(data_dir, "players", "pitcher_detail")
    if not os.path.isdir(detail_dir):
        return
    by_pitcher = {}
    for k in pitches:
        parts = k.split("|")
        if len(parts) == 4:
            by_pitcher.setdefault(parts[1], set()).add(k)
    reached = total = 0
    for pitcher, keys in by_pitcher.items():
        total += len(keys)
        f = os.path.join(detail_dir, f"{pitcher}.json")
        if not os.path.exists(f):
            continue
        try:
            doc = load(f)
        except Exception:
            continue
        for p in doc.get("pitches", []):
            if p.get("px") is None or p.get("pz") is None:
                continue
            k = (f"{p.get('gameDate')}|{pitcher}"
                 f"|{math.floor(p['px'] * 1000 + 0.5)}|{math.floor(p['pz'] * 1000 + 0.5)}")
            if k in keys:
                reached += 1
    rate = reached / total if total else 0
    if rate >= 0.9:
        ok(f"videos/links.json: 球キーの {reached}/{total} ({rate:.1%}) が投手詳細から到達可能")
    else:
        ng("videos/links.json: 球キーが公開データに当たらない",
           f"{reached}/{total} ({rate:.1%})。パイプラインの値が変わった可能性 "
           f"— export_video_links.py を再実行して再デプロイ")


EXCLUDED_FROM_REPO = ["data/players/batter_zones", "data/dates_2026.json"]


def check_repo_exclusions(repo_root):
    """フロント未使用のパイプライン出力が公開リポジトリに紛れ込んでいないか。

    再生成やrsyncでローカルに復活するのは正常 (gitignoreがコミットだけを防ぐ)。
    """
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        warn("公開除外チェック", "gitリポジトリ外のためスキップ")
        return
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "ls-files", "--"] + EXCLUDED_FROM_REPO,
            capture_output=True, text=True, check=True,
        ).stdout
    except Exception as e:
        warn("公開除外チェック", f"git実行不可: {e}")
        return
    tracked = [l for l in out.splitlines() if l.strip()]
    if tracked:
        ng("公開除外物がコミットされている (git rm --cached で除去)", ", ".join(tracked))
    else:
        ok("公開除外物 (batter_zones/, dates_2026.json) は未コミット")
    for rel in EXCLUDED_FROM_REPO:
        if os.path.exists(os.path.join(repo_root, rel)):
            ignored = subprocess.run(
                ["git", "-C", repo_root, "check-ignore", "-q", rel],
                capture_output=True,
            ).returncode == 0
            if not ignored:
                ng(f"{rel} がローカルに存在するのに .gitignore されていない (git add -A で混入する)")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skip_html = "--skip-html" in sys.argv
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_data = os.path.normpath(os.path.join(script_dir, "..", "data"))
    data_dir = args[0] if args else default_data
    repo_root = os.path.normpath(os.path.join(data_dir, ".."))
    print(f"データ検証: {data_dir}\n" + "=" * 60)

    try:
        years = unwrap(load(os.path.join(data_dir, "years.json")))
        ok(f"years.json ({years})")
    except Exception as e:
        ng("years.json", str(e))
        years = ["All"]

    latest_doc = None
    for y in years:
        doc = check_year_data(data_dir, y)
        if doc is not None and y != "All":
            latest_doc = doc
    if latest_doc:
        check_player_details(data_dir, latest_doc)
        check_result_pitches(data_dir, latest_doc)

    # 2026年大会別ファイル (yearData_2026_{spring,fresh,autumn,league}.json) — CLAUDE.md に記載
    for scope in competition_scopes_2026(data_dir):
        check_year_data(data_dir, scope)

    numeric_years = [y for y in years if y != "All"]
    check_tendencies(data_dir, "All")
    if numeric_years:
        check_tendencies(data_dir, numeric_years[-1])
    # 2026年大会別の傾向ファイル (tendencies_2026_*.json)
    for scope in competition_scopes_2026(data_dir):
        check_tendencies(data_dir, scope)
    check_models(data_dir)
    check_competitions(data_dir)
    check_competitions_date_files(data_dir)
    check_run_expectancy(data_dir)
    check_defense_data(data_dir)
    check_video_links(data_dir)
    if not skip_html:
        check_html(repo_root)
        check_orphaned_assets(repo_root)
        check_repo_exclusions(repo_root)

    print("=" * 60)
    print(f"結果: {len(PASS)} passed / {len(FAIL)} FAILED / {len(WARN)} warned")
    if FAIL:
        print("\n失敗一覧:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print("✅ 全チェック通過")


if __name__ == "__main__":
    main()
