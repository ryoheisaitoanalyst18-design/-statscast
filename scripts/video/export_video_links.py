#!/usr/bin/env python3
"""export_video_links.py — 球↔動画の紐づけを公開データに書き出す。

入力:
  1. Watson1 の SQLite (pitch_videos: 球 → イニングクリップ + クリップ内秒数)
  2. concat_game_video.py のマニフェスト (各クリップの連結後の開始秒)
  3. scripts/video/youtube_ids.json (試合UID → YouTube動画ID。アップロード後に手で記入)
出力:
  data/videos/links.json

■ 突合キー
公開JSONの球データには GameUID も PitchNo も入っていないため、値そのものを鍵にする。
  球  : "{gameDate}|{pitcher}|{px:.3f}|{pz:.3f}"
  打球: "{gameDate}|{ev:.3f}|{la:.3f}|{distance:.3f}"
どちらも TrackMan の生値がパイプラインを素通りしている列だけで構成している。
`direction` は公開側で座標変換されており一致しないため使ってはいけない。
打者ページの打球方向図は plateLocSide/Height と pitcher を持つので球キーで引ける。

使い方:
  python3 scripts/video/export_video_links.py
  python3 scripts/video/export_video_links.py --placeholder  # ID未取得のまま疎通確認
終了コード: 0=成功, 1=失敗
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DB_PATH = os.environ.get(
    "WATSON1_DB", os.path.expanduser("~/.diamondlab/diamondlab.sqlite3"))
MANIFEST_DIR = os.environ.get(
    "VIDEO_UPLOAD_DIR", os.path.expanduser("~/.diamondlab/videos/upload"))
IDS_PATH = os.path.join(HERE, "youtube_ids.json")
OUT_PATH = os.path.join(REPO, "data", "videos", "links.json")

# チームコード → 表示名 (tokyo-baseball/trackman_qa.py と同じ対応)
TEAM_JA = {
    "TOK": "東京大学", "TOK_TOK": "東京大学", "TOD_TOD": "東京大学",
    "RIK": "立教大学", "RIK_RIK": "立教大学", "RIK_RKK": "立教大学",
    "JUNI_KEI": "慶應義塾大学", "KEI_KEI": "慶應義塾大学", "KEI": "慶應義塾大学",
    "HOS": "法政大学", "HOS_HOS": "法政大学",
    "MEJ": "明治大学", "MEI_MEI": "明治大学", "MEI": "明治大学",
    "WAS_EDA": "早稲田大学", "WAS": "早稲田大学", "WAS_WAS": "早稲田大学",
}


def team_ja(code: str) -> str:
    return TEAM_JA.get(code, code)


def q(x: float) -> int:
    """1/1000 単位の整数に量子化する。

    JS 側は Math.round(x * 1000) を使う。丸め規則を両言語で完全に一致させるため
    "%.3f" は使わない ("%.3f" は Python が偶数丸め、JS の toFixed が切り上げ丸めで、
    0.3125 のように二進で厳密に表せる中間値で答えが割れる)。
    floor(v + 0.5) は Math.round の定義そのもので、同じ double に対し必ず同じ値になる。"""
    import math
    return math.floor(x * 1000 + 0.5)


def pitch_key(date, pitcher, px, pz) -> str:
    return f"{date}|{pitcher}|{q(px)}|{q(pz)}"


def batted_key(date, ev, la, dist) -> str:
    return f"{date}|{q(ev)}|{q(la)}|{q(dist)}"


def load_youtube_ids(placeholder: bool) -> dict:
    if os.path.exists(IDS_PATH):
        with open(IDS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        # "_comment" 等の下線始まりキーは説明用なので無視する
        return {k: v for k, v in raw.items()
                if not k.startswith("_") and isinstance(v, str) and v.strip()}
    if not placeholder:
        print(f"  ⚠ {IDS_PATH} がありません")
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--placeholder", action="store_true",
                    help="YouTube ID 未取得の試合にダミーIDを入れて疎通確認する")
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        sys.exit(f"Watson1 のDBが見つかりません: {DB_PATH}")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    yt = load_youtube_ids(args.placeholder)
    games_out, pitches, batted = [], {}, {}
    collisions, skipped = [], []

    game_rows = con.execute("""
        SELECT DISTINCT g.id, g.game_uid, g.date, g.away_team, g.home_team
        FROM games g JOIN pitches p ON p.game_id = g.id
        JOIN pitch_videos pv ON pv.pitch_id = p.id
        ORDER BY g.date, g.game_uid""").fetchall()

    for g in game_rows:
        uid = g["game_uid"]
        label = f"{g['date']} {team_ja(g['away_team'])}@{team_ja(g['home_team'])}"

        manifest_path = os.path.join(MANIFEST_DIR, f"{uid}.json")
        if not os.path.exists(manifest_path):
            skipped.append(f"{label}: 連結マニフェストが無い "
                           f"(concat_game_video.py を実行してください)")
            continue
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        starts = {c["video_id"]: c["start"] for c in manifest["clips"]}

        video_id = yt.get(uid)
        if not video_id:
            if not args.placeholder:
                skipped.append(f"{label}: YouTube動画IDが未記入 ({uid})")
                continue
            video_id = f"PLACEHOLDER_{uid}"

        gi = len(games_out)
        rows = con.execute("""
            SELECT p.date, p.pitcher, p.plate_loc_side px, p.plate_loc_height pz,
                   p.exit_speed ev, p.angle la, p.distance dist,
                   pv.video_id, pv.offset_sec
            FROM pitch_videos pv JOIN pitches p ON p.id = pv.pitch_id
            WHERE p.game_id = ?""", (g["id"],)).fetchall()

        n_pitch = n_batted = n_orphan = 0
        for r in rows:
            if r["video_id"] not in starts:
                n_orphan += 1     # 連結に含まれないクリップ (全体動画への紐づけ等)
                continue
            t = int(round(starts[r["video_id"]] + r["offset_sec"]))
            if r["px"] is not None and r["pz"] is not None:
                k = pitch_key(r["date"], r["pitcher"], r["px"], r["pz"])
                if k in pitches:
                    collisions.append(f"球キー重複: {k}")
                pitches[k] = [gi, t]
                n_pitch += 1
            if None not in (r["ev"], r["la"], r["dist"]):
                k = batted_key(r["date"], r["ev"], r["la"], r["dist"])
                if k in batted:
                    collisions.append(f"打球キー重複: {k}")
                batted[k] = [gi, t]
                n_batted += 1

        games_out.append({
            "uid": uid,
            "date": g["date"],
            "away": team_ja(g["away_team"]),
            "home": team_ja(g["home_team"]),
            "youtubeId": video_id,
            "duration": manifest.get("actualDuration") or manifest["totalDuration"],
        })
        print(f"  ✓ {label}  球 {n_pitch}件 / 打球 {n_batted}件"
              + (f" / 連結外スキップ {n_orphan}件" if n_orphan else ""))

    for s in skipped:
        print(f"  ⏭ {s}")

    if collisions:
        print(f"\n✗ キーが重複しました ({len(collisions)}件)。"
              f"このままでは誤った動画に飛びます:")
        for c in collisions[:10]:
            print(f"    {c}")
        return 1

    if not games_out:
        print("\n出力できる試合がありません。"
              "concat_game_video.py の実行と youtube_ids.json の記入を確認してください")
        return 1

    doc = {
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "games": games_out,
        "pitches": pitches,
        "batted": batted,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(args.out) / 1024
    print(f"\n✓ {args.out}  ({size_kb:.1f} KB)")
    print(f"  試合 {len(games_out)}件 / 球キー {len(pitches)}件 / 打球キー {len(batted)}件")
    if args.placeholder:
        print("  ⚠ PLACEHOLDER ID が入っています。本番デプロイ前に必ず再生成してください")
    return 0


if __name__ == "__main__":
    sys.exit(main())
