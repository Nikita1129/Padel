"""Collect today's and tomorrow's grid for every configured club.

    python -m collector.run                      # append to data/raw/, rebuild tabs, push to Sheets
    python -m collector.run --dry-run            # print what would be written
    python -m collector.run --no-push            # append to data/raw/ only
    python -m collector.run --save-html DIR      # also keep the fetched pages

Exit codes: 0 = success, 1 = something failed. A club/date that fails (blocked,
unexpected shape, slow page) does NOT discard the clubs that succeeded: whatever
was collected is written, the failure is reported, and the run exits 1. Only a
run that collected nothing at all writes nothing. If the Sheets push fails AFTER
the raw rows were appended, the exit code is 1 but the rows are kept: rerun
`python -m collector.derive --push` once the cause is fixed.

A snapshot may therefore be missing a club. That is on purpose: one unreachable
club used to cost the snapshot for all four.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
import zoneinfo
from pathlib import Path

from .config import DEFAULT_CONFIG, Club, load_config
from .fetch import Browser, FetchError, Throttle, fetch_grid, grid_url
from .padelpoint import collect_padelpoint
from .parse import ParseError, Slot, parse_grid
from .storage import RAW_COLUMNS, RAW_DIR, StorageError, append_rows

RUN_BUDGET_S = 60.0


class CollectError(RuntimeError):
    pass


def row_from_slot(snapshot_ts: str, slot: Slot, source: str) -> dict:
    return {
        "snapshot_ts": snapshot_ts,
        "club": slot.club,
        "court": slot.court,
        "slot_date": slot.slot_date.isoformat(),
        "slot_start": slot.slot_start.strftime("%H:%M"),
        "slot_end": slot.slot_end.strftime("%H:%M"),
        "status": slot.status,
        "raw_status": slot.raw_status,
        "price": slot.price,
        "booking_type": slot.booking_type,
        "source": source,
    }


def dedup_key(row: dict) -> tuple:
    return (row["snapshot_ts"], row["club"], row["court"], row["slot_date"], row["slot_start"])


def collect_one(club: Club, slot_date: dt.date, now: dt.datetime, throttle: Throttle, browser: Browser,
                save_html: Path | None, log) -> tuple[list[Slot], str]:
    """Fetch and parse a single club/date. Raises on any problem with that one club."""
    if club.platform == "padelpoint":
        throttle.wait()
        slots = collect_padelpoint(club, slot_date, now, browser,
                                   save_dir=(save_html / club.slug) if save_html else None, log=log)
        return slots, "browser"
    url = grid_url(club, slot_date)
    html, source = fetch_grid(club, url, throttle, browser, log=log)
    if save_html is not None:
        out = save_html / club.slug
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{slot_date.isoformat()}_{source}.html").write_text(html, encoding="utf-8")
    return parse_grid(html, club, slot_date, now), source


def collect(clubs: list[Club], dates: list[dt.date], now: dt.datetime, save_html: Path | None = None,
            log=print, budget_s: float = RUN_BUDGET_S) -> tuple[list[dict], list[str]]:
    """Fetch and parse every club/date.

    Returns (rows, failures). A club/date that raises is recorded in `failures` and
    the rest are still collected, so one unreachable site costs one club rather than
    the whole snapshot. Raises CollectError only when nothing at all was collected.
    """
    snapshot_ts = now.isoformat(timespec="seconds")
    started = time.monotonic()
    throttle = Throttle(1.0)
    browser = Browser()
    rows: list[dict] = []
    failures: list[str] = []
    seen: set[tuple] = set()
    try:
        for club in clubs:
            for slot_date in dates:
                elapsed = time.monotonic() - started
                if elapsed > budget_s:
                    failures.append(f"{club.slug} {slot_date}: run budget of {budget_s:.0f}s "
                                    f"exceeded after {elapsed:.0f}s; stopped here")
                    log(f"FAILED: {failures[-1]}")
                    return _finish(rows, failures)
                try:
                    slots, source = collect_one(club, slot_date, now, throttle, browser, save_html, log)
                except Exception as exc:
                    # Deliberately broad: whatever one club does, the others still get written.
                    # The failure is reported and makes the run exit 1, so it is never silent.
                    failures.append(f"{club.slug} {slot_date}: {exc.__class__.__name__}: {exc}")
                    log(f"FAILED: {failures[-1]}")
                    continue
                n_new = 0
                for slot in slots:
                    row = row_from_slot(snapshot_ts, slot, source)
                    key = dedup_key(row)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(row)
                    n_new += 1
                counts = {}
                for s in slots:
                    counts[s.status] = counts.get(s.status, 0) + 1
                log(f"[{club.slug}] {slot_date} via {source}: {n_new} slots, "
                    f"{len({s.court for s in slots})} courts, {counts}")
    finally:
        browser.close()
    return _finish(rows, failures)


def _finish(rows: list[dict], failures: list[str]) -> tuple[list[dict], list[str]]:
    """Nothing collected is still a hard failure: never write an empty snapshot."""
    if not rows:
        detail = f": {'; '.join(failures)}" if failures else ""
        raise CollectError(f"zero rows collected{detail}")
    return rows, failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--dry-run", action="store_true", help="print rows, write nothing")
    ap.add_argument("--save-html", metavar="DIR", help="save every fetched page under DIR/<club>/<date>_<source>.html")
    ap.add_argument("--club", action="append", help="restrict to these club slugs (repeatable)")
    ap.add_argument("--no-push", action="store_true", help="append raw rows but do not touch the Google Sheet")
    ap.add_argument("--respect-window", action="store_true",
                    help="exit 0 without doing anything when the local time is outside collection_window")
    ap.add_argument("--raw-dir", default=str(RAW_DIR))
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    clubs = list(cfg.clubs)
    if args.club:
        clubs = [c for c in clubs if c.slug in set(args.club)]
        if not clubs:
            print(f"error: no club matches {args.club}", file=sys.stderr)
            return 1
    tz = zoneinfo.ZoneInfo(cfg.timezone)
    now = dt.datetime.now(tz)
    if args.respect_window and not (cfg.window_start <= now.time() <= cfg.window_end):
        print(f"skipped: {now.strftime('%H:%M %Z')} is outside the collection window "
              f"{cfg.window_start:%H:%M}-{cfg.window_end:%H:%M}", file=sys.stderr)
        return 0
    dates = [now.date(), now.date() + dt.timedelta(days=1)]

    log = lambda msg: print(msg, file=sys.stderr)  # progress goes to stderr, data to stdout
    try:
        rows, failures = collect(clubs, dates, now, save_html=Path(args.save_html) if args.save_html else None,
                                 log=log, budget_s=cfg.run_budget_seconds)
    except (FetchError, ParseError, CollectError) as exc:
        print(f"RUN FAILED, nothing written: {exc}", file=sys.stderr)
        return 1

    def report_failures() -> None:
        clubs_hit = sorted({f.split(" ", 1)[0] for f in failures})
        print(f"PARTIAL RUN: {len(failures)} club/date failures ({', '.join(clubs_hit)}); "
              f"{len(rows)} rows from the rest were kept", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)

    if args.dry_run:
        print("\t".join(RAW_COLUMNS))
        for row in rows:
            print("\t".join(row[c] for c in RAW_COLUMNS))
        print(f"dry-run: {len(rows)} rows, nothing written", file=sys.stderr)
        if failures:
            report_failures()
            return 1
        return 0

    try:
        written = append_rows(rows, Path(args.raw_dir))
    except StorageError as exc:
        print(f"RUN FAILED, nothing written: {exc}", file=sys.stderr)
        return 1
    for path, n in written.items():
        log(f"appended {n} rows to {path}")
    if args.no_push:
        if failures:
            report_failures()
            return 1
        return 0

    from .derive import build_tables
    from .sheets import SheetsError, push_tables

    try:
        final, daily, dash = build_tables(Path(args.raw_dir), cfg.timezone)
        push_tables(final, daily, dash)
    except Exception as exc:  # raw rows are already safe on disk; report and fail
        print(f"SHEETS PUSH FAILED (raw rows were appended and are kept): {exc!r}", file=sys.stderr)
        if failures:
            report_failures()
        return 1
    log(f"pushed slots_final ({len(final)}), daily_occupancy ({len(daily)}), dashboard ({len(dash)})")
    if failures:
        report_failures()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
