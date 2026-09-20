import zoneinfo

from collector.derive import DAILY_COLUMNS, SLOTS_FINAL_COLUMNS, build_tables, daily_occupancy, period, slots_final
from collector.storage import append_rows
from tests.test_storage import make_row

TZ = zoneinfo.ZoneInfo("Europe/Chisinau")


def obs(ts, status, **over):
    return make_row(snapshot_ts=ts, status=status, raw_status="available=" + ("true" if status == "free" else "false"), **over)


def test_last_status_before_start_and_first_seen_booked():
    rows = [
        obs("2026-11-02T20:00:00+02:00", "free"),      # day before: free
        obs("2026-11-03T09:00:00+02:00", "booked"),    # first seen booked
        obs("2026-11-03T12:00:00+02:00", "free"),      # cancelled
        obs("2026-11-03T18:30:00+02:00", "booked"),    # last observation before 19:00 start
        obs("2026-11-03T19:05:00+02:00", "past"),      # after start: ignored
        obs("2026-11-03T21:00:00+02:00", "past"),
    ]
    final = slots_final(rows, TZ)
    assert len(final) == 1 and list(final[0]) == SLOTS_FINAL_COLUMNS
    f = final[0]
    assert f["final_status"] == "booked"
    assert f["last_observed_at"] == "2026-11-03T18:30:00+02:00"
    assert f["first_seen_booked_at"] == "2026-11-03T09:00:00+02:00"
    assert f["observations"] == "4"


def test_slot_only_seen_after_start_is_excluded():
    assert slots_final([obs("2026-11-03T19:30:00+02:00", "past")], TZ) == []


def test_free_slot_has_no_first_seen_booked():
    f = slots_final([obs("2026-11-03T18:00:00+02:00", "free")], TZ)[0]
    assert f["final_status"] == "free" and f["first_seen_booked_at"] == ""


def test_period_boundaries():
    assert period("11:30") == "morning"
    assert period("12:00") == "afternoon"
    assert period("16:30") == "afternoon"
    assert period("17:00") == "evening"


def test_daily_occupancy_split():
    final = []
    spec = {"07:00": "free", "11:00": "booked", "12:00": "booked", "16:00": "free", "17:00": "booked", "21:00": "booked"}
    for start, status in spec.items():
        final.append({"club": "Divi Padel Club", "court": "№1 - Blue", "slot_date": "2026-11-03", "slot_start": start,
                      "slot_end": "", "final_status": status, "last_observed_at": "", "first_seen_booked_at": "", "observations": "1"})
    for r in final:
        r["slot_end"] = f"{int(r['slot_start'][:2]) + 1:02d}:00"
    daily = daily_occupancy(final, {"Divi Padel Club": 500})
    assert len(daily) == 1 and list(daily[0]) == DAILY_COLUMNS
    d = daily[0]
    assert (d["total_slots"], d["booked_slots"], d["occupancy_pct"]) == ("6", "4", "66.7")
    assert (d["booked_hours"], d["price_per_hour_assumed"], d["revenue_estimate_mdl"]) == ("4", "500", "2000")
    no_price = daily_occupancy(final)[0]
    assert (no_price["booked_hours"], no_price["price_per_hour_assumed"], no_price["revenue_estimate_mdl"]) == ("4", "", "")
    assert (d["morning_slots"], d["morning_booked"], d["morning_pct"]) == ("2", "1", "50.0")
    assert (d["afternoon_slots"], d["afternoon_booked"], d["afternoon_pct"]) == ("2", "1", "50.0")
    assert (d["evening_slots"], d["evening_booked"], d["evening_pct"]) == ("2", "2", "100.0")


def test_build_tables_is_idempotent(tmp_path):
    rows = [obs("2026-11-03T09:00:00+02:00", "booked"),
            obs("2026-11-03T09:00:00+02:00", "free", court="№2 - Green"),
            obs("2026-11-03T07:00:00+02:00", "free", club="Ursu Padel", court="Padel (exterior)", slot_start="08:00", slot_end="08:30")]
    append_rows(rows, tmp_path)
    first = build_tables(tmp_path)
    second = build_tables(tmp_path)
    assert first == second
    final, daily = first
    assert [(r["club"], r["date"], r["booked_slots"], r["total_slots"]) for r in daily] == [
        ("Divi Padel Club", "2026-11-03", "1", "2"), ("Ursu Padel", "2026-11-03", "0", "1")]


def test_slot_hours_handles_midnight_end():
    from collector.derive import slot_hours
    assert slot_hours({"slot_start": "23:30", "slot_end": "00:00"}) == 0.5
    assert slot_hours({"slot_start": "07:00", "slot_end": "08:00"}) == 1.0
