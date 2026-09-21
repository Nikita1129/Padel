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
    final, daily, dash = first
    assert [(r["club"], r["date"], r["booked_slots"], r["total_slots"]) for r in daily] == [
        ("Divi Padel Club", "2026-11-03", "1", "2"), ("Ursu Padel", "2026-11-03", "0", "1")]


def test_slot_hours_handles_midnight_end():
    from collector.derive import slot_hours
    assert slot_hours({"slot_start": "23:30", "slot_end": "00:00"}) == 0.5
    assert slot_hours({"slot_start": "07:00", "slot_end": "08:00"}) == 1.0


def test_dashboard_only_counts_complete_days():
    from collector.derive import DASHBOARD_COLUMNS, dashboard
    final = [{"club": "Divi Padel Club", "court": f"c{c}", "slot_date": d, "slot_start": "19:00",
              "slot_end": "20:00", "final_status": "booked", "last_observed_at": "",
              "first_seen_booked_at": "", "observations": "1"} for d in ("2026-11-01", "2026-11-02") for c in (1, 2)]
    daily = [
        # complete days: 64 slots each
        {"club": "Divi Padel Club", "date": "2026-11-01", "total_slots": "64", "booked_slots": "32",
         "evening_slots": "24", "evening_booked": "18", "booked_hours": "32"},
        {"club": "Divi Padel Club", "date": "2026-11-02", "total_slots": "64", "booked_slots": "16",
         "evening_slots": "24", "evening_booked": "6", "booked_hours": "16"},
        # partial day (collection started mid-day): must be ignored
        {"club": "Divi Padel Club", "date": "2026-10-31", "total_slots": "20", "booked_slots": "20",
         "evening_slots": "10", "evening_booked": "10", "booked_hours": "20"},
    ]
    rows = dashboard(daily, final, {"Divi Padel Club": 500})
    assert len(rows) == 1 and list(rows[0]) == DASHBOARD_COLUMNS
    d = rows[0]
    assert (d["zile_complete"], d["prima_zi"], d["ultima_zi"]) == ("2", "2026-11-01", "2026-11-02")
    assert (d["ore_rezervate"], d["ore_pe_zi"]) == ("48", "24.0")
    assert (d["terenuri"], d["ore_pe_teren_pe_zi"]) == ("2", "12.0")
    assert d["ocupare_pct"] == "37.5"          # 48 booked of 128 slots
    assert d["ocupare_seara_pct"] == "50.0"    # 24 booked of 48 evening slots
    assert (d["venit_estimat_mdl"], d["venit_estimat_pe_zi_mdl"]) == ("24000", "12000")


def test_dashboard_without_price_leaves_revenue_empty():
    from collector.derive import dashboard
    final = [{"club": "X", "court": "c1", "slot_date": "2026-11-01", "slot_start": "19:00",
              "slot_end": "20:00", "final_status": "booked", "last_observed_at": "",
              "first_seen_booked_at": "", "observations": "1"}]
    daily = [{"club": "X", "date": "2026-11-01", "total_slots": "10", "booked_slots": "5",
              "evening_slots": "4", "evening_booked": "2", "booked_hours": "5"}]
    d = dashboard(daily, final)[0]
    assert d["venit_estimat_mdl"] == "" and d["pret_ora_presupus"] == ""
    assert d["ore_rezervate"] == "5"
