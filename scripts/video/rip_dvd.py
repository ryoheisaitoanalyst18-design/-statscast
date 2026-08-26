#!/usr/bin/env python3
"""rip_dvd.py — 試合DVDを再エンコードせずに吸い出す。

これまでは HandBrake で取り込んでいたが、HandBrake は必ず再エンコードするため
1試合40分かかり、しかも焼き込みと合わせて劣化が2回起きていた。
DVD の中身 (MPEG-2) はそのまま使えるので、映像はストリームコピーで包み直すだけにする。
ドライブの読み出し速度律速になり、40分 → 5〜10分。劣化も焼き込みの1回だけになる。

音声だけ AAC に変換する。DVD の AC3 を MP4 に入れると環境によって再生できないことが
あるためで、音声の再エンコードは数秒で終わる。

インターレース解除はここでは**やらない**。DVD は 720x480 インターレースだが、
ここで解除すると1回余計にエンコードすることになる。焼き込み (make_overlay.py) が
どのみち再エンコードするので、そこで `--deinterlace` を付けて一度に済ませる。
このスクリプトは解除が要るかどうかを判定して出力に書き残す。

使い方:
  python3 scripts/video/rip_dvd.py --dry-run          # 構成を見るだけ
  python3 scripts/video/rip_dvd.py                    # 最長タイトルを吸い出す
  python3 scripts/video/rip_dvd.py --name 20260912_法政明治
  python3 scripts/video/rip_dvd.py --source /mnt/e    # ドライブを明示
  python3 scripts/video/rip_dvd.py --all              # 全タイトル

出力: ~/.diamondlab/videos/{名前}.mp4  (Watson1 にそのまま登録できる)
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

OUT_DIR = os.environ.get(
    "WATSON1_VIDEO_DIR", os.path.expanduser("~/.diamondlab/videos"))
# WSL から見える可能性のあるドライブ。C: はシステムなので探さない。
CANDIDATE_MOUNTS = ["/mnt/d", "/mnt/e", "/mnt/f", "/mnt/g"]
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


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


def find_dvd(source: str | None) -> str:
    """DVD のルート (VIDEO_TS を含むディレクトリ) を返す。"""
    roots = [source] if source else CANDIDATE_MOUNTS
    for r in roots:
        if not r or not os.path.isdir(r):
            continue
        for sub in ("VIDEO_TS", "video_ts", "DVD_RTAV"):
            if os.path.isdir(os.path.join(r, sub)):
                return r
        # ルートに直接 VOB が置かれている場合 (吸い出し済みフォルダなど)
        if glob.glob(os.path.join(r, "*.VOB")) or glob.glob(os.path.join(r, "*.vob")):
            return r
    if source:
        sys.exit(f"DVD の構造が見つかりません: {source}")
    sys.exit(
        "DVD が見つかりません。\n"
        "WSL は光学ドライブを自動マウントしないことがあります。\n"
        "ディスクを入れてから、Windows のドライブレターに合わせて実行してください:\n"
        "  sudo mkdir -p /mnt/d && sudo mount -t drvfs D: /mnt/d\n"
        "その後もう一度このスクリプトを実行してください。")


def find_titles(root: str) -> list[dict]:
    """タイトル (VTS ごとのVOB群) を返す。大きい順。"""
    vts_dir = None
    for sub in ("VIDEO_TS", "video_ts"):
        if os.path.isdir(os.path.join(root, sub)):
            vts_dir = os.path.join(root, sub)
            break

    # DVD-VR (レコーダーで未ファイナライズのディスク)
    vro = glob.glob(os.path.join(root, "DVD_RTAV", "VR_MOVIE.VRO"))
    if vro:
        return [{"title": "VR_MOVIE", "files": vro,
                 "size": sum(os.path.getsize(f) for f in vro)}]

    search = vts_dir or root
    vobs = sorted(glob.glob(os.path.join(search, "*.VOB")) +
                  glob.glob(os.path.join(search, "*.vob")))
    groups: dict[str, list] = {}
    for v in vobs:
        base = os.path.basename(v)
        m = re.match(r"(VTS_\d+)_(\d+)\.VOB", base, re.I)
        if m:
            # _0.VOB はメニュー。中身が無いので飛ばす。
            if int(m.group(2)) == 0:
                continue
            groups.setdefault(m.group(1).upper(), []).append(v)
        else:
            groups.setdefault("OTHER", []).append(v)
    titles = [{"title": k, "files": sorted(v),
               "size": sum(os.path.getsize(f) for f in v)}
              for k, v in groups.items()]
    return sorted(titles, key=lambda t: -t["size"])


def probe(ffmpeg: str, path_or_pipe: list) -> str:
    r = subprocess.run([ffmpeg] + path_or_pipe + ["-hide_banner"],
                       capture_output=True, text=True)
    return r.stderr


def field_order_flag(ffmpeg: str, sample: str) -> str | None:
    """コンテナが申告しているフィールド順 ('top first' 等)。無ければ None。"""
    info = probe(ffmpeg, ["-i", sample])
    for line in info.splitlines():
        if "Video:" not in line:
            continue
        for flag in ("top first", "bottom first"):
            if flag in line:
                return flag
    return None


def detect_interlace(ffmpeg: str, sample: str) -> tuple[bool, str]:
    """インターレース解除が要るか判定する。

    まず idet で実際の縞を数える。ただし放送素材は真っ黒な導入部や静止画が続くと
    Undetermined ばかりになって結論が出ない。その場合はコンテナのフィールド順
    フラグに従う — DVD は 720x480 インターレースが基本なので、
    **判断がつかないときは解除する側に倒す**。
    縞が残ったまま40試合焼く方が、progressive 素材に yadif をかけるより damage が大きい。
    """
    r = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", sample, "-vf", "idet",
         "-frames:v", "400", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    m = re.search(
        r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*"
        r"Progressive:\s*(\d+)\s*Undetermined:\s*(\d+)", r.stderr)
    flag = field_order_flag(ffmpeg, sample)
    if m:
        tff, bff, prog, und = (int(x) for x in m.groups())
        inter = tff + bff
        decided = inter + prog
        if decided >= 40:      # 結論の出たフレームが十分あるときだけ信じる
            pct = inter / decided * 100
            return (pct > 20,
                    f"インターレース {pct:.0f}% "
                    f"(TFF {tff} / BFF {bff} / Progressive {prog} / 不明 {und})")
        detail = f"idet では判定不能 (不明 {und} フレーム)"
    else:
        detail = "idet の出力なし"
    if flag:
        return True, f"{detail} → コンテナのフラグ '{flag}' に従い解除する"
    return False, f"{detail} → フィールド順フラグも無いので解除しない"


def hhmm(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}時間{m:02d}分{s:02d}秒" if h else f"{m}分{s:02d}秒"


def duration_of(stderr: str) -> float | None:
    m = _DURATION_RE.search(stderr)
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def rip(ffmpeg: str, title: dict, out_path: str) -> bool:
    """VOB群を1本に連結して包み直す。映像は無劣化コピー、音声のみ AAC。

    VOB は 1GB ごとに分割された MPEG program stream の断片で、単純に連結すれば
    1本のストリームになる。だから cat で繋いで ffmpeg に流し込むのが最も素直で、
    concat デマクサのようにファイルごとのヘッダ解釈で躓くことがない。
    """
    total_bytes = sum(os.path.getsize(f) for f in title["files"])
    print(f"  吸い出し中 ({total_bytes / 1e9:.2f} GB) → {out_path}")
    cat = subprocess.Popen(["cat"] + title["files"], stdout=subprocess.PIPE)
    cmd = [ffmpeg, "-y", "-hide_banner", "-fflags", "+genpts", "-i", "pipe:0",
           "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "copy",          # ← ここが肝。映像は一切触らない
           "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", out_path]
    r = subprocess.run(cmd, stdin=cat.stdout, capture_output=True, text=True)
    cat.stdout.close()
    cat.wait()
    if r.returncode != 0:
        print("  ✗ 失敗:")
        print("    " + "\n    ".join(r.stderr.strip().splitlines()[-10:]))
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="DVD のマウント先 (省略で自動検出)")
    ap.add_argument("--name", help="出力名 (省略で日時)")
    ap.add_argument("--all", action="store_true", help="全タイトルを吸い出す")
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--dry-run", action="store_true", help="構成を見るだけ")
    args = ap.parse_args()

    ffmpeg = ffmpeg_exe()
    root = find_dvd(args.source)
    print(f"DVD: {root}")

    titles = find_titles(root)
    if not titles:
        sys.exit("再生可能なタイトルが見つかりません (空のディスク？)")

    for i, t in enumerate(titles):
        head = probe(ffmpeg, ["-i", t["files"][0]])
        dur = duration_of(head)
        # CSS 暗号化がかかっていると ffmpeg はストリームを解釈できない
        enc = "Invalid data" in head or "could not find codec" in head.lower()
        mark = "★" if i == 0 else " "
        print(f" {mark} {t['title']}  {len(t['files'])}ファイル  "
              f"{t['size'] / 1e9:.2f} GB  先頭VOB {hhmm(dur) if dur else '尺不明'}"
              + ("  ⚠ 読めません (暗号化の疑い)" if enc else ""))
        if enc:
            print("     → 市販DVDのようなコピー防止がかかっている可能性があります。")
            print("       その場合は MakeMKV での吸い出し (案B) に切り替えてください。")

    picked = titles if args.all else titles[:1]

    # インターレース判定は先頭タイトルの先頭VOBで代表させる
    need_di, detail = detect_interlace(ffmpeg, picked[0]["files"][0])
    print(f"\nインターレース判定: {detail}")
    print("  → 焼き込み時に " + ("`--deinterlace` が必要です" if need_di
                              else "解除は不要です"))

    if args.dry_run:
        print("\n(dry-run: 何も吸い出しません)")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = args.name or time.strftime("%Y%m%d_%H%M%S")
    rc = 0
    for i, t in enumerate(picked):
        name = stamp if len(picked) == 1 else f"{stamp}_{t['title']}"
        out_path = os.path.join(args.out_dir, f"{name}.mp4")
        if os.path.exists(out_path):
            print(f"\n✗ 既にあります: {out_path} (--name で別名にしてください)")
            rc = 1
            continue
        print(f"\n=== {t['title']}")
        t0 = time.time()
        if not rip(ffmpeg, t, out_path):
            rc = 1
            continue
        el = time.time() - t0
        info = probe(ffmpeg, ["-i", out_path])
        dur = duration_of(info) or 0
        gb = os.path.getsize(out_path) / 1e9
        speed = dur / el if el else 0
        print(f"  ✓ 完了 {gb:.2f} GB / 尺 {hhmm(dur)} / 所要 {hhmm(el)} "
              f"({speed:.1f}倍速)")
        # 焼き込み側が解除の要否を判断できるよう書き残す
        with open(os.path.splitext(out_path)[0] + ".rip.json", "w",
                  encoding="utf-8") as f:
            json.dump({"source": root, "title": t["title"],
                       "ripped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "duration": round(dur, 3),
                       "interlaced": need_di, "idet": detail}, f,
                      ensure_ascii=False, indent=2)

    if rc == 0:
        print(f"\n次: Watson1 でこの動画を試合に登録 → アンカーを1球リンク → "
              f"⚡自動リンク → ✂分割 → batch_burn.py")
        if need_di:
            print("     焼き込みは `--deinterlace` を付けること "
                  "(batch_burn.py は .rip.json を見て自動で付けます)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
