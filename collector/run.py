"""Collect today's and tomorrow's grid for every configured club.

    python -m collector.run --dry-run            # print what would be written
    python -m collector.run --save-html DIR      # also keep the fetched pages

Exit codes: 0 = success, 1 = nothing collected/written because of an error.
Any failure (blocked, zero slots, unexpected shape, over the time budget)
happens BEFORE anything is written, so a failed run leaves no partial rows.
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
from .parse import ParseError, Slot, parse_grid

RAW_COLUMNS = ["snapshot_ts", "club", "court", "slot_date", "slot_start", "slot_end",
               "status", "raw_status", "price", "booking_type", "source"]
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


def collect(clubs: list[Club], dates: list[dt.date], now: dt.datetime, save_html: Path | None = None,
            log=print, budget_s: float = RUN_BUDGET_S) -> list[dict]:
    """Fetch and parse every club/date. Returns rows, deduplicated in memory. Raises on any problem."""
    snapshot_ts = now.isoformat(timespec="seconds")
    started = time.monotonic()
    throttle = Throttle(1.0)
    browser = Browser()
    rows: list[dict] = []
    seen: set[tuple] = set()
    try:
        for club in clubs:
            for slot_date in dates:
                elapsed = time.monotonic() - started
                if elapsed > budget_s:
                    raise CollectError(f"run budget of {budget_s:.0f}s exceeded after {elapsed:.0f}s; aborting before {club.slug} {slot_date}")
                url = grid_url(club, slot_date)
                html, source = fetch_grid(club, url, throttle, browser, log=log)
                if save_html is not None:
                    out = save_html / club.slug
                    out.mkdir(parents=True, exist_ok=True)
                    (out / f"{slot_date.isoformat()}_{source}.html").write_text(html, encoding="utf-8")
                slots = parse_grid(html, club, slot_date, now)
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
    if not rows:
        raise CollectError("zero rows collected")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--dry-run", action="store_true", help="print rows, write nothing")
    ap.add_argument("--save-html", metavar="DIR", help="save every fetched page under DIR/<club>/<date>_<source>.html")
    ap.add_argument("--club", action="append", help="restrict to these club slugs (repeatable)")
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
    dates = [now.date(), now.date() + dt.timedelta(days=1)]

    log = lambda msg: print(msg, file=sys.stderr)  # progress goes to stderr, data to stdout
    try:
        rows = collect(clubs, dates, now, save_html=Path(args.save_html) if args.save_html else None, log=log)
    except (FetchError, ParseError, CollectError) as exc:
        print(f"RUN FAILED, nothing written: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\t".join(RAW_COLUMNS))
        for row in rows:
            print("\t".join(row[c] for c in RAW_COLUMNS))
        print(f"dry-run: {len(rows)} rows, nothing written", file=sys.stderr)
        return 0

    print("error: writing rows is implemented in Phase 2; use --dry-run", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
