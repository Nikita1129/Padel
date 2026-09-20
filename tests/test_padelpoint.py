import datetime as dt

import pytest

from collector.padelpoint import slots_from_buttons
from collector.parse import ParseError

DAY = dt.date(2026, 9, 20)


def buttons(states):
    """states: list of (HH:MM, 'Alege'|'Ocupat'|other, disabled)."""
    return [(f"{t}\n{s}", d) for t, s, d in states]


def test_buttons_to_slots(clubs, now):
    club = clubs["padelpoint"]
    grid = [(f"{h:02d}:{m:02d}", "Ocupat" if (h * 2 + m // 30) % 4 == 0 else "Alege", (h * 2 + m // 30) % 4 == 0)
            for h in range(7, 23) for m in (0, 30)]
    courts = {f"Court {n}": buttons(grid) + [("23:00\nSfarsit", True)] for n in range(1, 10)}
    slots = slots_from_buttons(club, courts, DAY, now)
    assert len(slots) == 9 * 32
    assert {s.court for s in slots} == {f"Court {n}" for n in range(1, 10)}
    assert all(s.club == "PadelPoint" for s in slots)
    assert {s.slot_start for s in slots if s.slot_start >= dt.time(23, 0)} == set()   # end marker skipped
    # pattern: Ocupat when (2h + m/30) % 4 == 0 -> 08:00, 10:00, ..., 18:00, 20:00, 22:00
    past = next(s for s in slots if s.court == "Court 1" and s.slot_start == dt.time(8, 0))
    assert (past.status, past.raw_status, past.slot_end) == ("past", "Ocupat;disabled=true", dt.time(8, 30))
    free = next(s for s in slots if s.court == "Court 1" and s.slot_start == dt.time(7, 0))
    assert (free.status, free.raw_status) == ("free", "Alege;disabled=false")
    booked = next(s for s in slots if s.court == "Court 1" and s.slot_start == dt.time(18, 0))
    assert booked.status == "booked"


def test_expirat_is_past_as_reported_by_the_site(clubs, now):
    club = clubs["padelpoint"]
    courts = {f"Court {n}": buttons([("07:00", "Expirat", True), ("07:30", "Alege", False)]) for n in range(1, 10)}
    slots = slots_from_buttons(club, courts, DAY, now)
    s = next(x for x in slots if x.court == "Court 1" and x.slot_start == dt.time(7, 0))
    assert (s.status, s.raw_status) == ("past", "Expirat;disabled=true")


def test_unknown_state_fails(clubs, now):
    club = clubs["padelpoint"]
    courts = {f"Court {n}": buttons([("07:00", "Alege", False), ("07:30", "Rezervat?", True)]) for n in range(1, 10)}
    with pytest.raises(ParseError, match="unrecognised state"):
        slots_from_buttons(club, courts, DAY, now)


def test_missing_court_fails(clubs, now):
    club = clubs["padelpoint"]
    courts = {f"Court {n}": buttons([("07:00", "Alege", False), ("07:30", "Alege", False)]) for n in range(1, 9)}
    with pytest.raises(ParseError, match="expected 9 courts, found 8"):
        slots_from_buttons(club, courts, DAY, now)


def test_single_digit_hour_is_padded(clubs, now):
    club = clubs["padelpoint"]
    courts = {f"Court {n}": buttons([("7:00", "Alege", False), ("7:30", "Ocupat", True)]) for n in range(1, 10)}
    slots = slots_from_buttons(club, courts, DAY, now)
    assert {s.slot_start for s in slots} == {dt.time(7, 0), dt.time(7, 30)}
