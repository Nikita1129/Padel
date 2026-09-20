"""Compare legacy (apify_legacy) and new observations where they overlap.

    python tools/parity_check.py [--raw-dir data/raw]

For every date that has both legacy and new rows for a club, the final state
of each club/court/slot (last observation before the slot started) is
computed from each source separately and compared. Mismatches are printed one
per line; exit code 1 if any, 0 otherwise. Slots present in only one source
are listed separately as coverage gaps, not as mismatches.
"""
from __future__ import annotations

import argparse
import sys
import zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from collector.config import load_config  # noqa: E402
from collector.derive import slots_final  # noqa: E402
from collector.storage import RAW_DIR, read_all_rows  # noqa: E402

LEGACY = "apify_legacy"


def compare(rows: list[dict], tz: zoneinfo.ZoneInfo) -> dict:
    old = slots_final([r for r in rows if r["source"] == LEGACY], tz)
    new = slots_final([r for r in rows if r["source"] != LEGACY], tz)
    key = lambda r: (r["club"], r["court"], r["slot_date"], r["slot_start"])
    old_by, new_by = {key(r): r for r in old}, {key(r): r for r in new}
    overlap_dates = {(r["club"], r["slot_date"]) for r in old} & {(r["club"], r["slot_date"]) for r in new}
    mismatches, only_old, only_new, compared = [], [], [], 0
    for k in sorted(set(old_by) | set(new_by)):
        if (k[0], k[2]) not in overlap_dates:
            continue
        if k in old_by and k in new_by:
            compared += 1
            if old_by[k]["final_status"] != new_by[k]["final_status"]:
                mismatches.append((k, old_by[k]["final_status"], new_by[k]["final_status"]))
        elif k in old_by:
            only_old.append(k)
        else:
            only_new.append(k)
    return {"overlap_dates": sorted(overlap_dates), "compared": compared,
            "mismatches": mismatches, "only_old": only_old, "only_new": only_new}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", default=str(RAW_DIR))
    args = ap.parse_args(argv)
    cfg = load_config()
    result = compare(read_all_rows(Path(args.raw_dir)), zoneinfo.ZoneInfo(cfg.timezone))
    print(f"overlapping club/dates: {len(result['overlap_dates'])}; slots compared: {result['compared']}; "
          f"mismatches: {len(result['mismatches'])}; only legacy: {len(result['only_old'])}; only new: {len(result['only_new'])}")
    for (club, court, date, start), a, b in result["mismatches"]:
        print(f"MISMATCH {club} | {court} | {date} {start} | legacy={a} new={b}")
    for k in result["only_old"][:50]:
        print(f"only-legacy {k}")
    for k in result["only_new"][:50]:
        print(f"only-new {k}")
    return 1 if result["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
