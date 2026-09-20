"""One-time import of the legacy Apify export into the new raw schema.

    python tools/import_legacy.py legacy/data/dataset_padel-ocupare_*.json
    python tools/import_legacy.py --all-clubs ...      # also clubs not in config/clubs.yaml
    python tools/import_legacy.py --club PadelPoint ... # only the named club(s), by `club` name
    python tools/import_legacy.py --dry-run ...

Mapping (nothing is guessed; fields the old system never had stay empty):
  snapshot_ts  <- colectat_la, converted to Europe/Chisinau
  club, court  <- club, teren
  slot_date    <- data;  slot_start <- ora;  slot_end <- ora + durata_ore
  status       <- free   if ocupat == 0
                  booked if ocupat == 1 and viitor == 1
                  past   if ocupat == 1 and viitor == 0
  raw_status   <- "ocupat=<value>" (the literal value the export carries)
  price, booking_type <- empty (never exposed)
  source       <- apify_legacy
Rows with status != OK (sentinel rows without a slot) are skipped and counted.
Refuses to import a club twice: stops if data/raw already contains apify_legacy
rows for any club about to be imported (other clubs can be added later).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from collector.config import load_config  # noqa: E402
from collector.run import dedup_key  # noqa: E402
from collector.storage import RAW_COLUMNS, RAW_DIR, append_rows, read_all_rows  # noqa: E402

SOURCE = "apify_legacy"


def map_row(item: dict, tz: zoneinfo.ZoneInfo) -> dict | None:
    if item.get("status") != "OK" or not item.get("ora") or not item.get("teren"):
        return None
    snap = dt.datetime.fromisoformat(str(item["colectat_la"]).replace("Z", "+00:00")).astimezone(tz)
    start = dt.time.fromisoformat(item["ora"])
    minutes = round(float(item["durata_ore"]) * 60)
    end = (dt.datetime.combine(dt.date.fromisoformat(item["data"]), start) + dt.timedelta(minutes=minutes)).time()
    ocupat, viitor = item.get("ocupat"), item.get("viitor")
    if ocupat == 0:
        status = "free"
    elif ocupat == 1 and viitor == 1:
        status = "booked"
    elif ocupat == 1 and viitor == 0:
        status = "past"
    else:
        raise ValueError(f"unmappable ocupat/viitor pair {ocupat!r}/{viitor!r} in {item}")
    row = {c: "" for c in RAW_COLUMNS}
    row.update({
        "snapshot_ts": snap.isoformat(timespec="seconds"),
        "club": item["club"], "court": item["teren"], "slot_date": item["data"],
        "slot_start": start.strftime("%H:%M"), "slot_end": end.strftime("%H:%M"),
        "status": status, "raw_status": f"ocupat={ocupat}", "source": SOURCE,
    })
    return row


def convert(items: list[dict], tz: zoneinfo.ZoneInfo, keep_clubs: set[str] | None) -> tuple[list[dict], dict]:
    rows, seen = [], set()
    stats = {"input": len(items), "skipped_not_ok": 0, "skipped_other_club": 0, "duplicates": 0}
    for item in items:
        row = map_row(item, tz)
        if row is None:
            stats["skipped_not_ok"] += 1
            continue
        if keep_clubs is not None and row["club"] not in keep_clubs:
            stats["skipped_other_club"] += 1
            continue
        key = dedup_key(row)
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)
        rows.append(row)
    stats["mapped"] = len(rows)
    return rows, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", nargs="+", help="legacy padel-ocupare JSON export(s)")
    ap.add_argument("--raw-dir", default=str(RAW_DIR))
    ap.add_argument("--all-clubs", action="store_true", help="import clubs that are not in config/clubs.yaml too")
    ap.add_argument("--club", action="append", help="import only these club names (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    tz = zoneinfo.ZoneInfo(cfg.timezone)
    keep = None if args.all_clubs else {c.name for c in cfg.clubs}
    if args.club:
        keep = set(args.club)
    raw_dir = Path(args.raw_dir)

    items = []
    for path in args.export:
        items.extend(json.loads(Path(path).read_text(encoding="utf-8")))
    rows, stats = convert(items, tz, keep)
    already = {r["club"] for r in read_all_rows(raw_dir) if r["source"] == SOURCE} if raw_dir.exists() else set()
    already &= {r["club"] for r in rows}
    if already:
        print(f"refusing: {raw_dir} already contains {SOURCE} rows for {sorted(already)}", file=sys.stderr)
        return 1
    print(" ".join(f"{k}={v}" for k, v in stats.items()), file=sys.stderr)
    if not rows:
        print("nothing to import", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry-run: would append {len(rows)} rows to {raw_dir}", file=sys.stderr)
        return 0
    for path, n in append_rows(rows, raw_dir).items():
        print(f"appended {n} rows to {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
