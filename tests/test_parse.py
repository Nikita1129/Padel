import dataclasses
import datetime as dt

import pytest

from collector.parse import ParseError, has_grid, parse_grid
from tests.conftest import read_fixture

DAY = dt.date(2026, 9, 20)


def test_divi_grid(clubs, now):
    slots = parse_grid(read_fixture("divi-padel", "synthetic-2026-09-20.html"), clubs["divi-padel"], DAY, now)
    assert len(slots) == 64
    assert {s.court for s in slots} == {"№1 - Blue", "№2 - Green", "№3 - Red", "№4 - Black"}
    assert all(s.club == "Divi Padel Club" for s in slots)
    starts = sorted({s.slot_start for s in slots})
    assert starts[0] == dt.time(7, 0) and starts[-1] == dt.time(22, 0) and len(starts) == 16
    last = next(s for s in slots if s.slot_start == dt.time(22, 0))
    assert last.slot_end == dt.time(23, 0)
    assert {s.status for s in slots} <= {"free", "booked", "past"}
    assert {s.raw_status for s in slots} == {"available=true", "available=false"}
    assert all(s.price == "" and s.booking_type == "" for s in slots)


def test_ursu_grid(clubs, now):
    slots = parse_grid(read_fixture("ursu-padel", "synthetic-2026-09-20.html"), clubs["ursu-padel"], DAY, now)
    assert len(slots) == 28
    assert {s.court for s in slots} == {"Padel (exterior)"}
    s = next(x for x in slots if x.slot_start == dt.time(21, 30))
    assert s.slot_end == dt.time(22, 0)


def test_past_vs_booked_depends_on_now(clubs, now):
    html = read_fixture("divi-padel", "synthetic-2026-09-20.html")
    slots = parse_grid(html, clubs["divi-padel"], DAY, now)   # now = 14:30
    for s in slots:
        if s.raw_status.startswith("available=true"):
            assert s.status == "free"
        elif s.slot_start <= dt.time(14, 0):
            assert s.status == "past", s
        else:
            assert s.status == "booked", s
    # Tomorrow's grid observed today: nothing is past.
    tomorrow = parse_grid(html, clubs["divi-padel"], DAY + dt.timedelta(days=1), now)
    assert "past" not in {s.status for s in tomorrow}
    # Observed late in the evening: every unavailable cell is past, none booked.
    late = parse_grid(html, clubs["divi-padel"], DAY, now.replace(hour=23, minute=0))
    assert "booked" not in {s.status for s in late}


def test_slot_exactly_at_now_is_past(clubs, now):
    html = read_fixture("divi-padel", "synthetic-2026-09-20.html")
    at_14 = parse_grid(html, clubs["divi-padel"], DAY, now.replace(minute=0))
    s = next(x for x in at_14 if x.slot_start == dt.time(14, 0) and x.court == "№1 - Blue")
    assert s.status in ("free", "past")


def test_has_grid():
    assert has_grid(read_fixture("ursu-padel", "synthetic-2026-09-20.html"))
    assert not has_grid(read_fixture("bad", "no-grid.html"))
    assert not has_grid(read_fixture("bad", "blocked-403.html"))


@pytest.mark.parametrize("fixture, message", [
    (("bad", "no-grid.html"), "no grid cells"),
    (("bad", "blocked-403.html"), "no grid cells"),
    (("bad", "unknown-status.html"), "unknown data-available"),
    (("bad", "missing-court-name.html"), "no <th> court name"),
])
def test_bad_pages_raise(clubs, now, fixture, message):
    club = dataclasses.replace(clubs["divi-padel"], expected_courts=1)
    with pytest.raises(ParseError, match=message):
        parse_grid(read_fixture(*fixture), club, DAY, now)


def test_wrong_court_count_raises(clubs, now):
    with pytest.raises(ParseError, match="expected 4 courts, found 1"):
        parse_grid(read_fixture("ursu-padel", "synthetic-2026-09-20.html"), clubs["divi-padel"], DAY, now)


def test_wrong_step_raises(clubs, now):
    club = dataclasses.replace(clubs["ursu-padel"], slot_minutes=60)
    with pytest.raises(ParseError, match="grid step is 30 min, config says 60"):
        parse_grid(read_fixture("ursu-padel", "synthetic-2026-09-20.html"), club, DAY, now)


def test_naive_now_rejected(clubs):
    with pytest.raises(ValueError):
        parse_grid("<td data-time='07:00' data-available='true'>", clubs["ursu-padel"], DAY, dt.datetime(2026, 9, 20))


REAL_NOW = dt.datetime(2026, 9, 20, 12, 48, tzinfo=dt.timezone(dt.timedelta(hours=3)))  # capture time of fixtures/real


@pytest.mark.parametrize("slug, day, n_slots, courts, expected", [
    ("divi-padel", "2026-09-20", 64, 4, {"past": 24, "booked": 30, "free": 10}),
    ("divi-padel", "2026-09-21", 64, 4, {"free": 41, "booked": 23}),
    ("ursu-padel", "2026-09-20", 28, 1, {"past": 10, "booked": 16, "free": 2}),
    ("ursu-padel", "2026-09-21", 28, 1, {"free": 24, "booked": 4}),
])
def test_real_pages_captured_from_github_actions(clubs, slug, day, n_slots, courts, expected):
    """Real server-rendered pages fetched by the first workflow run (plain GET, no browser)."""
    html = read_fixture("real", slug, f"{day}_html.html")
    slots = parse_grid(html, clubs[slug], dt.date.fromisoformat(day), REAL_NOW)
    assert len(slots) == n_slots and len({s.court for s in slots}) == courts
    counts = {}
    for s in slots:
        counts[s.status] = counts.get(s.status, 0) + 1
    assert counts == expected
    assert {s.raw_status for s in slots} <= {"available=true;pending=false", "available=false;pending=false"}


def test_real_pages_differ_between_today_and_tomorrow(clubs):
    """The ?date= parameter really selects the day: the two captures are not the same grid."""
    a = parse_grid(read_fixture("real", "divi-padel", "2026-09-20_html.html"), clubs["divi-padel"], dt.date(2026, 9, 21), REAL_NOW)
    b = parse_grid(read_fixture("real", "divi-padel", "2026-09-21_html.html"), clubs["divi-padel"], dt.date(2026, 9, 21), REAL_NOW)
    assert [s.raw_status for s in a] != [s.raw_status for s in b]


def test_unknown_pending_value_raises(clubs, now):
    html = '<table><tr><th>Padel (exterior)</th><td data-time="08:00" data-available="true" data-pending="maybe"></td></tr></table>'
    with pytest.raises(ParseError, match="unknown data-pending"):
        parse_grid(html, clubs["ursu-padel"], DAY, now)
