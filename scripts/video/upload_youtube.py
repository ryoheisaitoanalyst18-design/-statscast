#!/usr/bin/env python3
"""upload_youtube.py — 焼き込み済みの試合動画を YouTube に限定公開でアップロードし、
動画IDを youtube_ids.json に記入する (OPERATIONS.md「試合動画の紐づけ」の手順④の自動化)。

認証情報は**このリポジトリ (公開) の外**に置く。update_and_deploy.sh は scripts/ を
丸ごと git add するので、ここに置くと次のデプロイで公開されてしまう。
  ~/.config/statscast/youtube_client_secret.json … Google Cloud で作る OAuth クライアント
                                                   (種類: デスクトップアプリ) をダウンロードして置く
  ~/.config/statscast/youtube_token.json         … --auth で作られる (権限 600)

使い方:
  python3 scripts/video/upload_youtube.py --auth      # 初回だけ。表示される URL を Windows のブラウザで開いて許可
  python3 scripts/video/upload_youtube.py --dry-run   # 何をアップするかだけ表示 (認証不要)
  python3 scripts/video/upload_youtube.py             # 未アップの試合を全部 → 処理完了を待って ID 記入
  python3 scripts/video/upload_youtube.py --game-uid 20261007-JinguStadium-1

サイトに再生できないリンクを出さないため、ID を youtube_ids.json に書くのは
  YouTube 側の処理が終わった / 限定公開になっている / 日本でブロックされていない
を確かめてから。アップ直後の ID は {UPLOAD_DIR}/upload_state.json に控え、途中で
止まっても次回は同じ動画の確認から再開する。アップ前には同じタイトルの動画が
チャンネルに無いかも見る (二重アップロードしない)。

API の使用量 (2026-06 以降): アップロードは専用枠で 1 回 1 単位・1 日 100 本まで。
状態確認は通常枠 (1 日 10,000 単位) から 1 回 1 単位。試合数で困ることはない。

終了コード: 0=完了 (または対象なし) / 1=失敗あり / 2=処理待ちが残った / 3=認証が必要
"""
import argparse
import datetime as dt
import glob
import json
import os
import random
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from batch_burn import (DB_PATH, IDS_JSON, UPLOAD_DIR, expected_duration,  # noqa: E402
                        ffmpeg_exe, verify_mp4)
from export_video_links import team_ja  # noqa: E402

CONFIG_DIR = os.environ.get(
    "STATSCAST_CONFIG", os.path.expanduser("~/.config/statscast"))
CLIENT_SECRET = os.path.join(CONFIG_DIR, "youtube_client_secret.json")
TOKEN = os.path.join(CONFIG_DIR, "youtube_token.json")
STATE_PATH = os.path.join(UPLOAD_DIR, "upload_state.json")
# アップロード + 自分の動画の状態確認だけ (動画の編集・削除の権限は取らない)
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
AUTH_PORT = 8790          # ingest.sh=8787 / approve.sh=8788 と別
CHUNK = 32 * 1024 * 1024  # 途切れても 32MB 単位でやり直せる
RETRY_STATUS = {500, 502, 503, 504}
MAX_RETRY = 10

SETUP_HELP = f"""\
YouTube にアップロードするための初期設定 (最初の1回だけ):
  1. https://console.cloud.google.com/ でプロジェクトを作る
  2. 「API とサービス」→ ライブラリ →「YouTube Data API v3」を有効にする
  3. OAuth 同意画面: ユーザーの種類=外部、テストユーザーに自分の Google アカウントを追加。
     作成後、公開ステータスを「本番環境」にする (「テスト」のままだと 7 日でログインが切れる)
  4. 認証情報 → OAuth クライアント ID を作成 → 種類「デスクトップ アプリ」→ JSON をダウンロード
  5. その JSON を {CLIENT_SECRET} に置く (このリポジトリの中には置かない)。
     JSON をダウンロードできなかったら: python3 scripts/video/upload_youtube.py --setup-client
  6. python3 scripts/video/upload_youtube.py --auth
"""


# ---------------------------------------------------------------- 認証
def _guard_outside_repo(path: str) -> None:
    real = os.path.realpath(path)
    if real == os.path.realpath(REPO) or real.startswith(os.path.realpath(REPO) + os.sep):
        sys.exit(f"認証情報が公開リポジトリの中にあります: {path}\n"
                 "デプロイで公開されてしまうので、リポジトリの外に移してください")


def _save_token(creds) -> None:
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())


def load_creds():
    """保存済みのログインを読む。期限切れなら更新。使えなければ None."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    _guard_outside_repo(CONFIG_DIR)
    if not os.path.exists(TOKEN):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if creds.valid:
        return creds
    if creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            print(f"ログインが無効になっています ({e})")
            return None
        _save_token(creds)
        return creds
    return None


def setup_client() -> int:
    """クライアントの JSON をダウンロードできなかった時用 (新しい Google Auth Platform では
    作成直後のダイアログでしか出ない)。ID とシークレットを端末で入力して JSON を作る。
    シークレットは画面に表示せず、チャット等に貼らずに済む。"""
    import getpass
    _guard_outside_repo(CONFIG_DIR)
    print("Google Cloud の「クライアント」で作った「デスクトップ アプリ」の値を貼り付けてください。")
    cid = input("クライアント ID (….apps.googleusercontent.com): ").strip()
    secret = getpass.getpass("クライアント シークレット (GOCSPX-… 入力は表示されません): ").strip()
    if not cid.endswith(".apps.googleusercontent.com") or not secret:
        print("値が正しくないようです。もう一度やり直してください")
        return 1
    cfg = {"installed": {
        "client_id": cid, "client_secret": secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    fd = os.open(CLIENT_SECRET, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    print(f"保存しました: {CLIENT_SECRET}")
    print("続けてログイン: python3 ~/-statscast/scripts/video/upload_youtube.py --auth")
    return 0


def do_auth() -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow
    _guard_outside_repo(CONFIG_DIR)
    if not os.path.exists(CLIENT_SECRET):
        print(SETUP_HELP)
        return 3
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    # Windows のブラウザから WSL へは IPv4 の localhost で届く
    creds = flow.run_local_server(
        host="localhost", bind_addr="127.0.0.1", port=AUTH_PORT, open_browser=False,
        prompt="consent",
        authorization_prompt_message=(
            "次の URL を Windows のブラウザで開き、試合動画を上げるチャンネルの"
            "アカウントで許可してください:\n{url}\n"),
        success_message="認証できました。このタブは閉じてかまいません。")
    _save_token(creds)
    print(f"ログイン情報を保存しました: {TOKEN}")
    print(f"アップロード先チャンネル: {channel_title(youtube(creds))}")
    return 0


def youtube(creds):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_title(yt) -> str:
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    return items[0]["snippet"]["title"] if items else "(チャンネルが見つかりません)"


# ---------------------------------------------------------------- 状態ファイル
def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def published_ids() -> dict:
    return {k: v for k, v in _load_json(IDS_JSON).items()
            if not k.startswith("_") and isinstance(v, str) and v.strip()}


def write_id(uid: str, video_id: str) -> None:
    """youtube_ids.json に記入 (説明用の _ キーと並び順は保つ)."""
    data = _load_json(IDS_JSON)
    data[uid] = video_id
    _save_json(IDS_JSON, data)


# ---------------------------------------------------------------- 対象・タイトル
def game_info(uid: str) -> dict | None:
    con = sqlite3.connect(DB_PATH)
    try:
        r = con.execute("SELECT date, away_team, home_team FROM games WHERE game_uid=?",
                        (uid,)).fetchone()
    finally:
        con.close()
    return dict(zip(("date", "away", "home"), r)) if r else None


def title_for(uid: str) -> str:
    g = game_info(uid) or {}
    if not g:
        return f"東京六大学野球 {uid}"
    return (f"{g['date']} {team_ja(g['away'])} vs {team_ja(g['home'])} "
            f"東京六大学野球 [{uid}]")


def targets(game_uid: str | None) -> list[str]:
    """焼き込み済みでサイト未反映の試合 + 前回アップして処理待ちの試合."""
    done = published_ids()
    state = _load_json(STATE_PATH)
    uids = {os.path.basename(p)[:-len("_overlay.mp4")]
            for p in glob.glob(os.path.join(UPLOAD_DIR, "*_overlay.mp4"))}
    uids |= {u for u, s in state.items() if s.get("status") == "processing"}
    out = sorted(u for u in uids if u not in done)
    return [u for u in out if u == game_uid] if game_uid else out


# ---------------------------------------------------------------- アップロード
def find_existing(yt, title: str) -> str | None:
    """チャンネルの最近のアップロードに同じタイトルがあればその ID (二重アップ防止)."""
    ch = yt.channels().list(part="contentDetails", mine=True).execute().get("items", [])
    if not ch:
        return None
    uploads = ch[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = yt.playlistItems().list(part="snippet", playlistId=uploads,
                                    maxResults=50).execute().get("items", [])
    for it in items:
        if it["snippet"]["title"] == title:
            return it["snippet"]["resourceId"]["videoId"]
    return None


def upload(yt, path: str, title: str, description: str) -> str:
    import httplib2
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    body = {
        "snippet": {"title": title, "description": description, "categoryId": "17"},
        # 公開 (public) にはしない。サイトから飛ぶ人だけが見られる限定公開
        "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False,
                   "embeddable": True},
    }
    media = MediaFileUpload(path, mimetype="video/mp4", chunksize=CHUNK, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media,
                             notifySubscribers=False)
    resp, retry, last = None, 0, -10
    while resp is None:
        try:
            status, resp = req.next_chunk()
            retry = 0
            if status and status.progress() * 100 >= last + 10:
                last = int(status.progress() * 100)
                print(f"    アップロード {last}%", flush=True)
        except HttpError as e:
            if e.resp.status not in RETRY_STATUS:
                raise
            retry += 1
            err = f"HTTP {e.resp.status}"
        except (OSError, httplib2.HttpLib2Error) as e:
            retry += 1
            err = type(e).__name__
        else:
            continue
        if retry > MAX_RETRY:
            raise RuntimeError(f"再試行 {MAX_RETRY} 回で失敗 ({err})")
        wait = min(2 ** retry, 64) + random.random()
        print(f"    {err} — {wait:.0f} 秒後に再試行 ({retry}/{MAX_RETRY})", flush=True)
        time.sleep(wait)
    return resp["id"]


def check(yt, video_id: str) -> tuple[str, str]:
    """(状態, 説明)。状態: ok / processing / failed / private / blocked / missing."""
    items = yt.videos().list(part="status,processingDetails,contentDetails",
                             id=video_id).execute().get("items", [])
    if not items:
        return "missing", "動画が見つかりません (削除された可能性)"
    v = items[0]
    st = v.get("status", {})
    up = st.get("uploadStatus")
    if up in ("failed", "rejected", "deleted"):
        why = st.get("failureReason") or st.get("rejectionReason") or up
        return "failed", f"YouTube 側で {up} ({why})"
    if up != "processed":
        prog = v.get("processingDetails", {}).get("processingProgress", {})
        left = prog.get("timeLeftMs")
        return "processing", (f"処理中 (残り約 {int(left) // 60000} 分)" if left
                              else "処理中")
    if st.get("privacyStatus") != "unlisted":
        return "private", (f"公開設定が {st.get('privacyStatus')} になっています。"
                           "YouTube Studio で「限定公開」に変えてから再実行してください")
    rr = v.get("contentDetails", {}).get("regionRestriction", {})
    if "JP" in rr.get("blocked", []) or ("allowed" in rr and "JP" not in rr["allowed"]):
        return "blocked", "日本で再生できない状態です (著作権の申し立て等)。YouTube Studio で確認してください"
    return "ok", "処理完了・限定公開"


def remove_intermediate(uid: str) -> None:
    """YouTube で再生できると確認できた試合の連結・焼き込みファイル (1試合約4GB) を消す。
    連結マニフェスト {uid}.json は export_video_links.py がクリップの開始秒に使うので残す。
    元の録画とイニング別クリップ (Watson1) も残す。"""
    freed = 0
    for name in (f"{uid}.mp4", f"{uid}_overlay.mp4"):
        p = os.path.join(UPLOAD_DIR, name)
        if os.path.exists(p):
            freed += os.path.getsize(p)
            os.remove(p)
    if freed:
        print(f"    中間ファイルを削除 ({freed / 1e9:.1f} GB)")


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auth", action="store_true", help="初回ログイン (ブラウザで許可)")
    ap.add_argument("--setup-client", action="store_true",
                    help="クライアントの JSON が無いとき、ID とシークレットを入力して作る")
    ap.add_argument("--dry-run", action="store_true", help="対象を表示するだけ")
    ap.add_argument("--game-uid", help="1試合だけ")
    ap.add_argument("--wait-min", type=float, default=90,
                    help="YouTube 側の処理完了を待つ最大分数 (既定90。超えたら次回に持ち越し)")
    ap.add_argument("--poll-sec", type=float, default=60, help="処理状況を見る間隔 (秒)")
    ap.add_argument("--keep-files", action="store_true",
                    help="YouTube で再生できると確認した後も連結・焼き込みファイルを消さない")
    args = ap.parse_args()

    if args.setup_client:
        return setup_client()
    if args.auth:
        return do_auth()

    todo = targets(args.game_uid)
    state = _load_json(STATE_PATH)
    if not todo:
        print("アップロード対象の試合はありません")
        return 0
    ffmpeg = ffmpeg_exe()
    print(f"対象 {len(todo)} 試合")
    for uid in todo:
        s = state.get(uid, {})
        if s.get("id"):
            print(f"  {uid}: アップ済み・確認待ち (ID {s['id']})")
            continue
        path = os.path.join(UPLOAD_DIR, f"{uid}_overlay.mp4")
        ok = verify_mp4(ffmpeg, path, expected_duration(uid))
        size = os.path.getsize(path) / 1e9 if os.path.exists(path) else 0
        print(f"  {uid}: {size:.2f} GB {'検査OK' if ok else '⚠ 検査NG (焼き直しが必要)'}"
              f" — {title_for(uid)}")
    if args.dry_run:
        print("\n(dry-run: アップロードしません)")
        return 0

    creds = load_creds()
    if creds is None:
        print("\nYouTube にログインしていません (または期限切れ)。")
        print(SETUP_HELP if not os.path.exists(CLIENT_SECRET)
              else "  python3 scripts/video/upload_youtube.py --auth を実行してください")
        return 3
    yt = youtube(creds)
    print(f"アップロード先チャンネル: {channel_title(yt)}")

    failed = []
    for uid in todo:
        if state.get(uid, {}).get("id"):
            continue
        path = os.path.join(UPLOAD_DIR, f"{uid}_overlay.mp4")
        if not verify_mp4(ffmpeg, path, expected_duration(uid)):
            failed.append((uid, "動画ファイルが不完全 (batch_burn.py で焼き直し)"))
            continue
        title = title_for(uid)
        vid = find_existing(yt, title)
        if vid:
            print(f"\n■ {uid}: 同じタイトルの動画が既にあるので使います (ID {vid})")
        else:
            print(f"\n■ {uid}: アップロード開始 ({os.path.getsize(path) / 1e9:.2f} GB)")
            g = game_info(uid) or {}
            desc = (f"東京六大学野球 {g.get('date', '')} "
                    f"{team_ja(g.get('away', ''))} vs {team_ja(g.get('home', ''))}\n"
                    "チーム分析用 (限定公開)")
            try:
                vid = upload(yt, path, title, desc)
            except Exception as e:  # noqa: BLE001
                failed.append((uid, f"アップロード失敗: {e}"))
                continue
        state[uid] = {"id": vid, "title": title, "status": "processing",
                      "uploaded_at": dt.datetime.now().isoformat(timespec="seconds")}
        _save_json(STATE_PATH, state)
        print(f"  → ID {vid}")

    # YouTube 側の処理完了を待ち、問題なければサイト用の ID を記入
    pending = [u for u in todo if state.get(u, {}).get("status") == "processing"]
    deadline = time.time() + args.wait_min * 60
    while pending:
        for uid in list(pending):
            vid = state[uid]["id"]
            status, msg = check(yt, vid)
            if status == "processing":
                continue
            pending.remove(uid)
            state[uid].update(status="published" if status == "ok" else status,
                              message=msg)
            _save_json(STATE_PATH, state)
            if status == "ok":
                write_id(uid, vid)
                print(f"  ✓ {uid}: {msg} → youtube_ids.json に記入 (ID {vid})")
                if not args.keep_files:
                    remove_intermediate(uid)
            else:
                failed.append((uid, msg))
        if not pending or time.time() > deadline:
            break
        print(f"  処理待ち {len(pending)} 試合 — {args.poll_sec:.0f} 秒後に再確認", flush=True)
        time.sleep(args.poll_sec)

    for uid, msg in failed:
        print(f"  ✗ {uid}: {msg}")
    for uid in pending:
        print(f"  … {uid}: まだ処理中。次回の実行で続きから確認します")
    if failed:
        return 1
    return 2 if pending else 0


if __name__ == "__main__":
    sys.exit(main())
