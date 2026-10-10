import dataclasses
import datetime as dt

import pytest

from collector.padelpoint import slots_from_buttons
from collector.parse import ParseError

DAY = dt.date(2026, 9, 20)
DASH = "–"  # the site writes the interval with an en dash


def hourly(states):
    """states: [(start hour, label, disabled)] in the layout used since 2026-10-10."""
    return [(f"{h:02d}:00{DASH}{h + 1:02d}:00\n{label}", d) for h, label, d in states]


def half_hourly(states):
    """states: [(HH:MM, 'Alege'|'Ocupat'|other, disabled)] in the layout used until 2026-10-10."""
    return [(f"{t}\n{s}", d) for t, s, d in states]


# A realistic day: everything before `now` (14:30) is gone, later hours alternate.
FULL_DAY = [(h, "Indisponibil", True) if h < 14 or h % 2 == 0 else (h, "500 L", False)
            for h in range(7, 23)]


def test_new_layout_buttons_to_slots(clubs, now):
    club = clubs["padelpoint"]
    courts = {f"Court {n}": hourly(FULL_DAY) for n in range(1, 10)}
    slots = slots_from_buttons(club, courts, DAY, now)

    assert len(slots) == 9 * 16                      # 16 full-hour slots, 07:00 to 23:00
    assert {s.court for s in slots} == {f"Court {n}" for n in range(1, 10)}
    assert all(s.club == "PadelPoint" for s in slots)
    one = {s.slot_start: s for s in slots if s.court == "Court 1"}
    assert (one[dt.time(7, 0)].slot_end, one[dt.time(22, 0)].slot_end) == (dt.time(8, 0), dt.time(23, 0))
    # "Indisponibil" is booked-or-started, exactly like Courtica's data-available=false
    assert one[dt.time(8, 0)].status == "past"
    assert one[dt.time(8, 0)].raw_status == "Indisponibil;disabled=true"
    assert one[dt.time(18, 0)].status == "booked"
    # a price label plus disabled=false is the only thing that means free now
    assert one[dt.time(15, 0)].status == "free"
    assert one[dt.time(15, 0)].raw_status == "500 L;disabled=false"


def test_new_layout_keeps_the_price_the_site_shows(clubs, now):
    club = clubs["padelpoint"]
    labels = [(h, "600 lei", False) if h == 20 else (h, "1 200,00 MDL", False) if h == 21
              else (h, "500 L", False) if h >= 14 else (h, "Indisponibil", True)
              for h in range(7, 23)]
    slots = slots_from_buttons(club, {f"Court {n}": hourly(labels) for n in range(1, 10)}, DAY, now)
    one = {s.slot_start: s for s in slots if s.court == "Court 1"}
    assert one[dt.time(15, 0)].price == "500"
    assert one[dt.time(20, 0)].price == "600"
    assert one[dt.time(21, 0)].price == "1200.00"
    assert one[dt.time(8, 0)].price == ""             # the site prices only what it still sells


def test_interval_that_disagrees_with_the_config_fails(clubs, now):
    """The slot length is stated on the button; a change there must stop the run, not reinterpret it."""
    club = clubs["padelpoint"]
    courts = {f"Court {n}": [(f"07:00{DASH}07:30\n500 L", False), (f"07:30{DASH}08:00\n500 L", False)]
              for n in range(1, 10)}
    with pytest.raises(ParseError, match="button says 07:00-07:30 .30 min., config says 60"):
        slots_from_buttons(club, courts, DAY, now)


def test_old_half_hour_layout_still_parses(clubs, now):
    """Pages in the pre-2026-10-10 layout (and the fixtures built from them) must keep working."""
    club = dataclasses.replace(clubs["padelpoint"], slot_minutes=30)
    grid = [(f"{h:02d}:{m:02d}", "Ocupat" if (h * 2 + m // 30) % 4 == 0 else "Alege",
             (h * 2 + m // 30) % 4 == 0)
            for h in range(7, 23) for m in (0, 30)]
    courts = {f"Court {n}": half_hourly(grid) + [("23:00\nSfarsit", True)] for n in range(1, 10)}
    slots = slots_from_buttons(club, courts, DAY, now)

    assert len(slots) == 9 * 32
    assert {s.slot_start for s in slots if s.slot_start >= dt.time(23, 0)} == set()   # end marker skipped
    one = {s.slot_start: s for s in slots if s.court == "Court 1"}
    assert (one[dt.time(8, 0)].status, one[dt.time(8, 0)].raw_status) == ("past", "Ocupat;disabled=true")
    assert (one[dt.time(7, 0)].status, one[dt.time(7, 0)].raw_status) == ("free", "Alege;disabled=false")
    assert one[dt.time(18, 0)].status == "booked"
    assert {s.price for s in slots} == {""}          # the old layout showed no price


def test_expirat_is_past_as_reported_by_the_site(clubs, now):
    club = dataclasses.replace(clubs["padelpoint"], slot_minutes=30)
    courts = {f"Court {n}": half_hourly([("07:00", "Expirat", True), ("07:30", "Alege", False)])
              for n in range(1, 10)}
    slots = slots_from_buttons(club, courts, DAY, now)
    s = next(x for x in slots if x.court == "Court 1" and x.slot_start == dt.time(7, 0))
    assert (s.status, s.raw_status) == ("past", "Expirat;disabled=true")


def test_unknown_state_fails(clubs, now):
    club = clubs["padelpoint"]
    courts = {f"Court {n}": hourly([(7, "500 L", False), (8, "Rezervat?", True)]) for n in range(1, 10)}
    with pytest.raises(ParseError, match="unrecognised state"):
        slots_from_buttons(club, courts, DAY, now)


def test_missing_court_fails(clubs, now):
    club = clubs["padelpoint"]
    courts = {f"Court {n}": hourly([(7, "500 L", False), (8, "500 L", False)]) for n in range(1, 9)}
    with pytest.raises(ParseError, match="expected 9 courts, found 8"):
        slots_from_buttons(club, courts, DAY, now)


def test_single_digit_hour_is_padded(clubs, now):
    club = clubs["padelpoint"]
    courts = {f"Court {n}": [(f"7:00{DASH}8:00\n500 L", False), (f"8:00{DASH}9:00\nIndisponibil", True)]
              for n in range(1, 10)}
    slots = slots_from_buttons(club, courts, DAY, now)
    assert {s.slot_start for s in slots} == {dt.time(7, 0), dt.time(8, 0)}
