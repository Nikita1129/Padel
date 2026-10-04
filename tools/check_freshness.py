"""Fail when data/raw has gone stale, so a silent stop becomes a failed run.

Collection can stop without any error: GitHub's `schedule` trigger is
best-effort, and an external dispatcher can be down or its token expired. In
September 2026 that produced a six-day hole nobody noticed.

A check inside the collect workflow cannot detect "nothing ran at all" - if no
run starts, no check runs either. But GitHub's own cron, unreliable as its
cadence is, still fires several times a day. Running this on every scheduled
run therefore turns prolonged silence into a non-zero exit, which GitHub
reports by email.

Exit codes: 0 = data is fresh or no check is due, 1 = stale or missing.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import os
import sys
from zoneinfo import ZoneInfo

import yaml

CONFIG = os.path.join(os.path.dirname(__file__), os.pardir, "config", "clubs.yaml")


def load_config(path: str) -> tuple[ZoneInfo, dt.time, dt.time]:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    tz = ZoneInfo(cfg["timezone"])
    start_s, end_s = cfg["collection_window"].split("-")
    start = dt.time.fromisoformat(start_s.strip())
    end = dt.time.fromisoformat(end_s.strip())
    return tz, start, end


def newest_snapshot(raw_dir: str) -> dt.datetime | None:
    """Latest snapshot_ts across the raw CSVs, or None when there is none."""
    newest: dt.datetime | None = None
    # The monthly files are named YYYY-MM.csv, so the last two by name cover
    # any month boundary.
    for path in sorted(glob.glob(os.path.join(raw_dir, "*.csv")))[-2:]:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                raw = (row.get("snapshot_ts") or "").strip()
                if not raw:
                    continue
                try:
                    ts = dt.datetime.fromisoformat(raw)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    continue
                if newest is None or ts > newest:
                    newest = ts
    return newest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-age-hours",
        type=float,
        default=6.0,
        help="fail when the newest snapshot is older than this (default 6)",
    )
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--config", default=CONFIG)
    args = ap.parse_args()

    tz, win_start, win_end = load_config(args.config)
    now = dt.datetime.now(tz)

    # Only check once the window has been open for max_age hours today.
    # Earlier than that, the newest snapshot is legitimately from last night and
    # would look stale; later than window_end, nothing is expected either.
    earliest_check = dt.datetime.combine(now.date(), win_start, tzinfo=tz) + dt.timedelta(
        hours=args.max_age_hours
    )
    window_close = dt.datetime.combine(now.date(), win_end, tzinfo=tz)
    if now < earliest_check or now > window_close:
        print(
            f"no check due at {now:%Y-%m-%d %H:%M %Z} "
            f"(next check from {earliest_check:%Y-%m-%d %H:%M}, "
            f"window closes {window_close:%H:%M} local)"
        )
        return 0

    newest = newest_snapshot(args.raw_dir)
    if newest is None:
        print(f"ERROR: no snapshot_ts found in {args.raw_dir}", file=sys.stderr)
        return 1

    age_h = (now - newest).total_seconds() / 3600
    if age_h > args.max_age_hours:
        print(
            f"ERROR: newest snapshot is {age_h:.1f} h old "
            f"({newest:%Y-%m-%d %H:%M %Z}), limit is {args.max_age_hours:.1f} h.\n"
            "Collection has stopped. Check, in this order:\n"
            "  1. the external cron job (see docs/cron-independent.md) - expired token?\n"
            "  2. recent runs of collect.yml for failures\n"
            "  3. whether the club pages still parse (run with --dry-run locally)",
            file=sys.stderr,
        )
        return 1

    print(f"fresh: newest snapshot {newest:%Y-%m-%d %H:%M %Z}, {age_h:.1f} h old")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
