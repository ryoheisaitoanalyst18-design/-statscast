#!/usr/bin/env python3
"""cleanup_stale_details.py — players/*_detail/ の残骸ファイルを検出・削除する。

PYTHONHASHSEED による姓名順反転で過去のパイプライン実行時に生成された
姓名順違いの残骸JSONが蓄積する問題に対処するスクリプト。

使い方:
  python3 scripts/cleanup_stale_details.py [DATA_DIR]            # dry-run (デフォルト)
  python3 scripts/cleanup_stale_details.py [DATA_DIR] --delete   # 実際に削除する

  DATA_DIR 省略時はリポジトリの data/。パイプライン出力側 (例: ~/statscast/data) を
  指定して掃除することもできる。**リポジトリ側だけ消しても、データ同期が
  パイプライン→リポジトリの --delete ミラーのため次回デプロイで復活する。
  必ずパイプライン側も掃除すること** (docs/OPERATIONS.md 参照)。

安全ガード:
  - 削除前後に validate_data.py <DATA_DIR> --skip-html を実行し、FAILがあれば中断
  - dry-run では何も削除しない

出力:
  - ターミナルに削除対象リストと件数
  - scripts/cleanup_report_YYYY-MM-DD.txt に詳細を保存
"""
import json
import os
import subprocess
import sys
from datetime import date

DETAIL_DIRS = {
    "batter": "batter_detail",
    "pitcher": "pitcher_detail",
}
LEADERBOARD_KEYS = {
    "batter": "batterLeaderboard",
    "pitcher": "pitcherLeaderboard",
}


def unwrap(doc):
    if isinstance(doc, list) and doc and isinstance(doc[0], dict) and "result" in doc[0]:
        return doc[0]["result"]["data"]["json"]
    return doc


def collect_canonical_names(data_dir):
    """全 yearData_*.json のリーダーボードに現れる選手名の和集合を返す。"""
    canonical = set()
    import glob
    for path in glob.glob(os.path.join(data_dir, "yearData_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = unwrap(json.load(f))
        except Exception:
            continue
        for key in LEADERBOARD_KEYS.values():
            for row in d.get(key, []):
                name = row.get("Name")
                if name:
                    canonical.add(name)
    return canonical


def find_stale(players_dir, detail_subdir, canonical):
    """詳細ディレクトリ内でカノニカル名に無いファイルを返す。"""
    detail_dir = os.path.join(players_dir, detail_subdir)
    if not os.path.isdir(detail_dir):
        return []
    stale = []
    for fn in sorted(os.listdir(detail_dir)):
        if not fn.endswith(".json"):
            continue
        name = fn[:-5]  # .json を除いた選手名
        if name not in canonical:
            stale.append(os.path.join(detail_dir, fn))
    return stale


def run_validate(script_dir, data_dir):
    """validate_data.py <data_dir> --skip-html を実行し、終了コードを返す。"""
    validate_script = os.path.join(script_dir, "validate_data.py")
    result = subprocess.run(
        [sys.executable, validate_script, data_dir, "--skip-html"],
        capture_output=True, text=True
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, end="")
    return result.returncode


def main():
    do_delete = "--delete" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if args:
        data_dir = os.path.normpath(os.path.abspath(args[0]))
    else:
        data_dir = os.path.normpath(os.path.join(script_dir, "..", "data"))
    players_dir = os.path.join(data_dir, "players")
    print(f"対象: {data_dir}")

    print(f"{'=== 削除モード ===' if do_delete else '=== DRY-RUN モード (--delete で実削除) ==='}")

    canonical = collect_canonical_names(data_dir)
    print(f"カノニカル選手名: {len(canonical)} 人")

    all_stale = []
    for kind, subdir in DETAIL_DIRS.items():
        stale = find_stale(players_dir, subdir, canonical)
        print(f"\n{kind}_detail/ 残骸: {len(stale)} 件")
        for p in stale:
            print(f"  {os.path.relpath(p, os.path.join(script_dir, '..'))}")
        all_stale.extend(stale)

    report_date = str(date.today())
    report_path = os.path.join(script_dir, f"cleanup_report_{report_date}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"cleanup_stale_details.py 実行日: {report_date}\n")
        f.write(f"カノニカル選手名: {len(canonical)} 人\n")
        f.write(f"残骸ファイル総数: {len(all_stale)} 件\n\n")
        for p in all_stale:
            f.write(os.path.relpath(p, os.path.join(script_dir, "..")) + "\n")
    print(f"\nレポート: {report_path}")

    if not all_stale:
        print("\n残骸ファイルなし。終了。")
        return 0

    if not do_delete:
        print(f"\n合計 {len(all_stale)} 件の残骸が見つかりました。削除するには --delete を付けて実行してください。")
        return 0

    # 削除前の安全ガード
    print("\n--- 削除前の検証 ---")
    if run_validate(script_dir, data_dir) != 0:
        print("✗ 削除前の validate_data.py が FAIL — 削除を中断します")
        return 1

    print(f"\n{len(all_stale)} 件を削除します...")
    for p in all_stale:
        os.remove(p)
        print(f"  削除: {os.path.relpath(p, os.path.join(script_dir, '..'))}")

    # 削除後の安全ガード
    print("\n--- 削除後の検証 ---")
    if run_validate(script_dir, data_dir) != 0:
        print("✗ 削除後の validate_data.py が FAIL — 手動で確認してください")
        return 1

    print(f"\n✅ {len(all_stale)} 件の残骸を削除し、検証も通過しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
