"""Fail when data/raw has gone stale, so a silent stop becomes a failed run.

Collection can stop without any error: GitHub's `schedule` trigger is
best-effort, and an external dispatcher can be down or its token expired. In
September 2026 that produced a six-day hole nobody noticed.

A check inside the collect workflow cannot detect "nothing ran at all" - if no
run starts, no check runs either. But GitHub's own cron, unreliable as its
cadence is, still fires several times a day. Running this on every scheduled
run therefore turns prolonged silence into a non-zero exit, which GitHub
reports by email.

The check is per club, not only for the newest row anywhere. A run that keeps
three clubs and loses the fourth still writes rows, so the newest snapshot stays
young and a club-wide hole hides behind it: that is exactly what happened when
padelpoint.md changed its buttons on 2026-10-10 and this script kept printing
"fresh" while the biggest club was missing from every snapshot of the day.

A second, different kind of hole: rows keep arriving but they stop meaning
anything. A booking grid that gives back bookings it had already shown leaves a
club looking empty instead of looking broken - Divi's Courtica page did this
from 2026-10-12 and closed 2026-10-13 with 0 of 64 slots booked. The derived
table marks those days (grid_released, see collector/derive.py) and the
dashboard drops them, which is correct but also invisible: the club just stops
moving. So this script fails while such a day is recent, which costs an email
per scheduled run until the cause is found or the check is turned off with
--release-lookback-days 0.

Exit codes: 0 = data is fresh or no check is due, 1 = stale, missing or released.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

import yaml

CONFIG = os.path.join(os.path.dirname(__file__), os.pardir, "config", "clubs.yaml")


def load_config(path: str) -> tuple[ZoneInfo, dt.time, dt.time, list[str]]:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    tz = ZoneInfo(cfg["timezone"])
    start_s, end_s = cfg["collection_window"].split("-")
    start = dt.time.fromisoformat(start_s.strip())
    end = dt.time.fromisoformat(end_s.strip())
    # The `club` column holds the name, which is what the raw rows can be matched on.
    clubs = [str(c["name"]) for c in cfg.get("clubs") or []]
    return tz, start, end, clubs


def newest_by_club(raw_dir: str) -> dict[str, dt.datetime]:
    """Latest snapshot_ts per club across the raw CSVs ("" keyed rows are skipped)."""
    newest: dict[str, dt.datetime] = {}
    # The monthly files are named YYYY-MM.csv, so the last two by name cover
    # any month boundary.
    for path in sorted(glob.glob(os.path.join(raw_dir, "*.csv")))[-2:]:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                raw = (row.get("snapshot_ts") or "").strip()
                club = (row.get("club") or "").strip()
                if not raw or not club:
                    continue
                try:
                    ts = dt.datetime.fromisoformat(raw)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    continue
                if club not in newest or ts > newest[club]:
                    newest[club] = ts
    return newest


def newest_snapshot(raw_dir: str) -> dt.datetime | None:
    """Latest snapshot_ts across every club, or None when there is none."""
    per_club = newest_by_club(raw_dir)
    return max(per_club.values()) if per_club else None


def stale_clubs(per_club: dict[str, dt.datetime], clubs: list[str], reference: dt.datetime,
                max_age_h: float) -> list[tuple[str, float | None]]:
    """Configured clubs left behind the rest, as (club, hours behind, or None for no rows).

    `reference` is the newest snapshot of ANY club, not the wall clock: the
    question is whether a club is missing from runs that are collecting the
    others, which is true at any hour and does not depend on the collection
    window. Outside the window every club is equally old and nothing is flagged.
    """
    out = []
    for club in clubs:
        ts = per_club.get(club)
        if ts is None:
            out.append((club, None))
            continue
        behind_h = (reference - ts).total_seconds() / 3600
        if behind_h > max_age_h:
            out.append((club, behind_h))
    return out


def released_days(daily: list[dict], today: dt.date, lookback_days: int) -> list[tuple[str, str, str]]:
    """Recent club-days whose grid gave back the bookings it had shown, newest first.

    `daily` is the daily_occupancy table. Today is included: the grid emptying
    out is visible long before the day is over, and waiting a day only delays
    the alarm.
    """
    out = []
    for row in daily:
        if not row.get("grid_released"):
            continue
        age = (today - dt.date.fromisoformat(row["date"])).days
        if 0 <= age <= lookback_days:
            out.append((row["club"], row["date"], row.get("released_pct", "")))
    return sorted(out, key=lambda t: t[1], reverse=True)


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
    ap.add_argument(
        "--release-lookback-days",
        type=int,
        default=3,
        help="fail when a day this recent had its bookings released (0 disables the check)",
    )
    args = ap.parse_args()

    tz, win_start, win_end, clubs = load_config(args.config)
    now = dt.datetime.now(tz)

    per_club = newest_by_club(args.raw_dir)
    newest = max(per_club.values()) if per_club else None

    # A club left behind by runs that are collecting the others is a hole of its own,
    # and comparing clubs with each other works at any hour, so this runs before the
    # window guard below.
    if newest is not None:
        stale = stale_clubs(per_club, clubs, newest, args.max_age_hours)
        if stale:
            detail = "\n".join(
                f"  - {club}: no rows at all" if behind is None else f"  - {club}: {behind:.1f} h behind"
                for club, behind in stale
            )
            print(
                f"ERROR: {len(stale)} of {len(clubs)} clubs have no row within "
                f"{args.max_age_hours:.1f} h of the newest snapshot "
                f"({newest:%Y-%m-%d %H:%M %Z}):\n{detail}\n"
                "The other clubs are being collected, so this is that club's own problem:\n"
                "  1. recent runs of collect.yml - a club/date failure is reported as PARTIAL RUN\n"
                "  2. whether its page still parses: python -m collector.run --dry-run --club <slug>\n"
                "  3. whether the club is still in config/clubs.yaml under this exact name",
                file=sys.stderr,
            )
            return 1

    if args.release_lookback_days > 0:
        # Imported here so a stale-data failure is still reported if deriving breaks.
        # Like the comparison above, this does not depend on the wall clock, so it
        # runs before the window guard: a grid that gives bookings back is just as
        # visible at 02:00 as at noon.
        from collector.derive import build_tables

        _, daily, _ = build_tables(pathlib.Path(args.raw_dir), str(tz))
        released = released_days(daily, now.date(), args.release_lookback_days)
        if released:
            detail = "\n".join(f"  - {club} on {date}: {pct}% of the previous evening's booked hours went free"
                               for club, date, pct in released)
            print(
                f"ERROR: {len(released)} club-day(s) in the last {args.release_lookback_days} days "
                f"had their bookings released by the site:\n{detail}\n"
                "Those days are dropped from the dashboard, so the club looks frozen rather than broken.\n"
                "Check, in this order:\n"
                "  1. open the club's booking page and compare it with data/raw for the same day\n"
                "  2. whether the club moved its bookings to another system (then the page is no longer evidence)\n"
                "  3. collector/derive.py RELEASE_* if normal churn has simply grown past the threshold\n"
                "Silence it meanwhile with --release-lookback-days 0.",
                file=sys.stderr,
            )
            return 1

    # The absolute age, unlike the comparison above, is only meaningful once the
    # window has been open for max_age hours today. Earlier than that the newest
    # snapshot is legitimately from last night; after window_end nothing is expected.
    earliest_check = dt.datetime.combine(now.date(), win_start, tzinfo=tz) + dt.timedelta(
        hours=args.max_age_hours
    )
    window_close = dt.datetime.combine(now.date(), win_end, tzinfo=tz)
    if now < earliest_check or now > window_close:
        print(
            f"no age check due at {now:%Y-%m-%d %H:%M %Z} "
            f"(next check from {earliest_check:%Y-%m-%d %H:%M}, "
            f"window closes {window_close:%H:%M} local); every club is up to date"
        )
        return 0

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

    behind = ", ".join(f"{club} {(newest - per_club[club]).total_seconds() / 3600:.1f} h behind"
                       for club in clubs if club in per_club)
    print(f"fresh: newest snapshot {newest:%Y-%m-%d %H:%M %Z}, {age_h:.1f} h old; per club: {behind}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
