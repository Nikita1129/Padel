"""Rebuild the derived tables from data/raw/ (idempotent).

dashboard:        one row per club: how much it sold over the whole tracked period.
                  Only days that are OVER and were fully observed count, so clubs
                  and days stay comparable; today and tomorrow are still filling up
                  and would drag every club down.

slots_final:      one row per club/court/date/slot with the last status observed
                  BEFORE the slot started, when it was first seen booked, and the
                  status the site showed the day BEFORE the slot (status_prev_day).
daily_occupancy:  per club and date: total/booked slots and occupancy %, overall
                  and split by slot start: morning < 12:00, afternoon 12:00-16:59,
                  evening >= 17:00; plus booked and total court-hours and
                  revenue_estimate_mdl = booked hours x the price of each hour.
                  The price is the one the site showed for that slot, else the
                  one it showed for the same club and hour elsewhere, else the
                  price_per_hour assumption from config; site_priced_pct says how
                  much of it came from the site. Still an ESTIMATE: "booked" on
                  Courtica also covers blocked/training slots.
                  released_hours / released_pct compare the day with the state the
                  site showed the evening before, and grid_released marks a day the
                  site emptied out (see RELEASE_* below); the dashboard drops those.

    python -m collector.derive            # print table sizes and a preview
    python -m collector.derive --push     # also rewrite both tabs in the Google Sheet
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import zoneinfo
from pathlib import Path

from .config import DEFAULT_CONFIG, load_config
from .storage import RAW_DIR, read_all_rows

SLOTS_FINAL_COLUMNS = ["club", "court", "slot_date", "slot_start", "slot_end",
                       "final_status", "last_observed_at", "first_seen_booked_at", "observations",
                       "price", "status_prev_day"]
DAILY_COLUMNS = ["club", "date", "total_slots", "booked_slots", "occupancy_pct",
                 "morning_slots", "morning_booked", "morning_pct",
                 "afternoon_slots", "afternoon_booked", "afternoon_pct",
                 "evening_slots", "evening_booked", "evening_pct",
                 # The new columns are appended, not inserted: a formula or chart built
                 # on the sheet keeps pointing at the same column it did before.
                 "booked_hours", "price_per_hour_assumed", "revenue_estimate_mdl",
                 "total_hours", "price_per_hour_effective", "site_priced_pct",
                 "booked_hours_prev_day", "released_hours", "released_pct", "grid_released"]
DASHBOARD_COLUMNS = ["club", "terenuri", "prima_zi", "ultima_zi", "zile_complete",
                     "ore_rezervate", "ore_pe_zi", "ore_pe_teren_pe_zi",
                     "ocupare_pct", "ocupare_seara_pct",
                     "pret_ora_presupus", "venit_estimat_mdl", "venit_estimat_pe_zi_mdl",
                     "pret_ora_efectiv", "zile_incomplete", "zile_eliberate"]
VALID_FINAL = {"free", "booked", "blocked", "unknown"}

# A booking grid that drops bookings it had already shown stops being evidence of
# what was sold. Divi's Courtica page started doing this on 2026-10-12: slots that
# had been booked for a day went free during the morning and stayed free, so
# 2026-10-13 closed with 0 of 64 slots booked at a club that runs near 60%.
# A day is called released when MOST of what the site showed as booked the evening
# before was gone by the time the slots started. Both bounds matter: churn of
# 10-30% of booked hours is normal at every club (measured over 99 club-days), and
# a club with only a couple of booked hours flips ratios on a single cancellation.
# "Survived" means still booked at the start: a slot the grid later marks blocked
# is as unknowable as one it marks free, so it counts as released too.
RELEASE_MIN_HOURS = 5.0   # ignore days too small to judge
RELEASE_KEPT_MIN = 0.5    # flag when less than half of last evening's hours survive
# Window used to decide what a full day looks like for a club (see dashboard()).
COMPLETE_WINDOW_DAYS = 7


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
        slot_day = dt.date.fromisoformat(row["slot_date"])
        start = dt.datetime.combine(slot_day, dt.time.fromisoformat(row["slot_start"]), tzinfo=tz)
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
        # What the site said while the slot was still a day away. Compared with
        # final_status this shows a grid that gives bookings back (see RELEASE_*).
        midnight = dt.datetime.combine(dt.date.fromisoformat(key[2]), dt.time(0, 0), tzinfo=tz)
        before = [r for s, r in obs if s < midnight]
        # The page prices only what it still sells, so a slot that ends up booked
        # carries the price from the last observation that still offered it.
        priced = [r.get("price", "").strip() for s, r in obs if (r.get("price") or "").strip()]
        out.append({
            "club": key[0], "court": key[1], "slot_date": key[2], "slot_start": key[3],
            "slot_end": last["slot_end"],
            "final_status": last["status"],
            "last_observed_at": last_snap.isoformat(timespec="seconds"),
            "first_seen_booked_at": min(booked).isoformat(timespec="seconds") if booked else "",
            "observations": str(len(obs)),
            "price": priced[-1] if priced else "",
            "status_prev_day": before[-1]["status"] if before else "",
        })
    return out


def slot_hours(row: dict) -> float:
    start = dt.time.fromisoformat(row["slot_start"])
    end = dt.time.fromisoformat(row["slot_end"])
    minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    if minutes <= 0:  # slot ending at 00:00
        minutes += 24 * 60
    return minutes / 60


def site_prices(rows: list[dict]) -> dict[tuple[str, str], float]:
    """Price the site showed per club and slot start, from the raw rows.

    A booking page prices only what it still sells, so a slot that was already
    taken at every observation never shows its own price - but the same hour on
    another court or another day usually does. The most common value wins; a tie
    goes to the higher price, so the estimate never quietly rounds revenue down.

    The table spans the whole history, so a lasting price change takes as many
    observations to become the most common value as the old price has. Narrow it
    to a recent window if prices start moving often.
    """
    seen: dict[tuple[str, str], dict[float, int]] = {}
    for row in rows:
        raw = (row.get("price") or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        counts = seen.setdefault((row["club"], row["slot_start"]), {})
        counts[value] = counts.get(value, 0) + 1
    return {key: max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0] for key, counts in seen.items()}


def booked_price(row: dict, observed: dict[tuple[str, str], float],
                 prices: dict[str, float]) -> tuple[float | None, bool]:
    """(price for this slot, whether it came from the site) - None when nothing knows it."""
    raw = (row.get("price") or "").strip()
    if raw:
        try:
            return float(raw), True
        except ValueError:
            pass
    hit = observed.get((row["club"], row["slot_start"]))
    if hit is not None:
        return hit, True
    return prices.get(row["club"]), False


def daily_occupancy(final: list[dict], prices: dict[str, float] | None = None,
                    observed: dict[tuple[str, str], float] | None = None) -> list[dict]:
    prices = prices or {}
    observed = observed or {}
    agg: dict[tuple, dict] = {}
    hours: dict[tuple, float] = {}
    total_hours: dict[tuple, float] = {}
    revenue: dict[tuple, float] = {}
    site_hours: dict[tuple, float] = {}
    prev_hours: dict[tuple, float] = {}   # booked the evening before
    kept_hours: dict[tuple, float] = {}   # ... and still booked at the start
    for row in final:
        key = (row["club"], row["slot_date"])
        a = agg.setdefault(key, {p: [0, 0] for p in ("all", "morning", "afternoon", "evening")})
        booked = 1 if row["final_status"] == "booked" else 0
        for p in ("all", period(row["slot_start"])):
            a[p][0] += 1
            a[p][1] += booked
        length = slot_hours(row)
        total_hours[key] = total_hours.get(key, 0.0) + length
        if row.get("status_prev_day") == "booked":
            prev_hours[key] = prev_hours.get(key, 0.0) + length
            if booked:
                kept_hours[key] = kept_hours.get(key, 0.0) + length
        if booked:
            hours[key] = hours.get(key, 0.0) + length
            price, from_site = booked_price(row, observed, prices)
            if price is not None:
                revenue[key] = revenue.get(key, 0.0) + length * price
                if from_site:
                    site_hours[key] = site_hours.get(key, 0.0) + length

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
        booked_h = hours.get(key, 0.0)
        rev = revenue.get(key, 0.0)
        site_h = site_hours.get(key, 0.0)
        price = prices.get(key[0])
        # Keys are set in column order, which is what the sheet writer expects.
        row["booked_hours"] = f"{booked_h:g}"
        row["price_per_hour_assumed"] = f"{price:g}" if price is not None else ""
        # Empty, not zero, when no price is known from anywhere: an unknown is not a 0 MDL day.
        known = rev > 0 or price is not None
        row["revenue_estimate_mdl"] = f"{round(rev):d}" if known else ""
        row["total_hours"] = f"{total_hours.get(key, 0.0):g}"
        row["price_per_hour_effective"] = f"{rev / booked_h:.0f}" if booked_h and rev else ""
        row["site_priced_pct"] = f"{100 * site_h / booked_h:.0f}" if booked_h else ""
        prev_h = prev_hours.get(key, 0.0)
        kept_h = kept_hours.get(key, 0.0)
        row["booked_hours_prev_day"] = f"{prev_h:g}"
        row["released_hours"] = f"{prev_h - kept_h:g}"
        # Empty, not 0, when the slots were never seen a day ahead: nothing to compare.
        row["released_pct"] = f"{100 * (prev_h - kept_h) / prev_h:.0f}" if prev_h else ""
        row["grid_released"] = "1" if prev_h >= RELEASE_MIN_HOURS and kept_h < RELEASE_KEPT_MIN * prev_h else ""
        out.append(row)
    return out


def usual_hours(offered: dict[str, float]) -> dict[str, float]:
    """Court-hours a club usually offers around each of its dates.

    `offered` maps date -> total_hours observed. For every date the answer is the
    most common value among the dates within COMPLETE_WINDOW_DAYS on either side
    (ties going to the lower value), so a lasting change in opening hours only
    affects how the days around it are judged.
    """
    dates = {d: dt.date.fromisoformat(d) for d in offered}
    out = {}
    for d, day in dates.items():
        counts: dict[float, int] = {}
        for other, value in offered.items():
            if abs((dates[other] - day).days) <= COMPLETE_WINDOW_DAYS:
                counts[value] = counts.get(value, 0) + 1
        out[d] = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    return out


def dashboard(daily: list[dict], final: list[dict], today: dt.date,
              prices: dict[str, float] | None = None) -> list[dict]:
    """One row per club, aggregated over FINISHED and fully observed days only.

    Two filters, and both are needed:

    - the day must be over (slot_date < today). Today and tomorrow have a full slot
      count but their bookings have not arrived yet, so including them drags
      occupancy down. The size of the error depends on how many finished days they
      are averaged against: over the 6 observed days of October 2026 it was 44.0%
      vs 38.2%, and over the whole 24-day history 1-3 points per club. It is always
      downward, and it never goes away as long as the two days are counted.
    - the day must have been fully observed, measured in COURT-HOURS, not slots:
      total_hours is courts x opening hours, so it does not move when the grid
      step does (PadelPoint went from 288 half-hour slots to 144 full-hour ones on
      2026-10-10 and stayed at 144 court-hours a day). A day counts when its
      total_hours reaches what the club usually offers around that date; fewer
      hours means collection started late or lost part of the grid.

      "Usually" is the most common total_hours among the club's days within
      COMPLETE_WINDOW_DAYS on either side, not the club's maximum ever. A maximum
      breaks on the day a club ADDS opening hours: padelpoint.md added a
      23:00-24:00 slot on 2026-10-13 (144 -> 153 court-hours) and every earlier
      day was suddenly "incomplete", which silently cut 105 booked hours out of
      the week of 2026-10-05. A local mode also keeps history valid when a club
      drops hours: only the days around the change are judged against the new
      shape. Ties go to the lower value, so a club in transition keeps its days.

    A day the booking grid emptied out (grid_released) is dropped too: see
    RELEASE_MIN_HOURS. Both exclusions are counted in zile_incomplete and
    zile_eliberate so a club cannot quietly stop moving in this table.

    Occupancy is measured in court-hours for the same reason as completeness: a
    half-hour slot and a full-hour slot are not one unit of the same thing.

    `today` is the local date in the configured timezone, not UTC.
    """
    prices = prices or {}
    courts = {}
    for row in final:
        courts.setdefault(row["club"], set()).add(row["court"])

    by_club: dict[str, list[dict]] = {}
    for row in daily:
        if dt.date.fromisoformat(row["date"]) >= today:
            continue  # not finished yet: bookings for it are still coming in
        by_club.setdefault(row["club"], []).append(row)

    out = []
    for club in sorted(by_club):
        rows = by_club[club]
        offered = {r["date"]: float(r.get("total_hours") or 0) for r in rows}
        full = usual_hours(offered)
        complete, incomplete, released = [], 0, 0
        for r in rows:
            if r.get("grid_released"):
                released += 1
            elif offered[r["date"]] >= full[r["date"]]:
                complete.append(r)
            else:
                incomplete += 1
        if not complete:
            continue
        days = len(complete)
        booked_h = sum(float(r["booked_hours"] or 0) for r in complete)
        total_h = sum(float(r.get("total_hours") or 0) for r in complete)
        eve = sum(int(r["evening_slots"]) for r in complete)
        eve_booked = sum(int(r["evening_booked"]) for r in complete)
        revenues = [r.get("revenue_estimate_mdl", "") for r in complete]
        revenue = sum(float(v) for v in revenues if v) if any(revenues) else None
        price = prices.get(club)
        n_courts = len(courts.get(club, ()))
        out.append({
            "club": club,
            "terenuri": str(n_courts),
            "prima_zi": min(r["date"] for r in complete),
            "ultima_zi": max(r["date"] for r in complete),
            "zile_complete": str(days),
            "ore_rezervate": f"{booked_h:g}",
            "ore_pe_zi": f"{booked_h / days:.1f}",
            "ore_pe_teren_pe_zi": f"{booked_h / days / n_courts:.1f}" if n_courts else "",
            "ocupare_pct": f"{100 * booked_h / total_h:.1f}" if total_h else "",
            "ocupare_seara_pct": f"{100 * eve_booked / eve:.1f}" if eve else "",
            "pret_ora_presupus": f"{price:g}" if price is not None else "",
            "venit_estimat_mdl": f"{round(revenue):d}" if revenue is not None else "",
            "venit_estimat_pe_zi_mdl": f"{round(revenue / days):d}" if revenue is not None else "",
            "pret_ora_efectiv": f"{revenue / booked_h:.0f}" if revenue and booked_h else "",
            "zile_incomplete": str(incomplete),
            "zile_eliberate": str(released),
        })
    return out


def build_tables(raw_dir: Path = RAW_DIR, tz_name: str = "Europe/Chisinau",
                 config_path=DEFAULT_CONFIG) -> tuple[list[dict], list[dict], list[dict]]:
    tz = zoneinfo.ZoneInfo(tz_name)
    rows = read_all_rows(raw_dir)
    final = slots_final(rows, tz)
    prices = {c.name: c.price_per_hour for c in load_config(config_path).clubs if c.price_per_hour is not None}
    daily = daily_occupancy(final, prices, site_prices(rows))
    today = dt.datetime.now(tz).date()
    return final, daily, dashboard(daily, final, today, prices)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", default=str(RAW_DIR))
    ap.add_argument("--push", action="store_true", help="rewrite the slots_final and daily_occupancy tabs")
    args = ap.parse_args(argv)

    final, daily, dash = build_tables(Path(args.raw_dir))
    print(f"slots_final: {len(final)} rows; daily_occupancy: {len(daily)} rows; "
          f"dashboard: {len(dash)} rows", file=sys.stderr)
    for row in dash:
        print("  " + "  ".join(f"{k}={v}" for k, v in row.items()), file=sys.stderr)
    if not args.push:
        return 0
    from .sheets import push_tables  # gspread/google-auth only needed here

    if not final:
        print("nothing to push: slots_final is empty", file=sys.stderr)
        return 1
    push_tables(final, daily, dash)
    print("pushed both tabs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
