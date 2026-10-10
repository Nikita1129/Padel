"""The gap alarm: a club that stops being collected must not hide behind the others."""
import datetime as dt
import sys
import zoneinfo

from tools import check_freshness as cf
from collector.storage import append_rows
from tests.test_storage import make_row

TZ = zoneinfo.ZoneInfo("Europe/Chisinau")
CLUBS = ["Divi Padel Club", "Ursu Padel", "Primus Padel Costesti", "PadelPoint"]


def ts(day, hour):
    return dt.datetime(2026, 11, day, hour, tzinfo=TZ)


def test_newest_by_club_takes_the_latest_row_of_each_club(tmp_path):
    rows = [make_row(snapshot_ts="2026-11-03T09:00:00+02:00"),
            make_row(snapshot_ts="2026-11-03T18:00:00+02:00"),
            make_row(snapshot_ts="2026-11-01T18:00:00+02:00", club="PadelPoint", court="Court 1"),
            make_row(snapshot_ts="", club="Ursu Padel"),            # unusable rows are skipped
            make_row(snapshot_ts="2026-11-03T18:00:00+02:00", club="")]
    append_rows([r for r in rows if r["snapshot_ts"] and r["club"]], tmp_path)
    found = cf.newest_by_club(str(tmp_path))
    assert found == {"Divi Padel Club": ts(3, 18), "PadelPoint": ts(1, 18)}
    assert cf.newest_snapshot(str(tmp_path)) == ts(3, 18)


def test_a_club_left_behind_by_the_others_is_flagged():
    """The failure mode of 2026-10-10: three clubs written every run, the fourth missing."""
    per_club = {c: ts(3, 18) for c in CLUBS}
    per_club["PadelPoint"] = ts(2, 10)
    stale = cf.stale_clubs(per_club, CLUBS, ts(3, 18), 6.0)
    assert [c for c, _ in stale] == ["PadelPoint"]
    assert stale[0][1] == 32.0


def test_clubs_that_are_all_equally_old_are_not_flagged():
    """Overnight, or after a full stop, nothing is behind anything: that is the age check's job."""
    per_club = {c: ts(3, 18) for c in CLUBS}
    assert cf.stale_clubs(per_club, CLUBS, ts(3, 18), 6.0) == []


def test_a_club_with_no_rows_at_all_is_flagged():
    per_club = {c: ts(3, 18) for c in CLUBS if c != "PadelPoint"}
    assert cf.stale_clubs(per_club, CLUBS, ts(3, 18), 6.0) == [("PadelPoint", None)]


def test_a_club_within_the_limit_is_not_flagged():
    per_club = {c: ts(3, 18) for c in CLUBS}
    per_club["Ursu Padel"] = ts(3, 13)      # 5 h behind: one skipped run, not a hole
    assert cf.stale_clubs(per_club, CLUBS, ts(3, 18), 6.0) == []


def test_main_exits_1_when_a_club_is_missing_from_recent_rows(tmp_path, monkeypatch, capsys):
    """End to end, and independent of the wall clock: the check is club against club."""
    append_rows([make_row(snapshot_ts="2026-11-03T18:00:00+02:00"),
                 make_row(snapshot_ts="2026-11-01T18:00:00+02:00", club="PadelPoint", court="Court 1")],
                tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_freshness.py", "--raw-dir", str(tmp_path),
                                      "--max-age-hours", "6"])
    assert cf.main() == 1
    err = capsys.readouterr().err
    assert "PadelPoint: 48.0 h behind" in err
    assert "Ursu Padel: no rows at all" in err
