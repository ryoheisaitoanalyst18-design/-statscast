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

    teams = {t.get("Team") for t in d["teamBatting"]}
    if teams <= VALID_TEAMS and len(teams) >= 2:
        ok(f"{name}: チーム構成")
    else:
        ng(f"{name}: チーム構成", str(teams - VALID_TEAMS))

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
    for scope in ("2026_league", "2026_fresh"):
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
        warn("run_expectancy.json: 想定外の状態キー (ソース CSV の外れ値行と推定。パイプライン側で要確認)",
             f"{len(anomalous)}件: {anomalous}")


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

    groups = {k: v for k, v in d.items() if k != "cutoff"}
    if not groups:
        ng("competitions_2026.json", "大会グループが空")
        return

    listed_dates = []
    for key, grp in groups.items():
        if "label" not in grp or "dates" not in grp:
            ng("competitions_2026.json", f"グループ '{key}' に label/dates がない")
            return
        listed_dates.extend(grp["dates"])

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

    # 2026年大会別ファイル (yearData_2026_{league,fresh}.json) — CLAUDE.md に記載
    for scope in ("2026_league", "2026_fresh"):
        check_year_data(data_dir, scope)

    numeric_years = [y for y in years if y != "All"]
    check_tendencies(data_dir, "All")
    if numeric_years:
        check_tendencies(data_dir, numeric_years[-1])
    # 2026年大会別の傾向ファイル (tendencies_2026_{league,fresh}.json)
    for scope in ("2026_league", "2026_fresh"):
        check_tendencies(data_dir, scope)
    check_models(data_dir)
    check_competitions(data_dir)
    check_competitions_date_files(data_dir)
    check_run_expectancy(data_dir)
    check_defense_data(data_dir)
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
