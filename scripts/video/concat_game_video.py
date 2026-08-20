#!/usr/bin/env python3
"""concat_game_video.py — Watson1 のイニング別クリップを試合1本に連結する。

Watson1 (~/diamondlab) の動画タグ付けは「イニング別に切り出したクリップ + そのクリップ
内での秒数」で球と動画を紐づけている。YouTube に上げるとき 17 本を個別にアップすると
動画IDの収集が現実的でないため、時系列順に1本へ連結して「試合1本 = 動画1本」にする。

連結は再エンコードなしの stream copy。各クリップの連結後の開始秒をマニフェストに残し、
export_video_links.py がそれを使って「クリップ内オフセット → 通し秒数」に変換する。

使い方:
  python3 scripts/video/concat_game_video.py                  # 紐づけのある全試合
  python3 scripts/video/concat_game_video.py --game-id 30
  python3 scripts/video/concat_game_video.py --date 2026-05-16
  python3 scripts/video/concat_game_video.py --dry-run        # 構成と尺だけ表示

出力: OUT_DIR/{game_uid}.mp4 と OUT_DIR/{game_uid}.json (マニフェスト)
      既定の OUT_DIR は ~/.diamondlab/videos/upload
終了コード: 0=成功, 1=失敗あり
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

DB_PATH = os.environ.get(
    "WATSON1_DB", os.path.expanduser("~/.diamondlab/diamondlab.sqlite3"))
OUT_DIR = os.environ.get(
    "VIDEO_UPLOAD_DIR", os.path.expanduser("~/.diamondlab/videos/upload"))

# 連結後の実尺と積み上げ計算のズレをこの秒数まで許容する。
# stream copy なので通常は 0.1 秒未満。超えたらオフセットが信用できない。
DRIFT_TOLERANCE_SEC = 1.0

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def ffmpeg_exe() -> str:
    """ffmpeg のパス。imageio-ffmpeg 同梱を優先、無ければシステム。"""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:  # noqa: BLE001
        pass
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("ffmpeg が見つかりません (pip install imageio-ffmpeg)")
    return exe


def probe_duration(ffmpeg: str, path: str) -> float:
    """コンテナの Duration を秒で返す。ffprobe 無しで済ませるため ffmpeg の
    標準エラー出力から読む (入力を開くだけでデコードはしない)。"""
    r = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True)
    m = _DURATION_RE.search(r.stderr)
    if not m:
        raise RuntimeError(f"尺を取得できません: {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def inning_sort_key(row: sqlite3.Row) -> tuple:
    """時系列順 (1回表 → 1回裏 → 2回表 …)。同一イニングの分割は label/id で安定化。"""
    return (row["inning"], 0 if row["top_bottom"] == "Top" else 1,
            row["label"] or "", row["id"])


def load_games(con: sqlite3.Connection, game_id, date) -> list[sqlite3.Row]:
    sql = """SELECT DISTINCT g.id, g.game_uid, g.date, g.away_team, g.home_team
             FROM games g
             JOIN pitches p ON p.game_id = g.id
             JOIN pitch_videos pv ON pv.pitch_id = p.id"""
    args: list = []
    if game_id is not None:
        sql += " WHERE g.id = ?"
        args.append(game_id)
    elif date:
        sql += " WHERE g.date = ?"
        args.append(date)
    sql += " ORDER BY g.date"
    return con.execute(sql, args).fetchall()


def load_clips(con: sqlite3.Connection, game_id: int) -> list[sqlite3.Row]:
    """イニング別クリップ (inning が入っているもの)。全体動画は除く。"""
    rows = con.execute(
        "SELECT id, file_path, inning, top_bottom, label FROM videos "
        "WHERE game_id = ? AND inning IS NOT NULL", (game_id,)).fetchall()
    return sorted(rows, key=inning_sort_key)


def build_game(ffmpeg: str, con: sqlite3.Connection, game: sqlite3.Row,
               out_dir: str, dry_run: bool) -> bool:
    label = f"{game['date']} {game['away_team']} @ {game['home_team']}"
    print(f"\n=== {label}  (game_id={game['id']}, {game['game_uid']})")

    clips = load_clips(con, game["id"])
    if not clips:
        print("  ✗ イニング別クリップが登録されていません")
        return False

    missing = [c["file_path"] for c in clips if not os.path.exists(c["file_path"])]
    if missing:
        print(f"  ✗ ファイルが見つかりません ({len(missing)}件): {missing[0]}")
        return False

    # 紐づけのあるクリップを把握しておく (連結には全クリップを含めるが、
    # 紐づけゼロのクリップしか無い試合を作っても意味がないため確認する)
    linked = {r[0] for r in con.execute(
        "SELECT DISTINCT pv.video_id FROM pitch_videos pv "
        "JOIN pitches p ON p.id = pv.pitch_id WHERE p.game_id = ?",
        (game["id"],))}

    entries, cursor = [], 0.0
    for c in clips:
        dur = probe_duration(ffmpeg, c["file_path"])
        entries.append({
            "video_id": c["id"],
            "label": c["label"] or f"{c['inning']}回{c['top_bottom']}",
            "inning": c["inning"],
            "topBottom": c["top_bottom"],
            "file": os.path.basename(c["file_path"]),
            "start": round(cursor, 3),
            "duration": round(dur, 3),
            "linked": c["id"] in linked,
        })
        cursor += dur

    total = cursor
    n_linked = sum(1 for e in entries if e["linked"])
    print(f"  クリップ {len(entries)}本 (紐づけあり {n_linked}本) / "
          f"通し尺 {int(total // 60)}分{total % 60:04.1f}秒")
    for e in entries:
        mark = "●" if e["linked"] else "○"
        print(f"    {mark} {e['label']:<8} 開始 {e['start']:>8.1f}s  "
              f"尺 {e['duration']:>7.1f}s")
    if n_linked == 0:
        print("  ✗ 紐づけ済みクリップがありません (Watson1 でタグ付けしてください)")
        return False

    manifest = {
        "gameUid": game["game_uid"],
        "gameId": game["id"],
        "date": game["date"],
        "awayTeam": game["away_team"],
        "homeTeam": game["home_team"],
        "totalDuration": round(total, 3),
        "clips": entries,
    }

    if dry_run:
        print("  (dry-run: 連結しません)")
        return True

    os.makedirs(out_dir, exist_ok=True)
    out_mp4 = os.path.join(out_dir, f"{game['game_uid']}.mp4")
    list_path = os.path.join(out_dir, f"{game['game_uid']}.concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for c in clips:
            # concat demuxer の書式。パス中の ' は '\'' でエスケープする
            f.write("file '%s'\n" % c["file_path"].replace("'", "'\\''"))

    print(f"  連結中 → {out_mp4}")
    r = subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c", "copy", "-movflags", "+faststart", out_mp4],
        capture_output=True, text=True)
    os.remove(list_path)
    if r.returncode != 0 or not os.path.exists(out_mp4):
        print("  ✗ ffmpeg 失敗:")
        print("    " + "\n    ".join(r.stderr.strip().splitlines()[-8:]))
        return False

    actual = probe_duration(ffmpeg, out_mp4)
    drift = abs(actual - total)
    manifest["actualDuration"] = round(actual, 3)
    if drift > DRIFT_TOLERANCE_SEC:
        print(f"  ✗ 尺のズレが大きすぎます (積み上げ {total:.2f}s / 実際 {actual:.2f}s "
              f"= {drift:.2f}s)。このままだとオフセットがずれます")
        return False

    out_json = os.path.join(out_dir, f"{game['game_uid']}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    size_gb = os.path.getsize(out_mp4) / 1e9
    print(f"  ✓ 完了 {size_gb:.2f} GB / 尺 {actual:.1f}s (ズレ {drift:.2f}s)")
    print(f"    マニフェスト: {out_json}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", type=int, help="Watson1 の games.id")
    ap.add_argument("--date", help="試合日 YYYY-MM-DD")
    ap.add_argument("--out", default=OUT_DIR, help=f"出力先 (既定 {OUT_DIR})")
    ap.add_argument("--dry-run", action="store_true", help="構成と尺の確認だけ")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        sys.exit(f"Watson1 のDBが見つかりません: {DB_PATH}")

    ffmpeg = ffmpeg_exe()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    games = load_games(con, args.game_id, args.date)
    if not games:
        print("対象の試合がありません (球↔動画の紐づけがある試合のみ処理します)")
        return 1

    results = [build_game(ffmpeg, con, g, args.out, args.dry_run) for g in games]
    okc, ngc = sum(results), len(results) - sum(results)
    print(f"\n完了: 成功 {okc} 件 / 失敗 {ngc} 件")
    if okc and not args.dry_run:
        print(f"\n次の手順: {args.out} の .mp4 を YouTube に限定公開でアップロードし、\n"
              f"        動画IDを scripts/video/youtube_ids.json に記入してから\n"
              f"        python3 scripts/video/export_video_links.py を実行してください")
    return 1 if ngc else 0


if __name__ == "__main__":
    sys.exit(main())
