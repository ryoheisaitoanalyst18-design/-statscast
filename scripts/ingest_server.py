#!/usr/bin/env python3
"""ローカル取込アプリ — TrackMan CSV/zip をブラウザから投入して本番反映まで自動実行する。

    python3 scripts/ingest_server.py        # → http://127.0.0.1:8787

ブラウザで CSV/zip をドロップ → 「取り込んで本番に反映」→ 進捗ログが流れ、
検証ゲート (validate_data.py) が通ったときだけ push される。

やっていること (中身は既存の update_and_deploy.sh に丸投げ):
  1. アップロードを ~/statscast_inbox/<日時>/ に保存
  2. zip は展開し、中の CSV を「列の和集合」で 1 本に結合 (列ズレ破損行を作らない)
  3. update_and_deploy.sh <combined.csv> --clean を実行
     → マスターCSVバックアップ → QA(打席合体/六大学外/破損行) → --clean 除外 →
       PitchUID 重複除外 → マージ → 全再生成 → 検証ゲート → push → 本番URL確認
  4. 出力を 1 行ずつブラウザへ中継

localhost からしか接続を受けない。stdlib のみ (pandas 等は子プロセス側の依存)。
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
DEPLOY_SH = REPO_DIR / "update_and_deploy.sh"
INBOX = Path.home() / "statscast_inbox"
HOST = "127.0.0.1"
PORT = int(os.environ.get("INGEST_PORT", "8787"))
MAX_UPLOAD = 2 * 1024 * 1024 * 1024  # 1ファイル 2GB まで

csv.field_size_limit(10 * 1024 * 1024)


# ============================================================
# ジョブ (同時に 1 本だけ)
# ============================================================
class Job:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.dir = INBOX / sid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.files: list[Path] = []
        self.lines: list[str] = []
        self.lock = threading.Lock()
        self.state = "idle"  # idle | running | success | failed
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.proc: subprocess.Popen | None = None

    def log(self, text: str) -> None:
        with self.lock:
            self.lines.extend(text.rstrip("\n").split("\n"))

    def tail(self, offset: int) -> tuple[list[str], int]:
        with self.lock:
            return self.lines[offset:], len(self.lines)

    def snapshot(self, offset: int) -> dict:
        new, total = self.tail(offset)
        elapsed = 0.0
        if self.started_at:
            elapsed = (self.finished_at or time.time()) - self.started_at
        return {
            "sid": self.sid,
            "state": self.state,
            "lines": new,
            "offset": total,
            "elapsed": round(elapsed),
            "files": [f.name for f in self.files],
        }


JOBS: dict[str, Job] = {}
CURRENT: Job | None = None
CURRENT_LOCK = threading.Lock()


# ============================================================
# zip 展開 + CSV 結合
# ============================================================
def collect_csvs(job: Job) -> list[Path]:
    """アップロードされた CSV と、zip 内の CSV を集める。"""
    found: list[Path] = []
    for f in job.files:
        if f.suffix.lower() == ".zip":
            dest = job.dir / f"{f.stem}_extracted"
            dest.mkdir(exist_ok=True)
            with zipfile.ZipFile(f) as z:
                for member in z.namelist():
                    if not member.lower().endswith(".csv") or member.endswith("/"):
                        continue
                    # zip slip 対策: 展開先を dest 配下に強制する
                    safe = dest / Path(member).name
                    with z.open(member) as src, open(safe, "wb") as out:
                        shutil.copyfileobj(src, out)
                    found.append(safe)
            job.log(f"  zip 展開: {f.name} → CSV {len([p for p in found if dest in p.parents])} 本")
        elif f.suffix.lower() == ".csv":
            found.append(f)
        else:
            job.log(f"  スキップ (CSV/zip ではない): {f.name}")
    return sorted(found)


def combine_csvs(csvs: list[Path], out_path: Path, job: Job) -> int:
    """複数 CSV を「列の和集合」で 1 本に結合する。

    列構成が違う CSV を単純連結すると列ズレ破損行が生まれる (過去に 1,671 球の実害)。
    DictReader/DictWriter を通し、欠けている列は空欄で埋めることでこれを防ぐ。
    """
    headers: list[str] = []
    seen: set[str] = set()
    for p in csvs:
        with open(p, newline="", encoding="utf-8-sig", errors="replace") as fh:
            cols = next(csv.reader(fh), [])
        for c in cols:
            if c not in seen:
                seen.add(c)
                headers.append(c)

    total = 0
    with open(out_path, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=headers, restval="", extrasaction="ignore")
        writer.writeheader()
        for p in csvs:
            with open(p, newline="", encoding="utf-8-sig", errors="replace") as fh:
                n = 0
                for row in csv.DictReader(fh):
                    row.pop(None, None)  # 列数超過分は捨てる (DictWriter が拒否するため)
                    writer.writerow(row)
                    n += 1
            total += n
            job.log(f"    {p.name}: {n} 行")
    return total


# ============================================================
# 実行
# ============================================================
def run_job(job: Job) -> None:
    global CURRENT
    job.state = "running"
    job.started_at = time.time()
    try:
        job.log("━━━ 入力ファイルの準備 ━━━")
        csvs = collect_csvs(job)
        if not csvs:
            job.log("エラー: CSV が 1 本も見つかりませんでした。")
            job.state = "failed"
            return
        job.log(f"CSV {len(csvs)} 本を結合します:")
        combined = job.dir / "combined.csv"
        rows = combine_csvs(csvs, combined, job)
        job.log(f"結合完了: {rows} 行 → {combined}")
        job.log("")
        job.log("━━━ update_and_deploy.sh 開始 (再生成に 5〜9 分かかります) ━━━")

        env = dict(os.environ, PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(
            ["bash", str(DEPLOY_SH), str(combined), "--clean"],
            cwd=str(REPO_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        job.proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            job.log(line)
        code = proc.wait()
        job.log("")
        if code == 0:
            job.log("✅ 完了: 本番サイトに反映されました。")
            job.state = "success"
        else:
            job.log(f"❌ 失敗 (exit {code})。上のログを確認してください。")
            job.log("   マスターCSVは実行前にバックアップ済みです "
                    "(~/ubuntu_data/trackman_data.backup_*.csv)。")
            job.state = "failed"
    except Exception as exc:  # noqa: BLE001 — 何が起きてもブラウザに理由を出す
        job.log(f"❌ 例外: {type(exc).__name__}: {exc}")
        job.state = "failed"
    finally:
        job.finished_at = time.time()
        with CURRENT_LOCK:
            if CURRENT is job:
                CURRENT = None


# ============================================================
# HTTP
# ============================================================
PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>statscast 取込</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#111;--mut:#666;--line:#ddd;--card:#fafafa;--acc:#1f6feb}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--mut:#8b949e;--line:#30363d;--card:#161b22;--acc:#4493f8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Hiragino Sans","Noto Sans JP",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:24px}
#drop{border:2px dashed var(--line);border-radius:12px;padding:44px 20px;text-align:center;
  background:var(--card);cursor:pointer;transition:.15s}
#drop:hover,#drop.over{border-color:var(--acc);background:color-mix(in srgb,var(--acc) 8%,var(--card))}
#drop b{display:block;font-size:16px;margin-bottom:6px}
#drop span{color:var(--mut);font-size:13px}
ul{list-style:none;padding:0;margin:16px 0 0}
li{display:flex;justify-content:space-between;gap:12px;padding:8px 12px;background:var(--card);
  border:1px solid var(--line);border-radius:8px;margin-bottom:6px;font-size:13px}
li .sz{color:var(--mut);white-space:nowrap}
.row{display:flex;gap:12px;align-items:center;margin-top:20px;flex-wrap:wrap}
button{font:inherit;font-weight:600;padding:10px 20px;border-radius:8px;border:1px solid transparent;
  background:var(--acc);color:#fff;cursor:pointer}
button:disabled{opacity:.45;cursor:not-allowed}
button.ghost{background:transparent;color:var(--fg);border-color:var(--line);font-weight:400}
.badge{font-size:12px;padding:4px 10px;border-radius:99px;border:1px solid var(--line);color:var(--mut)}
.badge.running{border-color:var(--acc);color:var(--acc)}
.badge.success{border-color:#2ea043;color:#2ea043}
.badge.failed{border-color:#f85149;color:#f85149}
pre{margin:16px 0 0;padding:16px;background:var(--card);border:1px solid var(--line);border-radius:10px;
  max-height:460px;overflow:auto;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
  white-space:pre-wrap;word-break:break-word}
.hide{display:none}
</style></head><body><div class="wrap">
<h1>試合データ取込</h1>
<div class="sub">TrackMan の CSV / zip を置くと、QA → マスター取込 → 全再生成 → 検証 → 本番反映まで自動で行います。</div>

<div id="drop">
  <b>ここに CSV / zip をドロップ</b>
  <span>クリックして選択もできます（複数可・zip の中身も自動で取り出します）</span>
</div>
<input type="file" id="pick" multiple accept=".csv,.zip" class="hide">
<ul id="list"></ul>

<div class="row">
  <button id="go" disabled>取り込んで本番に反映</button>
  <button id="clear" class="ghost">選び直す</button>
  <span id="badge" class="badge">待機中</span>
  <span id="time" class="badge hide"></span>
</div>

<pre id="log" class="hide"></pre>
</div><script>
let files=[],sid=null,offset=0,timer=null;
const $=i=>document.getElementById(i);
const fmt=n=>n>1048576?(n/1048576).toFixed(1)+" MB":(n/1024).toFixed(0)+" KB";
function render(){
  $("list").innerHTML=files.map(f=>`<li><span>${f.name.replace(/[<&]/g,"")}</span><span class="sz">${fmt(f.size)}</span></li>`).join("");
  $("go").disabled=files.length===0;
}
function add(fs){ files=files.concat([...fs].filter(f=>/\\.(csv|zip)$/i.test(f.name))); render(); }
const drop=$("drop");
drop.onclick=()=>$("pick").click();
$("pick").onchange=e=>add(e.target.files);
drop.ondragover=e=>{e.preventDefault();drop.classList.add("over")};
drop.ondragleave=()=>drop.classList.remove("over");
drop.ondrop=e=>{e.preventDefault();drop.classList.remove("over");add(e.dataTransfer.files)};
$("clear").onclick=()=>{files=[];render()};
function badge(state){
  const b=$("badge"),m={idle:"待機中",running:"実行中",success:"完了",failed:"失敗"};
  b.className="badge "+state; b.textContent=m[state]||state;
}
$("go").onclick=async()=>{
  $("go").disabled=true; $("clear").disabled=true;
  $("log").className=""; $("log").textContent=""; offset=0;
  badge("running"); $("time").className="badge";
  try{
    const r=await fetch("/session",{method:"POST"});
    if(!r.ok) throw new Error(await r.text());
    sid=(await r.json()).sid;
    for(const f of files){
      $("log").textContent+=`アップロード中: ${f.name} ...\\n`;
      const u=await fetch(`/upload?sid=${sid}&name=`+encodeURIComponent(f.name),{method:"POST",body:f});
      if(!u.ok) throw new Error(await u.text());
    }
    const s=await fetch(`/start?sid=${sid}`,{method:"POST"});
    if(!s.ok) throw new Error(await s.text());
    timer=setInterval(poll,1000); poll();
  }catch(e){
    $("log").textContent+="\\n❌ "+e.message+"\\n"; badge("failed");
    $("go").disabled=false; $("clear").disabled=false;
  }
};
async function poll(){
  const r=await fetch(`/log?sid=${sid}&offset=${offset}`);
  if(!r.ok) return;
  const d=await r.json();
  if(d.lines.length){
    const pre=$("log"), stick=pre.scrollTop+pre.clientHeight>=pre.scrollHeight-30;
    pre.textContent+=d.lines.join("\\n")+"\\n";
    if(stick) pre.scrollTop=pre.scrollHeight;
  }
  offset=d.offset;
  $("time").textContent=`${Math.floor(d.elapsed/60)}分${String(d.elapsed%60).padStart(2,"0")}秒`;
  if(d.state!=="running"){
    clearInterval(timer); badge(d.state);
    $("go").disabled=false; $("clear").disabled=false;
  }
}
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "statscast-ingest"

    def log_message(self, fmt: str, *args) -> None:  # アクセスログは出さない
        pass

    # -- helpers ------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _err(self, code: int, msg: str) -> None:
        self._send(code, msg.encode(), "text/plain; charset=utf-8")

    def _query(self) -> dict[str, str]:
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

    def _job(self) -> Job | None:
        return JOBS.get(self._query().get("sid", ""))

    # -- routes -------------------------------------------------
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/log":
            job = self._job()
            if not job:
                return self._err(404, "unknown session")
            try:
                offset = int(self._query().get("offset", "0"))
            except ValueError:
                offset = 0
            self._json(job.snapshot(offset))
        else:
            self._err(404, "not found")

    def do_POST(self) -> None:
        global CURRENT
        path = urllib.parse.urlparse(self.path).path

        if path == "/session":
            with CURRENT_LOCK:
                if CURRENT is not None:
                    return self._err(409, "別の取り込みが実行中です。完了を待ってください。")
            sid = datetime.now().strftime("%Y%m%d_%H%M%S")
            JOBS[sid] = Job(sid)
            return self._json({"sid": sid})

        if path == "/upload":
            job = self._job()
            if not job:
                return self._err(404, "unknown session")
            if job.state != "idle":
                return self._err(409, "この取り込みは既に開始しています。")
            name = os.path.basename(self._query().get("name", "upload.csv"))
            if not name.lower().endswith((".csv", ".zip")):
                return self._err(400, f"CSV/zip ではありません: {name}")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD:
                return self._err(413, f"サイズが不正です: {length} bytes")
            dest = job.dir / name
            remaining = length
            with open(dest, "wb") as out:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                dest.unlink(missing_ok=True)
                return self._err(400, "アップロードが途中で切れました。")
            job.files.append(dest)
            return self._json({"ok": True, "saved": str(dest), "bytes": length})

        if path == "/start":
            job = self._job()
            if not job:
                return self._err(404, "unknown session")
            if not job.files:
                return self._err(400, "ファイルがありません。")
            with CURRENT_LOCK:
                if CURRENT is not None:
                    return self._err(409, "別の取り込みが実行中です。")
                CURRENT = job
            threading.Thread(target=run_job, args=(job,), daemon=True).start()
            return self._json({"ok": True})

        self._err(404, "not found")


def main() -> int:
    if not DEPLOY_SH.exists():
        print(f"エラー: {DEPLOY_SH} が見つかりません。", file=sys.stderr)
        return 1
    INBOX.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print("=" * 52)
    print(" statscast 取込アプリ")
    print(f"   {url}  をブラウザで開いてください")
    print(f"   受信ファイル置き場: {INBOX}")
    print("   終了: Ctrl-C")
    print("=" * 52)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n終了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
