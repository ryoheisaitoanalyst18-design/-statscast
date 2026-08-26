#!/usr/bin/env python3
"""batch_burn.py — 紐づけ済みの試合を「連結 → 焼き込み → 検査」まで無人で流す。

秋季リーグは約40試合あり、1試合ずつ張り付いて回すのは現実的でない。
週末に溜まった試合を寝る前に流して朝に受け取る、という運用のためのキュー。

並列度の既定が 2 なのは実測にもとづく。x264 は 12 スレッドまでスケールしないので、
1試合を12スレッドで回すより 2試合を6スレッドずつ回した方が速い
(90秒サンプルで 5.1x → 合計 6.6x / 1試合あたり 22.4分 → 17.3分)。
3本以上にしてもメモリ (このPCは 7GB) とキャッシュを食い合って伸びない。

途中で止めても安全: 完成済み (moov atom がある) の出力は skip する。
逆に未完成ファイルが残っていたら「壊れている」と判断して焼き直す
— YouTube はそれを受け取らないため (2026-08-25 に実際に弾かれた)。

使い方:
  python3 scripts/video/batch_burn.py --dry-run   # 何をやるかだけ表示
  python3 scripts/video/batch_burn.py             # 全対象を処理
  python3 scripts/video/batch_burn.py --jobs 1    # 並列度を落とす (PCを他に使うとき)
  python3 scripts/video/batch_burn.py --crf 23    # 画質を少し落として容量を23%削る
  python3 scripts/video/batch_burn.py --game-uid 20260912-JinguStadium-1
  python3 scripts/video/batch_burn.py --force     # 完成済みも焼き直す

夜間に流すなら (ログが残り、ターミナルを閉じても続く):
  nohup python3 scripts/video/batch_burn.py > ~/batch_burn.log 2>&1 &
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get(
    "WATSON1_DB", os.path.expanduser("~/.diamondlab/diamondlab.sqlite3"))
UPLOAD_DIR = os.environ.get(
    "VIDEO_UPLOAD_DIR", os.path.expanduser("~/.diamondlab/videos/upload"))
IDS_JSON = os.path.join(HERE, "youtube_ids.json")
LOCK_PATH = os.path.join(UPLOAD_DIR, ".batch_burn.lock")

# 1試合あたりの出力はおよそ 2.3GB。空きがこれを下回ったら始めない。
MIN_FREE_GB = 20.0


def ffmpeg_exe() -> str:
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


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")

# 尺の許容誤差。連結は stream copy、焼き込みはトリムしない再エンコードなので
# 本来ぴったり一致する。1秒ずれたら中身を疑う。
DURATION_TOLERANCE_SEC = 1.0


def expected_duration(uid: str) -> float | None:
    """連結マニフェストが記録している実尺。無ければ None。"""
    try:
        with open(os.path.join(UPLOAD_DIR, f"{uid}.json"), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    d = m.get("actualDuration") or m.get("totalDuration")
    return float(d) if d else None


def verify_mp4(ffmpeg: str, path: str, expect: float | None = None) -> bool:
    """mp4 として使える状態か。

    moov atom の有無だけでは不十分。`-movflags +faststart` は moov を先頭に
    移すので、末尾が欠けたファイルでも moov は読めてしまう。連結マニフェストが
    ある場合は尺も突き合わせて、途中で切れていないことまで確かめる。
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    r = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True)
    if "moov atom not found" in r.stderr:
        return False
    if expect is None:
        return True
    m = _DURATION_RE.search(r.stderr)
    if not m:
        return False
    h, mi, s = m.groups()
    got = int(h) * 3600 + int(mi) * 60 + float(s)
    return abs(got - expect) <= DURATION_TOLERANCE_SEC


def needs_deinterlace(con: sqlite3.Connection, game_id: int) -> bool:
    """素材が rip_dvd.py 由来のインターレース映像かどうか。

    rip_dvd.py は解除せずに取り込み、要否を {元動画}.rip.json に書き残す。
    クリップは元動画から切り出されるので、判定は元動画のファイル名で引く。
    HandBrake 由来 (解除済み) の素材には .rip.json が無いので False になる。
    """
    rows = con.execute(
        "SELECT file_path FROM videos WHERE game_id = ?", (game_id,)).fetchall()
    for (fp,) in rows:
        # クリップ名は「{元動画}_1回表.mp4」。元動画側の .rip.json を探す。
        base = os.path.splitext(fp)[0]
        for cand in (base, base.rsplit("_", 1)[0]):
            side = cand + ".rip.json"
            if os.path.exists(side):
                try:
                    with open(side, encoding="utf-8") as f:
                        return bool(json.load(f).get("interlaced"))
                except (OSError, ValueError):
                    return False
    return False


def verify_deep(ffmpeg: str, path: str) -> tuple[bool, str]:
    """全編デコードして本当に最後まで中身があるか確かめる。

    moov も Duration も末尾が欠けたファイルを見抜けない (どちらもメタデータで、
    実データとは独立に「満尺」と書かれている)。確実なのは実際に流し切ることだけ。
    2.3GB で 1〜2分かかるので、焼いた直後の1回だけ回す。
    """
    r = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-f", "null", "-"],
                       capture_output=True, text=True)
    err = r.stderr.strip()
    if r.returncode != 0 or err:
        return False, (err.splitlines()[-1] if err else f"終了コード {r.returncode}")
    return True, ""


def uploaded_uids() -> set:
    """youtube_ids.json で既に動画IDが入っている試合。アップ済みは焼き直さない。"""
    try:
        with open(IDS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    return {k for k, v in data.items()
            if not k.startswith("_") and isinstance(v, str) and v.strip()}


def candidate_games(con: sqlite3.Connection, game_uid=None) -> list:
    """紐づけのある試合。concat_game_video.py の load_games と同じ条件。"""
    sql = """SELECT DISTINCT g.id, g.game_uid, g.date, g.away_team, g.home_team,
                    COUNT(DISTINCT pv.pitch_id) AS n_pitches
             FROM games g
             JOIN pitches p ON p.game_id = g.id
             JOIN pitch_videos pv ON pv.pitch_id = p.id"""
    args = []
    if game_uid:
        sql += " WHERE g.game_uid = ?"
        args.append(game_uid)
    sql += " GROUP BY g.id ORDER BY g.date"
    return con.execute(sql, args).fetchall()


def run(cmd: list, log_path: str) -> bool:
    """子プロセスを回し、出力をログに追記する。並列実行するので端末には出さない。"""
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(cmd)}\n")
        log.flush()
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    return r.returncode == 0


def process(game: sqlite3.Row, ffmpeg: str, crf: int, threads: int,
            force: bool, deinterlace: bool = False) -> tuple:
    """1試合ぶん。(uid, 状態, メッセージ, 所要秒) を返す。"""
    uid = game["game_uid"]
    t0 = time.time()
    concat = os.path.join(UPLOAD_DIR, f"{uid}.mp4")
    overlay = os.path.join(UPLOAD_DIR, f"{uid}_overlay.mp4")
    log_path = os.path.join(UPLOAD_DIR, f"{uid}.batch.log")

    expect = expected_duration(uid)
    if not force and verify_mp4(ffmpeg, overlay, expect):
        gb = os.path.getsize(overlay) / 1e9
        return (uid, "skip", f"完成済み {gb:.2f} GB", 0.0)

    # 連結。未完成ファイルが残っていたら作り直す (途中で落ちた残骸を掴まない)。
    if force or not verify_mp4(ffmpeg, concat, expect):
        if os.path.exists(concat):
            os.remove(concat)
        ok = run([sys.executable, os.path.join(HERE, "concat_game_video.py"),
                  "--game-id", str(game["id"])], log_path)
        # 連結し直したらマニフェストも更新されるので期待尺を取り直す
        expect = expected_duration(uid)
        if not ok or not verify_mp4(ffmpeg, concat, expect):
            return (uid, "fail", f"連結に失敗 (ログ: {log_path})", time.time() - t0)

    # 焼き込み。壊れた出力が残っていると make_overlay が上書きするが、
    # 途中で落ちた場合に備えてこちらでも消しておく。
    if os.path.exists(overlay):
        os.remove(overlay)
    cmd = [sys.executable, os.path.join(HERE, "make_overlay.py"),
           "--game-uid", uid, "--crf", str(crf)]
    if threads:
        cmd += ["--threads", str(threads)]
    if deinterlace:
        cmd += ["--deinterlace"]
    ok = run(cmd, log_path)
    if not ok or not verify_mp4(ffmpeg, overlay, expect):
        return (uid, "fail", f"焼き込みに失敗 (ログ: {log_path})", time.time() - t0)
    deep_ok, why = verify_deep(ffmpeg, overlay)
    if not deep_ok:
        # 壊れたまま残すと次回 skip されてしまう。消して次回焼き直させる。
        os.remove(overlay)
        return (uid, "fail", f"検査で破損を検出: {why}", time.time() - t0)

    gb = os.path.getsize(overlay) / 1e9
    return (uid, "ok", f"{gb:.2f} GB", time.time() - t0)


def hhmm(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}時間{m:02d}分" if h else f"{m}分{s:02d}秒"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=2, help="並列で焼く試合数 (既定2)")
    ap.add_argument("--crf", type=int, default=21,
                    help="x264 CRF。23 にすると容量が約23%%減る (既定21)")
    ap.add_argument("--game-uid", help="1試合だけ処理する")
    ap.add_argument("--force", action="store_true", help="完成済みも焼き直す")
    ap.add_argument("--include-uploaded", action="store_true",
                    help="YouTubeにアップ済みの試合も対象にする")
    ap.add_argument("--dry-run", action="store_true", help="対象を表示するだけ")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        sys.exit(f"Watson1 のDBが見つかりません: {DB_PATH}")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ffmpeg = ffmpeg_exe()

    # 二重起動を防ぐ。同じ出力先を2つのバッチが奪い合うと両方壊れる。
    if os.path.exists(LOCK_PATH):
        with open(LOCK_PATH, encoding="utf-8") as f:
            who = f.read().strip()
        sys.exit(f"別のバッチが動いています ({who})\n"
                 f"動いていないと確信できるなら削除: rm {LOCK_PATH}")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    games = candidate_games(con, args.game_uid)
    if not games:
        print("紐づけのある試合がありません (Watson1 でアンカーを打って自動リンクしてください)")
        return 0

    done = set() if args.include_uploaded else uploaded_uids()
    targets = [g for g in games if g["game_uid"] not in done]

    print(f"紐づけ済み {len(games)} 試合 / うちアップ済み除外 {len(games) - len(targets)} 試合")
    for g in targets:
        overlay = os.path.join(UPLOAD_DIR, f"{g['game_uid']}_overlay.mp4")
        state = ("完成済み" if (not args.force and verify_mp4(
            ffmpeg, overlay, expected_duration(g["game_uid"]))) else "要処理")
        print(f"  [{state}] {g['date']} {g['away_team']} @ {g['home_team']}  "
              f"{g['game_uid']}  {g['n_pitches']}球")
    todo = [g for g in targets
            if args.force
            or not verify_mp4(ffmpeg, os.path.join(
                UPLOAD_DIR, f"{g['game_uid']}_overlay.mp4"),
                expected_duration(g["game_uid"]))]
    if not todo:
        print("\n処理するものはありません。")
        return 0

    # 1試合およそ 2.3GB。足りないまま走らせると全部中途半端に壊れる。
    free_gb = shutil.disk_usage(UPLOAD_DIR).free / 1e9
    need_gb = len(todo) * 4.0        # 連結 + 焼き込みの両方を置く
    print(f"\n処理対象 {len(todo)} 試合 / 並列 {args.jobs} / CRF {args.crf}")
    print(f"ディスク空き {free_gb:.0f} GB (必要見込み {need_gb:.0f} GB)")
    if free_gb < max(MIN_FREE_GB, need_gb):
        print("✗ 空き容量が足りません。アップロード済みの中間ファイルを消してください。")
        return 1
    # 実測: 1試合21.4分 (連結4分 + 焼き込み15分 + 全編検査2.4分)。
    # 並列2で全体1.29倍 — x264 が12スレッドまでスケールしないぶんの取り分で、
    # 「2倍速くなる」わけではない。並列3以上は伸びないので頭打ちにする。
    speedup = 1.0 if args.jobs <= 1 else 1.29
    print(f"所要見込み: 約 {hhmm(len(todo) * 21.4 * 60 / speedup)} "
          f"(1試合21分・並列{args.jobs}として)")

    if args.dry_run:
        print("\n(dry-run: 何も処理しません)")
        return 0

    # x264 は 12 スレッドまでスケールしない。CPUを並列数で素直に割る。
    threads = max(1, (os.cpu_count() or 4) // max(1, args.jobs))
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(f"pid={os.getpid()} started={time.strftime('%Y-%m-%d %H:%M:%S')}")

    results = []
    t0 = time.time()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(process, g, ffmpeg, args.crf, threads, args.force,
                              needs_deinterlace(con, g["id"])): g
                    for g in todo}
            for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
                uid, state, msg, el = fut.result()
                mark = {"ok": "✓", "skip": "－", "fail": "✗"}[state]
                print(f"[{i}/{len(todo)}] {mark} {uid}  {msg}"
                      + (f"  ({hhmm(el)})" if el else ""), flush=True)
                results.append((uid, state, msg))
    finally:
        if os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)

    ok = [r for r in results if r[1] == "ok"]
    fail = [r for r in results if r[1] == "fail"]
    print(f"\n━━━ 完了 {hhmm(time.time() - t0)} ━━━")
    print(f"成功 {len(ok)} / 失敗 {len(fail)}")
    for uid, _, msg in fail:
        print(f"  ✗ {uid}: {msg}")
    if ok:
        print(f"\nアップロード待ち ({UPLOAD_DIR}):")
        for uid, _, msg in sorted(ok):
            print(f"  {uid}_overlay.mp4  {msg}")
        print("\nYouTube に限定公開で上げ、動画IDを "
              f"{IDS_JSON} に記入 → export_video_links.py → --data-only デプロイ")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
