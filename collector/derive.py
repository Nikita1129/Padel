"""Rebuild the derived tables from data/raw/ (idempotent).

slots_final:      one row per club/court/date/slot with the last status observed
                  BEFORE the slot started, and when it was first seen booked.
daily_occupancy:  per club and date: total/booked slots and occupancy %, overall
                  and split by slot start: morning < 12:00, afternoon 12:00-16:59,
                  evening >= 17:00.

    python -m collector.derive            # print table sizes and a preview
    python -m collector.derive --push     # also rewrite both tabs in the Google Sheet
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import zoneinfo
from pathlib import Path

from .storage import RAW_DIR, read_all_rows

SLOTS_FINAL_COLUMNS = ["club", "court", "slot_date", "slot_start", "slot_end",
                       "final_status", "last_observed_at", "first_seen_booked_at", "observations"]
DAILY_COLUMNS = ["club", "date", "total_slots", "booked_slots", "occupancy_pct",
                 "morning_slots", "morning_booked", "morning_pct",
                 "afternoon_slots", "afternoon_booked", "afternoon_pct",
                 "evening_slots", "evening_booked", "evening_pct"]
VALID_FINAL = {"free", "booked", "blocked", "unknown"}


def period(slot_start: str) -> str:
    hour = int(slot_start[:2])
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def slots_final(rows: list[dict], tz: zoneinfo.ZoneInfo) -> list[dict]:
    groups: dict[tuple, list[tuple[dt.datetime, dict]]] = {}
    for row in rows:
        key = (row["club"], row["court"], row["slot_date"], row["slot_start"])
        snap = dt.datetime.fromisoformat(row["snapshot_ts"])
        if snap.tzinfo is None:
            raise ValueError(f"naive snapshot_ts in raw data: {row['snapshot_ts']}")
        start = dt.datetime.combine(dt.date.fromisoformat(row["slot_date"]),
                                    dt.time.fromisoformat(row["slot_start"]), tzinfo=tz)
        if snap >= start:
            continue  # observed after the slot started: not evidence of the final state
        if row["status"] not in VALID_FINAL:
            continue  # a stale/foreign "past" label before start would be a collector bug; ignore, never count
        groups.setdefault(key, []).append((snap, row))

    out = []
    for key in sorted(groups):
        obs = sorted(groups[key], key=lambda t: t[0])
        last_snap, last = obs[-1]
        booked = [s for s, r in obs if r["status"] == "booked"]
        out.append({
            "club": key[0], "court": key[1], "slot_date": key[2], "slot_start": key[3],
            "slot_end": last["slot_end"],
            "final_status": last["status"],
            "last_observed_at": last_snap.isoformat(timespec="seconds"),
            "first_seen_booked_at": min(booked).isoformat(timespec="seconds") if booked else "",
            "observations": str(len(obs)),
        })
    return out


def daily_occupancy(final: list[dict]) -> list[dict]:
    agg: dict[tuple, dict] = {}
    for row in final:
        key = (row["club"], row["slot_date"])
        a = agg.setdefault(key, {p: [0, 0] for p in ("all", "morning", "afternoon", "evening")})
        booked = 1 if row["final_status"] == "booked" else 0
        for p in ("all", period(row["slot_start"])):
            a[p][0] += 1
            a[p][1] += booked

    def pct(total, booked):
        return f"{100 * booked / total:.1f}" if total else ""

    out = []
    for key in sorted(agg):
        a = agg[key]
        row = {"club": key[0], "date": key[1],
               "total_slots": str(a["all"][0]), "booked_slots": str(a["all"][1]),
               "occupancy_pct": pct(*a["all"])}
        for p in ("morning", "afternoon", "evening"):
            row[f"{p}_slots"] = str(a[p][0])
            row[f"{p}_booked"] = str(a[p][1])
            row[f"{p}_pct"] = pct(*a[p])
        out.append(row)
    return out


def build_tables(raw_dir: Path = RAW_DIR, tz_name: str = "Europe/Chisinau") -> tuple[list[dict], list[dict]]:
    rows = read_all_rows(raw_dir)
    final = slots_final(rows, zoneinfo.ZoneInfo(tz_name))
    return final, daily_occupancy(final)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", default=str(RAW_DIR))
    ap.add_argument("--push", action="store_true", help="rewrite the slots_final and daily_occupancy tabs")
    args = ap.parse_args(argv)

    final, daily = build_tables(Path(args.raw_dir))
    print(f"slots_final: {len(final)} rows; daily_occupancy: {len(daily)} rows", file=sys.stderr)
    for row in daily[-10:]:
        print("  " + "  ".join(f"{k}={v}" for k, v in row.items()), file=sys.stderr)
    if not args.push:
        return 0
    from .sheets import push_tables  # gspread/google-auth only needed here

    if not final:
        print("nothing to push: slots_final is empty", file=sys.stderr)
        return 1
    push_tables(final, daily)
    print("pushed both tabs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
