#!/usr/bin/env python3
"""make_overlay.py — 試合動画に TrackMan データを焼き込む。

concat_game_video.py が作った連結動画に対し、各球のデータ (球種・球速・回転数・
回転効率・カウント・対戦・投球位置) を ASS 字幕として生成し、libass で焼き込む。

字幕は「その球が来た瞬間から次の球まで」表示し続ける。紐づけの秒数は TrackMan の
時刻差から逆算した値で ±1〜2秒の誤差があるため、一瞬だけ出す作りにすると
ズレが目立つ。次球まで保持すれば多少ズレても読める。

使い方:
  # 試し焼き (1分だけ) — 見た目を確認してから本番を回す
  python3 scripts/video/make_overlay.py --game-uid 20260516-JinguStadium-2 \
      --start 0 --duration 60 --out /tmp/test.mp4
  # 本番 (全編)
  python3 scripts/video/make_overlay.py --game-uid 20260516-JinguStadium-2
  # ASS だけ作る (焼かない)
  python3 scripts/video/make_overlay.py --game-uid ... --ass-only
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get(
    "WATSON1_DB", os.path.expanduser("~/.diamondlab/diamondlab.sqlite3"))
UPLOAD_DIR = os.environ.get(
    "VIDEO_UPLOAD_DIR", os.path.expanduser("~/.diamondlab/videos/upload"))
FONTS_DIR = os.path.join(HERE, "fonts")
# WSL から見える Windows のフォント。日本語が出せるものを優先順に探す
WINDOWS_FONTS = [
    ("/mnt/c/Windows/Fonts/NotoSansJP-Bold.ttf", "Noto Sans JP"),
    ("/mnt/c/Windows/Fonts/YuGothB.ttc", "Yu Gothic"),
    ("/mnt/c/Windows/Fonts/meiryob.ttc", "Meiryo"),
]

# 出力解像度。元は 718x478 の SD だが、720p に上げてから焼く。
# 文字が滲まないのと、YouTube が 480p 入力に割り当てるビットレートが渋いため。
OUT_W, OUT_H = 1280, 720

# ストライクゾーン。サイトが公開している Zone% / 9分割zones と同じ定義にする
# (tokyo-baseball/update_season.py の is_zone と 9分割集計、compute_team_tendencies.py、
#  フロントの BatterDetailPage._SZ_* / TwoStrikeAnalysis が全部この値)。
# client/src/utils/strikeZone.ts と 3D 系は ±0.2167 / 0.45-1.05 を使っているが、
# あれは描画用で統計とは別物。映像に焼く枠はサイトの数字と一致していないといけない。
PLATE_HALF_W = 0.253
ZONE_BOTTOM, ZONE_TOP = 0.48, 1.09

# データパネル (左下)。放送側のスコア表示が右下に出るので、必ず左に寄せる。
# ミニゾーンもパネル内に入れる — 画面の素の場所に置くと放送グラフィックと重なって潰れる。
# パネル高さは中身で変える。打球データは 510球中 140球にしか無く、固定高にすると
# 残り 370球で下半分が空箱になって映像を無駄に隠す。
PANEL_L, PANEL_T, PANEL_R = 40, 366, 640
PANEL_H_PITCH = 258          # 投球データだけのとき
PANEL_H_BATTED = 320         # 打球データも載るとき
ZONE_CX, ZONE_CY = 555.0, 436.0      # パネル内右上
ZONE_PX_PER_M = 210.0                 # 1m あたりのピクセル
ZONE_MID_H = (ZONE_TOP + ZONE_BOTTOM) / 2

# 球種ごとの色 (ASS は &HBBGGRR)
PITCH_COLORS = {
    "Fastball": "&H4763F5", "Sinker": "&H2196F3", "Cutter": "&H00BCD4",
    "Slider": "&HE5C74A", "Curveball": "&HF5A76B", "ChangeUp": "&H7BC96F",
    "Splitter": "&HC77BE5", "Knuckleball": "&HAAAAAA",
}
PITCH_JA = {
    "Fastball": "ストレート", "Sinker": "シンカー", "Cutter": "カット",
    "Slider": "スライダー", "Curveball": "カーブ", "ChangeUp": "チェンジアップ",
    "Splitter": "フォーク", "Knuckleball": "ナックル", "Other": "その他",
}
RESULT_JA = {
    "StrikeCalled": "見逃し", "StrikeSwinging": "空振り", "BallCalled": "ボール",
    "FoulBall": "ファウル", "FoulBallNotFieldable": "ファウル",
    "FoulBallFieldable": "ファウル", "InPlay": "インプレー",
    "HitByPitch": "死球", "BallinDirt": "ボール",
}
# インプレーのときは PitchCall の「インプレー」より打席結果を出したい
PLAY_RESULT_JA = {
    "Out": "アウト", "Single": "ヒット", "Double": "二塁打", "Triple": "三塁打",
    "HomeRun": "本塁打", "Error": "エラー", "Sacrifice": "犠打",
    "FieldersChoice": "野選", "StolenBase": "盗塁",
}
# 打球データの見出し色 (投球データと区別する)
BATTED_COLOR = "&H4FD8FF"   # 淡いオレンジ


def find_font() -> tuple[str, str]:
    """使えるフォントを探し、(family名, fontsdir) を返す。"""
    os.makedirs(FONTS_DIR, exist_ok=True)
    for src, family in WINDOWS_FONTS:
        if os.path.exists(src):
            dst = os.path.join(FONTS_DIR, os.path.basename(src))
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
            return family, FONTS_DIR
    # 同梱済みのものがあれば使う
    if os.path.isdir(FONTS_DIR) and os.listdir(FONTS_DIR):
        return "Noto Sans JP", FONTS_DIR
    sys.exit("日本語フォントが見つかりません "
             f"({', '.join(p for p, _ in WINDOWS_FONTS)} のいずれかが必要)")


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
        sys.exit("ffmpeg が見つかりません")
    return exe


def ts(sec: float) -> str:
    """ASS の時刻書式 H:MM:SS.cc"""
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def esc(text) -> str:
    """ASS の特殊文字を潰す。"""
    return str(text or "").replace("\\", "").replace("{", "(").replace("}", ")")


def short_name(full) -> str:
    """'Sukegawa, Taishi' → 'Sukegawa'"""
    return esc(str(full or "").split(",")[0].strip())


def _bar(x0: float, y0: float, x1: float, y1: float) -> str:
    """塗り矩形の描画コマンド。ASS の \\p1 はパスを必ず塗るので、枠線は
    「細い矩形」を並べて表現する (輪郭線として描く手段が無い)。"""
    return (f"m {x0:.0f} {y0:.0f} l {x1:.0f} {y0:.0f} "
            f"{x1:.0f} {y1:.0f} {x0:.0f} {y1:.0f}")


def zone_frame_drawing(t: float = 3.0) -> str:
    """ストライクゾーンの外枠を太さ t の帯で描く。"""
    w = PLATE_HALF_W * ZONE_PX_PER_M
    h = (ZONE_TOP - ZONE_BOTTOM) / 2 * ZONE_PX_PER_M
    return " ".join([
        _bar(-w, -h, w, -h + t), _bar(-w, h - t, w, h),
        _bar(-w, -h, -w + t, h), _bar(w - t, -h, w, h),
    ])


def zone_grid_drawing(t: float = 1.5) -> str:
    """9分割の内側線。外枠より薄く描くので別イベントに分ける。"""
    w = PLATE_HALF_W * ZONE_PX_PER_M
    h = (ZONE_TOP - ZONE_BOTTOM) / 2 * ZONE_PX_PER_M
    parts = []
    for i in (1, 2):
        x = -w + (w * 2) / 3 * i
        parts.append(_bar(x, -h, x + t, h))
        y = -h + (h * 2) / 3 * i
        parts.append(_bar(-w, y, w, y + t))
    return " ".join(parts)


def build_ass(rows: list[sqlite3.Row], font: str, game_label: str) -> str:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_W}
PlayResY: {OUT_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Big,{font},46,&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,1,0,0,0,100,100,0,0,1,3,1,7,0,0,0,1
Style: Mid,{font},32,&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,1,0,0,0,100,100,0,0,1,2.5,1,7,0,0,0,1
Style: Small,{font},25,&H00D8D8D8,&H00D8D8D8,&H00101010,&H00000000,0,0,0,0,100,100,0,0,1,2,1,7,0,0,0,1
Style: Draw,{font},20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]

    def ev(layer, start, end, style, text):
        lines.append(f"Dialogue: {layer},{ts(start)},{ts(end)},{style},,0,0,0,,{text}")

    zone_frame = zone_frame_drawing()
    zone_grid = zone_grid_drawing()

    for i, r in enumerate(rows):
        t0 = r["abs_t"]
        t1 = rows[i + 1]["abs_t"] if i + 1 < len(rows) else t0 + 12.0
        t1 = min(t1, t0 + 25.0)       # 投手交代などで間が空きすぎたら打ち切る
        if t1 <= t0:
            continue

        ptype = r["pitch_type"] or r["auto_pitch_type"] or "Other"
        color = PITCH_COLORS.get(ptype, "&HDDDDDD")
        ja = PITCH_JA.get(ptype, ptype)

        # --- 左下パネル: 球種・球速・回転・変化量・リリース -----------
        has_batted = r["exit_speed"] is not None
        panel_b = PANEL_T + (PANEL_H_BATTED if has_batted else PANEL_H_PITCH)
        ev(1, t0, t1, "Draw",
           r"{\pos(0,0)\an7\p1\c&H141414&\alpha&H50&}"
           f"{_bar(PANEL_L, PANEL_T, PANEL_R, panel_b)}"
           r"{\p0}")
        ev(2, t0, t1, "Big", rf"{{\pos(64,{PANEL_T + 14})\c{color}&}}{esc(ja)}")
        velo = f"{r['rel_speed']:.1f}" if r["rel_speed"] is not None else "—"
        ev(2, t0, t1, "Big", rf"{{\pos(64,{PANEL_T + 74})}}{velo}{{\fs28}} km/h")
        spin = f"{r['spin_rate']:.0f} rpm" if r["spin_rate"] is not None else "回転数 —"
        ev(2, t0, t1, "Small", rf"{{\pos(64,{PANEL_T + 148})}}{spin}")
        if r["induced_vert_break"] is not None and r["horz_break"] is not None:
            ev(2, t0, t1, "Small",
               rf"{{\pos(64,{PANEL_T + 184})}}変化 縦{r['induced_vert_break']:+.0f} "
               rf"横{r['horz_break']:+.0f} cm")
        rel = []
        if r["rel_height"] is not None:
            rel.append(f"高{r['rel_height']:.2f}")
        if r["rel_side"] is not None:
            rel.append(f"横{r['rel_side']:+.2f}")
        if r["extension"] is not None:
            rel.append(f"Ext {r['extension']:.2f}")
        if rel:
            ev(2, t0, t1, "Small",
               rf"{{\pos(64,{PANEL_T + 220})}}リリース {' / '.join(rel)} m")

        # --- 打球データ (インプレー/ファウルで計測された球のみ) --------
        if has_batted:
            bat = [f"{r['exit_speed']:.1f} km/h"]
            if r["angle"] is not None:
                bat.append(f"{r['angle']:+.1f}°")
            if r["distance"] is not None:
                bat.append(f"{r['distance']:.0f} m")
            ev(1, t0, t1, "Draw",
               rf"{{\pos(0,0)\an7\p1\c&HFFFFFF&\alpha&H70&}}"
               f"{_bar(64, PANEL_T + 262, PANEL_R - 64, PANEL_T + 263)}"
               r"{\p0}")
            ev(2, t0, t1, "Mid",
               rf"{{\pos(64,{PANEL_T + 274})\c{BATTED_COLOR}&}}打球"
               rf"{{\c&HFFFFFF&}}  {'  '.join(bat)}")

        # --- パネル内右側: ミニゾーン + 投球位置 ----------------------
        ev(1, t0, t1, "Draw",
           rf"{{\pos({ZONE_CX},{ZONE_CY})\an7\p1\c&HFFFFFF&\alpha&H40&}}{zone_grid}{{\p0}}")
        ev(1, t0, t1, "Draw",
           rf"{{\pos({ZONE_CX},{ZONE_CY})\an7\p1\c&HFFFFFF&\alpha&H10&}}{zone_frame}{{\p0}}")
        if r["plate_loc_side"] is not None and r["plate_loc_height"] is not None:
            dx = ZONE_CX + r["plate_loc_side"] * ZONE_PX_PER_M
            dy = ZONE_CY - (r["plate_loc_height"] - ZONE_MID_H) * ZONE_PX_PER_M
            ev(2, t0, t1, "Draw",
               rf"{{\pos({dx:.0f},{dy:.0f})\an7\p1\c{color}&\alpha&H00&"
               r"\bord2.5\3c&H101010&}m -10 0 b -10 -13 10 -13 10 0 "
               r"b 10 13 -10 13 -10 0{\p0}")

        # --- 上部: 対戦・カウント・結果 -------------------------------
        matchup = f"{short_name(r['pitcher'])} → {short_name(r['batter'])}"
        inning = f"{r['inning']}回{'表' if r['top_bottom'] == 'Top' else '裏'}"
        count = f"{r['balls']}-{r['strikes']}"
        res = (PLAY_RESULT_JA.get(r["play_result"])
               or RESULT_JA.get(r["pitch_call"], ""))
        ev(2, t0, t1, "Mid",
           rf"{{\pos(40,32)\an7}}{inning}  {matchup}"
           rf"{{\fs26\c&HB0B0B0&}}   カウント {count}"
           + (rf"{{\c&HFFFFFF&}}   {res}" if res else ""))

    ev = None  # noqa: F841
    lines.append(f"Dialogue: 0,{ts(0)},{ts(6)},Small,,0,0,0,,"
                 rf"{{\pos({OUT_W - 40},32)\an9\c&H909090&}}{esc(game_label)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-uid", required=True)
    ap.add_argument("--start", type=float, default=0.0, help="切り出し開始秒")
    ap.add_argument("--duration", type=float, help="切り出し秒数 (省略で全編)")
    ap.add_argument("--out", help="出力先 (省略で {UID}_overlay.mp4)")
    ap.add_argument("--ass-only", action="store_true", help="ASSだけ作る")
    ap.add_argument("--crf", type=int, default=21, help="x264 CRF (小さいほど高画質)")
    args = ap.parse_args()

    import json
    manifest_path = os.path.join(UPLOAD_DIR, f"{args.game_uid}.json")
    src = os.path.join(UPLOAD_DIR, f"{args.game_uid}.mp4")
    for p in (manifest_path, src):
        if not os.path.exists(p):
            sys.exit(f"見つかりません: {p} (先に concat_game_video.py を実行)")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    starts = {c["video_id"]: c["start"] for c in manifest["clips"]}
    game_label = (f"{manifest['date']} {manifest['awayTeam']} @ {manifest['homeTeam']}")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute("""
            SELECT p.pitcher, p.batter, p.inning, p.top_bottom, p.balls, p.strikes,
                   p.pitch_type, p.auto_pitch_type, p.pitch_call, p.play_result,
                   p.rel_speed, p.spin_rate, p.plate_loc_side, p.plate_loc_height,
                   p.induced_vert_break, p.horz_break,
                   p.rel_height, p.rel_side, p.extension,
                   p.exit_speed, p.angle, p.distance,
                   pv.video_id, pv.offset_sec
            FROM pitch_videos pv JOIN pitches p ON p.id = pv.pitch_id
            WHERE p.game_id = ?""", (manifest["gameId"],)):
        if r["video_id"] not in starts:
            continue
        d = dict(r)
        d["abs_t"] = starts[r["video_id"]] + r["offset_sec"]
        rows.append(d)
    rows.sort(key=lambda d: d["abs_t"])
    if not rows:
        sys.exit("紐づけ済みの球がありません")
    print(f"  {game_label}: {len(rows)}球ぶんの字幕を生成")

    font, fontsdir = find_font()
    ass_path = os.path.join(UPLOAD_DIR, f"{args.game_uid}.ass")
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(build_ass(rows, font, game_label))
    print(f"  ✓ {ass_path} (フォント: {font})")
    if args.ass_only:
        return 0

    out = args.out or os.path.join(UPLOAD_DIR, f"{args.game_uid}_overlay.mp4")
    ff = ffmpeg_exe()
    # -ss を -i の前に置くと ASS の時刻とズレるので、必ず入力の後に置く
    cmd = [ff, "-y", "-i", src]
    if args.start:
        cmd += ["-ss", str(args.start)]
    if args.duration:
        cmd += ["-t", str(args.duration)]
    cmd += [
        "-vf", (f"scale={OUT_W}:{OUT_H}:flags=lanczos,"
                f"ass={ass_path}:fontsdir={fontsdir}"),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(args.crf),
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out,
    ]
    print(f"  焼き込み中 → {out}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  ✗ ffmpeg 失敗:")
        print("    " + "\n    ".join(r.stderr.strip().splitlines()[-12:]))
        return 1
    print(f"  ✓ 完了 {os.path.getsize(out) / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
