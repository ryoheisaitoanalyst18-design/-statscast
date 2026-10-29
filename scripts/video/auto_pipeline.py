#!/usr/bin/env python3
"""auto_pipeline.py — 試合動画をサイトに出すまでを一気に回す (OPERATIONS.md「試合動画の紐づけ」②〜⑤).

  1. 紐づけ    ~/diamondlab/auto_link_video.py --scan-dir <動画フォルダ>
              (どの試合か判定 → 全球を紐づけ → イニング別に切り出し。--videos-dir 指定時のみ)
  2. 焼き込み  batch_burn.py (連結 → データ焼き込み → 全編デコード検査)
  3. YouTube   upload_youtube.py (限定公開でアップ → 処理完了・限定公開・日本で再生可を確認 → ID 記入)
  4. サイト反映 export_video_links.py → ./update_and_deploy.sh --data-only (links.json が変わったときだけ)

各段は冪等 (済んだものは飛ばす) なので、途中で止まっても再実行で続きから。
同時に 2 つは走らない (ロック)。結果の要約は ~/video_pipeline_status.txt に書き、
Windows の通知 (成功・失敗とも) でも知らせる。実行中は Windows をスリープさせない
(このPCはモダンスタンバイで、スリープに入ると WSL ごと止まり焼き込み・アップロードが中断する)。
毎晩の定時実行は Windows のタスクスケジューラから (OPERATIONS.md 参照)。

使い方:
  python3 scripts/video/auto_pipeline.py --dry-run
  python3 scripts/video/auto_pipeline.py --videos-dir "/mnt/c/Users/<ユーザー名>/Documents/My Videos"
  python3 scripts/video/auto_pipeline.py --no-deploy     # サイト反映はしない (確認用)
  nohup python3 scripts/video/auto_pipeline.py --videos-dir "..." > ~/video_pipeline.log 2>&1 &
"""
import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from batch_burn import IDS_JSON, UPLOAD_DIR  # noqa: E402

WATSON1 = os.environ.get("WATSON1_DIR", os.path.expanduser("~/diamondlab"))
LINKS_JSON = os.path.join(REPO, "data", "videos", "links.json")
STATUS_PATH = os.path.expanduser("~/video_pipeline_status.txt")
LOCK_PATH = os.path.join(UPLOAD_DIR, ".auto_pipeline.lock")
UPLOAD_EXIT = {0: "完了", 1: "失敗あり", 2: "YouTube 側の処理待ちが残った (次回続き)",
               3: "YouTube へのログインが必要 (upload_youtube.py --auth)"}
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
# PowerShell 自身の AppUserModelID。これで送るとアプリ登録なしで通知が出る
TOAST_APP = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _powershell(script: str, wait: bool = True):
    """Windows PowerShell 5.1 でスクリプトを実行する (日本語を崩さないよう UTF-16 で渡す)."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    cmd = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-EncodedCommand", enc]
    if wait:
        return subprocess.run(cmd, capture_output=True, timeout=60)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify_windows(title: str, body: str) -> None:
    """Windows の通知 (トースト) を出す。出せなくても処理は止めない."""
    if not os.path.exists(POWERSHELL):
        return

    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("'", "&apos;"))
    xml = ("<toast><visual><binding template='ToastGeneric'>"
           f"<text>{esc(title)}</text><text>{esc(body[:250])}</text>"
           "</binding></visual></toast>")
    script = "\n".join([
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] | Out-Null",
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] | Out-Null",
        "$xml = @'", xml, "'@",
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument",
        "$doc.LoadXml($xml)",
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)",
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{TOAST_APP}')"
        ".Show($toast)",
    ])
    try:
        _powershell(script)
    except (OSError, subprocess.SubprocessError):
        pass


@contextlib.contextmanager
def keep_windows_awake(hours: float = 10):
    """実行中は Windows をスリープさせない (SetThreadExecutionState)。

    印のファイルがある間だけ PowerShell が 30 秒ごとに「システム使用中」を宣言し、
    終わったら印を消して解除する。異常終了で印が残っても hours 時間で切れる。"""
    if not os.path.exists(POWERSHELL):
        yield
        return
    flag = os.path.join(UPLOAD_DIR, ".keep_awake")
    open(flag, "w").close()
    winflag = subprocess.run(["wslpath", "-w", flag], capture_output=True,
                             text=True).stdout.strip().replace("'", "''")
    script = "\n".join([
        "$k = Add-Type -Name Power -Namespace StatscastAwake -PassThru -MemberDefinition"
        " '[DllImport(\"kernel32.dll\")] public static extern uint"
        " SetThreadExecutionState(uint f);'",
        f"$end = (Get-Date).AddHours({hours})",
        f"while ((Test-Path -LiteralPath '{winflag}') -and ((Get-Date) -lt $end)) {{",
        "  [void]$k::SetThreadExecutionState([uint32]2147483649)"
        "  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED",
        "  Start-Sleep -Seconds 30",
        "}",
    ])
    try:
        _powershell(script, wait=False)
    except OSError:
        pass
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(flag)


def digest(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return ""


def step(title: str, cmd: list, cwd: str = REPO) -> int:
    print(f"\n━━━ {title} ━━━\n$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd).returncode


def acquire_lock() -> bool:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    if os.path.exists(LOCK_PATH):
        try:
            pid = int(open(LOCK_PATH).read().strip())
            os.kill(pid, 0)                 # 生きていれば実行中
            return False
        except (ValueError, OSError):
            pass                            # 前回の異常終了の残骸
    with open(LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos-dir", help="新しい試合動画を探すフォルダ (紐づけから始める)")
    ap.add_argument("--dry-run", action="store_true", help="各段の処理対象を表示するだけ")
    ap.add_argument("--no-deploy", action="store_true", help="サイトへのデプロイはしない")
    ap.add_argument("--jobs", type=int, default=2, help="焼き込みの並列数 (batch_burn.py)")
    args = ap.parse_args()

    if not args.dry_run and not acquire_lock():
        print("別の auto_pipeline.py が実行中です")
        return 1
    started = dt.datetime.now()
    summary: list[str] = []
    try:
        with (contextlib.nullcontext() if args.dry_run else keep_windows_awake()):
            run_steps(args, summary)
    finally:
        if not args.dry_run and os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)

    took = int((dt.datetime.now() - started).total_seconds() // 60)
    text = (f"{started:%Y-%m-%d %H:%M} 開始 ({took} 分)"
            f"{' [dry-run]' if args.dry_run else ''}\n"
            + "\n".join(f"  {s}" for s in summary) + "\n")
    print("\n" + text)
    attention = ("失敗", "⚠", "判定できない", "ログインが必要")
    ok = not any(a in s for s in summary for a in attention)
    if not args.dry_run:
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        notify_windows(f"試合動画パイプライン: {'完了' if ok else '要確認'}",
                       " / ".join(summary))
    return 0 if ok else 1


def run_steps(args, summary: list[str]) -> None:
    ids_before = digest(IDS_JSON)
    py = sys.executable
    dry = ["--dry-run"] if args.dry_run else []
    if args.videos_dir:
        rc = step("1/4 紐づけ (Watson1)",
                  [py, os.path.join(WATSON1, "auto_link_video.py"),
                   "--scan-dir", args.videos_dir] + dry, cwd=WATSON1)
        summary.append(f"紐づけ: {'OK' if rc == 0 else '判定できない動画あり (ログ参照)'}")
    rc = step("2/4 連結 → 焼き込み → 検査",
              [py, os.path.join(HERE, "batch_burn.py"), "--jobs", str(args.jobs)] + dry)
    summary.append(f"焼き込み: {'OK' if rc == 0 else '失敗あり (ログ参照)'}")
    rc = step("3/4 YouTube にアップロード",
              [py, os.path.join(HERE, "upload_youtube.py")] + dry)
    summary.append(f"YouTube: {UPLOAD_EXIT.get(rc, f'終了コード {rc}')}")

    if args.dry_run:
        print("\n(dry-run: サイト反映はしません)")
    elif digest(IDS_JSON) == ids_before:
        summary.append("サイト反映: 新しい動画なし")
    else:
        links_before = digest(LINKS_JSON)
        step("4/4 サイト反映", [py, os.path.join(HERE, "export_video_links.py")])
        if digest(LINKS_JSON) == links_before:
            summary.append("サイト反映: links.json に変化なし")
        elif args.no_deploy:
            summary.append("サイト反映: links.json 更新済み・デプロイは保留 (--no-deploy)")
        else:
            rc = step("4/4 デプロイ", ["./update_and_deploy.sh", "--data-only"])
            summary.append(f"サイト反映: {'デプロイ完了' if rc == 0 else '⚠ デプロイ失敗'}")


if __name__ == "__main__":
    sys.exit(main())
