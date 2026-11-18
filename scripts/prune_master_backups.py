#!/usr/bin/env python3
"""マスター CSV の自動バックアップを間引く (取り込みのたびに 1 本 150MB 増え、2026-11 に 57 本・約 9GB になっていた)。

対象は update_and_deploy.sh が作る自動バックアップ `trackman_data.backup_YYYYMMDD_HHMMSS.csv` だけ。
手で名前を付けたバックアップ (…_pre_bulk・…bak_… など) には触らない。

残すもの: 新しい順に KEEP_LATEST 本 + 月ごとに一番古い 1 本 (月初の状態を遡れるように)。

使い方:
  python3 scripts/prune_master_backups.py            # 消す候補を表示するだけ
  python3 scripts/prune_master_backups.py --delete   # 実際に消す
"""
import argparse
import os
import re
import sys

BACKUP_DIR = os.path.expanduser(os.environ.get("MASTER_BACKUP_DIR", "~/ubuntu_data"))
PATTERN = re.compile(r"^trackman_data\.backup_(\d{8})_(\d{6})\.csv$")
KEEP_LATEST = 10


def plan(names: list[str], keep_latest: int = KEEP_LATEST) -> tuple[list[str], list[str]]:
    """(残す, 消す) を返す。名前の日時で並べる (ファイルの更新日時はコピーで変わりうるので使わない)。"""
    auto = sorted((n for n in names if PATTERN.match(n)), key=lambda n: PATTERN.match(n).groups())
    keep = set(auto[-keep_latest:]) if keep_latest > 0 else set()
    first_of_month: dict[str, str] = {}
    for n in auto:
        first_of_month.setdefault(PATTERN.match(n).group(1)[:6], n)
    keep |= set(first_of_month.values())
    return [n for n in auto if n in keep], [n for n in auto if n not in keep]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delete", action="store_true", help="実際に消す (既定は表示だけ)")
    ap.add_argument("--keep-latest", type=int, default=KEEP_LATEST)
    args = ap.parse_args()
    if not os.path.isdir(BACKUP_DIR):
        print(f"バックアップの置き場がない: {BACKUP_DIR}")
        return 0
    keep, drop = plan(os.listdir(BACKUP_DIR), args.keep_latest)
    size = sum(os.path.getsize(os.path.join(BACKUP_DIR, n)) for n in drop)
    print(f"自動バックアップ {len(keep) + len(drop)} 本 → 残す {len(keep)} 本 / 消す {len(drop)} 本 ({size / 1e9:.1f} GB)")
    for n in drop:
        if args.delete:
            os.remove(os.path.join(BACKUP_DIR, n))
        else:
            print(f"  消す候補: {n}")
    if drop and not args.delete:
        print("(表示だけ。消すときは --delete)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
