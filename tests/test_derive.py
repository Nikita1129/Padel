import datetime as dt
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
        # complete days: 64 one-hour slots each
        {"club": "Divi Padel Club", "date": "2026-11-01", "total_slots": "64", "booked_slots": "32",
         "evening_slots": "24", "evening_booked": "18", "booked_hours": "32", "total_hours": "64",
         "revenue_estimate_mdl": "16000"},
        {"club": "Divi Padel Club", "date": "2026-11-02", "total_slots": "64", "booked_slots": "16",
         "evening_slots": "24", "evening_booked": "6", "booked_hours": "16", "total_hours": "64",
         "revenue_estimate_mdl": "8000"},
        # partial day (collection started mid-day): must be ignored
        {"club": "Divi Padel Club", "date": "2026-10-31", "total_slots": "20", "booked_slots": "20",
         "evening_slots": "10", "evening_booked": "10", "booked_hours": "20", "total_hours": "20",
         "revenue_estimate_mdl": "10000"},
    ]
    rows = dashboard(daily, final, dt.date(2026, 11, 3), {"Divi Padel Club": 500})
    assert len(rows) == 1 and list(rows[0]) == DASHBOARD_COLUMNS
    d = rows[0]
    assert (d["zile_complete"], d["prima_zi"], d["ultima_zi"]) == ("2", "2026-11-01", "2026-11-02")
    assert (d["ore_rezervate"], d["ore_pe_zi"]) == ("48", "24.0")
    assert (d["terenuri"], d["ore_pe_teren_pe_zi"]) == ("2", "12.0")
    assert d["ocupare_pct"] == "37.5"          # 48 booked of 128 court-hours
    assert d["ocupare_seara_pct"] == "50.0"    # 24 booked of 48 evening slots
    assert (d["venit_estimat_mdl"], d["venit_estimat_pe_zi_mdl"]) == ("24000", "12000")
    assert (d["pret_ora_presupus"], d["pret_ora_efectiv"]) == ("500", "500")


def test_dashboard_without_price_leaves_revenue_empty():
    from collector.derive import dashboard
    final = [{"club": "X", "court": "c1", "slot_date": "2026-11-01", "slot_start": "19:00",
              "slot_end": "20:00", "final_status": "booked", "last_observed_at": "",
              "first_seen_booked_at": "", "observations": "1"}]
    daily = [{"club": "X", "date": "2026-11-01", "total_slots": "10", "booked_slots": "5",
              "evening_slots": "4", "evening_booked": "2", "booked_hours": "5", "total_hours": "10",
              "revenue_estimate_mdl": ""}]
    d = dashboard(daily, final, dt.date(2026, 11, 2))[0]
    assert d["venit_estimat_mdl"] == "" and d["pret_ora_presupus"] == ""
    assert d["ore_rezervate"] == "5"


def test_dashboard_excludes_today_and_tomorrow():
    """Days still filling up have a full slot count but not their final bookings.

    Without this filter the in-progress days were averaged in and pulled every
    club's occupancy down (~6 points on real October data).
    """
    from collector.derive import dashboard
    def day(d, booked):
        return {"club": "Divi Padel Club", "date": d, "total_slots": "64", "booked_slots": str(booked),
                "evening_slots": "24", "evening_booked": "12", "booked_hours": str(booked),
                "total_hours": "64", "revenue_estimate_mdl": str(booked * 500)}
    final = [{"club": "Divi Padel Club", "court": "c1", "slot_date": "2026-11-01", "slot_start": "19:00",
              "slot_end": "20:00", "final_status": "booked", "last_observed_at": "",
              "first_seen_booked_at": "", "observations": "1"}]
    daily = [day("2026-11-01", 32), day("2026-11-02", 32),
             day("2026-11-03", 20),   # today: half elapsed
             day("2026-11-04", 4)]    # tomorrow: barely booked yet
    d = dashboard(daily, final, dt.date(2026, 11, 3))[0]
    assert (d["zile_complete"], d["ultima_zi"]) == ("2", "2026-11-02")
    assert d["ocupare_pct"] == "50.0"          # 64 of 128 court-hours, the two finished days only
    assert d["ore_pe_zi"] == "32.0"


def test_dashboard_skips_a_club_with_no_finished_day():
    from collector.derive import dashboard
    final = [{"club": "X", "court": "c1", "slot_date": "2026-11-03", "slot_start": "19:00",
              "slot_end": "20:00", "final_status": "booked", "last_observed_at": "",
              "first_seen_booked_at": "", "observations": "1"}]
    daily = [{"club": "X", "date": "2026-11-03", "total_slots": "10", "booked_slots": "5",
              "evening_slots": "4", "evening_booked": "2", "booked_hours": "5", "total_hours": "10"}]
    assert dashboard(daily, final, dt.date(2026, 11, 3)) == []


def test_slots_final_keeps_the_last_price_the_site_showed():
    """A slot that ends up booked shows no price any more; the earlier observation does."""
    rows = [obs("2026-11-03T09:00:00+02:00", "free", price="300"),
            obs("2026-11-03T15:00:00+02:00", "free", price="500"),
            obs("2026-11-03T18:30:00+02:00", "booked")]
    f = slots_final(rows, TZ)[0]
    assert (f["final_status"], f["price"]) == ("booked", "500")


def test_site_prices_takes_the_most_common_value_per_club_and_hour():
    from collector.derive import site_prices
    rows = [obs("2026-11-03T09:00:00+02:00", "free", court=f"c{i}", price=p)
            for i, p in enumerate(["300", "300", "500", "", "not-a-number"])]
    rows += [obs("2026-11-03T09:00:00+02:00", "free", club="Ursu Padel", slot_start="08:00",
                 slot_end="09:00", price="250")]
    assert site_prices(rows) == {("Divi Padel Club", "19:00"): 300.0, ("Ursu Padel", "08:00"): 250.0}


def test_site_prices_breaks_a_tie_upwards():
    """Half the observations at 500 and half at 300 must not round the estimate down."""
    from collector.derive import site_prices
    rows = [obs("2026-11-03T09:00:00+02:00", "free", court=f"c{i}", price=p)
            for i, p in enumerate(["300", "500"])]
    assert site_prices(rows) == {("Divi Padel Club", "19:00"): 500.0}


def final_row(**over):
    row = {"club": "PadelPoint", "court": "Court 1", "slot_date": "2026-11-03", "slot_start": "11:00",
           "slot_end": "12:00", "final_status": "booked", "last_observed_at": "",
           "first_seen_booked_at": "", "observations": "1", "price": ""}
    row.update(over)
    return row


def test_daily_occupancy_prefers_the_real_price_over_the_config_assumption():
    """The point of collecting the price: 11:00 costs 300, not the 500 in config."""
    final = [final_row(price="300"), final_row(court="Court 2", slot_start="19:00", slot_end="20:00",
                                               price="500")]
    d = daily_occupancy(final, {"PadelPoint": 500})[0]
    assert (d["booked_hours"], d["total_hours"]) == ("2", "2")
    assert d["revenue_estimate_mdl"] == "800"          # 300 + 500, not 2 x 500
    assert (d["price_per_hour_assumed"], d["price_per_hour_effective"]) == ("500", "400")
    assert d["site_priced_pct"] == "100"


def test_daily_occupancy_prices_an_always_booked_slot_from_the_same_hour_elsewhere():
    """A slot taken at every observation never shows a price; the same hour does."""
    final = [final_row()]                               # booked throughout: no price of its own
    observed = {("PadelPoint", "11:00"): 300.0}
    d = daily_occupancy(final, {"PadelPoint": 500}, observed)[0]
    assert (d["revenue_estimate_mdl"], d["site_priced_pct"]) == ("300", "100")
    # without that table the config assumption is used, and says so
    d = daily_occupancy(final, {"PadelPoint": 500})[0]
    assert (d["revenue_estimate_mdl"], d["site_priced_pct"]) == ("500", "0")


def test_dashboard_counts_days_of_both_grid_steps():
    """PadelPoint went from 288 half-hour slots a day to 144 full-hour ones on 2026-10-10.

    A single per-club maximum would treat every day after the change as
    incomplete and the club would silently stop moving in this table.
    """
    from collector.derive import dashboard
    final = [{"club": "PadelPoint", "court": f"Court {c}", "slot_date": "2026-10-09",
              "slot_start": "19:00", "slot_end": "20:00", "final_status": "booked",
              "last_observed_at": "", "first_seen_booked_at": "", "observations": "1", "price": ""}
             for c in range(1, 10)]

    def day(date, slots, hours, booked_h):
        return {"club": "PadelPoint", "date": date, "total_slots": str(slots), "booked_slots": "0",
                "evening_slots": "0", "evening_booked": "0", "booked_hours": str(booked_h),
                "total_hours": str(hours), "revenue_estimate_mdl": str(int(booked_h * 400))}

    daily = [day("2026-10-08", 288, 144, 60), day("2026-10-09", 288, 144, 70),   # half-hour era
             day("2026-10-11", 144, 144, 65), day("2026-10-12", 144, 144, 75),   # full-hour era
             day("2026-10-13", 100, 100, 40)]                                    # late start: ignored
    d = dashboard(daily, final, dt.date(2026, 10, 14), {"PadelPoint": 500})[0]
    assert d["zile_complete"] == "4"
    assert (d["prima_zi"], d["ultima_zi"]) == ("2026-10-08", "2026-10-12")
    assert d["ore_rezervate"] == "270"
    assert d["ocupare_pct"] == "46.9"            # 270 of 576 court-hours
    assert (d["pret_ora_presupus"], d["pret_ora_efectiv"]) == ("500", "400")
