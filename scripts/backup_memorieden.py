#!/usr/bin/env python3
"""Back up MemorieDen SQLite DBs with retention.

Creates backups using sqlite3's online backup API (via Python stdlib), so it is safe with WAL.

Retention policy (per Coriana):
- daily: keep last 7 days
- sunday: keep for ~1 month (31 days)
- last-day-of-month: keep for 1 year (365 days)

Backups are stored under BASE_DIR with subfolders:
- daily/YYYY-MM-DD/
- weekly/YYYY-MM-DD/            (only Sundays)
- monthly/YYYY-MM-DD/           (only last day of month)

Each folder contains:
- memorieden_v1.sqlite3
- memorieden_v2.sqlite3
- manifest.json

Usage:
  python3 backup_memorieden.py
  python3 backup_memorieden.py --base-dir /mnt/m2/memorieden_backups
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_BASE_DIR = "/mnt/m2/memorieden_backups"
V1_DB = "/mnt/m2/memorieden/memorieden.sqlite3"
V2_DB = "/mnt/m2/memorieden_v2/mem0_local.db"


def is_last_day_of_month(d: dt.date) -> bool:
    return (d + dt.timedelta(days=1)).month != d.month


def sqlite_backup(src_path: str, dst_path: str) -> dict:
    src = Path(src_path)
    dst = Path(dst_path)
    if not src.exists():
        raise FileNotFoundError(src_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Open src as read-only. The backup() API will handle WAL properly.
    src_uri = f"file:{src.as_posix()}?mode=ro"
    src_conn = sqlite3.connect(src_uri, uri=True)
    try:
        dst_conn = sqlite3.connect(dst.as_posix())
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    st = dst.stat()
    return {"path": str(dst), "bytes": st.st_size}


def delete_older_than(folder: Path, max_age_days: int) -> int:
    if not folder.exists():
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    deleted = 0
    for child in folder.iterdir():
        if not child.is_dir():
            continue
        # Use directory mtime as the backup timestamp.
        age_days = (now.timestamp() - child.stat().st_mtime) / 86400.0
        if age_days > max_age_days:
            # remove directory tree
            for p in sorted(child.rglob("*"), reverse=True):
                try:
                    if p.is_file() or p.is_symlink():
                        p.unlink()
                    elif p.is_dir():
                        p.rmdir()
                except FileNotFoundError:
                    pass
            try:
                child.rmdir()
            except OSError:
                pass
            deleted += 1
    return deleted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", default=DEFAULT_BASE_DIR)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base = Path(args.base_dir)
    today = dt.datetime.now().date()  # local date is fine for retention labels
    stamp = today.strftime("%Y-%m-%d")

    daily_dir = base / "daily" / stamp
    weekly_dir = base / "weekly" / stamp
    monthly_dir = base / "monthly" / stamp

    targets = [(daily_dir, "daily")]
    if today.weekday() == 6:  # Sunday
        targets.append((weekly_dir, "sunday"))
    if is_last_day_of_month(today):
        targets.append((monthly_dir, "month_end"))

    report = {
        "date": stamp,
        "targets": [t[1] for t in targets],
        "dbs": {},
        "retention": {},
    }

    if args.dry_run:
        print(json.dumps({"dry_run": True, **report}, indent=2))
        return

    for out_dir, kind in targets:
        out_dir.mkdir(parents=True, exist_ok=True)

        v1_out = out_dir / "memorieden_v1.sqlite3"
        v2_out = out_dir / "memorieden_v2.sqlite3"

        report["dbs"].setdefault(kind, {})
        report["dbs"][kind]["v1"] = sqlite_backup(V1_DB, str(v1_out))
        report["dbs"][kind]["v2"] = sqlite_backup(V2_DB, str(v2_out))

        manifest = {
            "kind": kind,
            "date": stamp,
            "sources": {"v1": V1_DB, "v2": V2_DB},
            "outputs": {"v1": str(v1_out), "v2": str(v2_out)},
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Retention cleanup
    report["retention"]["daily_deleted"] = delete_older_than(base / "daily", 7)
    report["retention"]["weekly_deleted"] = delete_older_than(base / "weekly", 31)
    report["retention"]["monthly_deleted"] = delete_older_than(base / "monthly", 365)

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
