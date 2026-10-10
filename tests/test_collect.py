"""collect() end-to-end with fetch_grid replaced by fixture files: no network."""
import datetime as dt

import pytest

from collector import run
from collector.fetch import FetchError, Throttle
from collector.parse import ParseError
from collector.run import RAW_COLUMNS, CollectError, collect, dedup_key
from tests.conftest import read_fixture

DAY = dt.date(2026, 9, 20)


# The layout padelpoint.md has served since 2026-10-10: full hours, interval plus
# either a price (free) or "Indisponibil" (booked or already started).
PP_BUTTONS = [(f"{h:02d}:00\u2013{h + 1:02d}:00\n" + ("500 L" if h % 3 else "Indisponibil"), h % 3 == 0)
              for h in range(7, 23)]


def fake_fetch_factory(mapping):
    """Serve fixtures for Courtica clubs; None simulates a block. PadelPoint is faked separately."""
    def fake_fetch(club, url, throttle, browser, log=print):
        html = mapping[club.slug]
        if html is None:
            raise FetchError(f"{club.slug}: simulated block")
        return html, "html"
    return fake_fetch


def fake_padelpoint(club, slot_date, now, browser, save_dir=None, log=print):
    from collector.padelpoint import slots_from_buttons
    return slots_from_buttons(club, {f"Court {n}": PP_BUTTONS for n in range(1, 10)}, slot_date, now)


ALL_FIXTURES = {
    "divi-padel": read_fixture("divi-padel", "synthetic-2026-09-20.html"),
    "ursu-padel": read_fixture("ursu-padel", "synthetic-2026-09-20.html"),
    "primus-padel-costesti": read_fixture("primus-padel-costesti", "synthetic-2026-09-20.html"),
}
N_COURTICA = 64 + 28 + 68
N_ALL = N_COURTICA + 9 * 16


@pytest.fixture(autouse=True)
def _fake_padelpoint(monkeypatch):
    monkeypatch.setattr(run, "collect_padelpoint", fake_padelpoint)


def test_collect_two_clubs_two_days(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    rows, failures = collect(list(cfg.clubs), [DAY, DAY + dt.timedelta(days=1)], now, log=lambda *_: None)
    assert failures == []
    assert len(rows) == 2 * N_ALL
    assert all(list(r) == RAW_COLUMNS for r in rows)
    assert {r["snapshot_ts"] for r in rows} == {"2026-09-20T14:30:00+03:00"}
    assert {r["slot_date"] for r in rows} == {"2026-09-20", "2026-09-21"}
    assert {r["source"] for r in rows} == {"html", "browser"}
    assert {r["club"] for r in rows} == {"Divi Padel Club", "Ursu Padel", "Primus Padel Costesti", "PadelPoint"}
    assert len({dedup_key(r) for r in rows}) == len(rows)


def test_collect_saves_html(cfg, now, monkeypatch, tmp_path):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    collect(list(cfg.clubs), [DAY], now, save_html=tmp_path, log=lambda *_: None)
    assert (tmp_path / "divi-padel" / "2026-09-20_html.html").exists()
    assert (tmp_path / "ursu-padel" / "2026-09-20_html.html").exists()


def test_one_blocked_club_keeps_the_others(cfg, now, monkeypatch):
    """The whole point of the change: Ursu being down must not cost the other three."""
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({**ALL_FIXTURES, "ursu-padel": None}))
    rows, failures = collect(list(cfg.clubs), [DAY], now, log=lambda *_: None)
    assert len(failures) == 1 and "simulated block" in failures[0] and failures[0].startswith("ursu-padel")
    assert {r["club"] for r in rows} == {"Divi Padel Club", "Primus Padel Costesti", "PadelPoint"}
    assert len(rows) == N_ALL - 28


def test_bad_page_is_one_failure_not_a_lost_snapshot(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({**ALL_FIXTURES, "divi-padel": read_fixture("bad", "unknown-status.html")}))
    rows, failures = collect(list(cfg.clubs), [DAY], now, log=lambda *_: None)
    assert len(failures) == 1 and failures[0].startswith("divi-padel") and "ParseError" in failures[0]
    assert "Divi Padel Club" not in {r["club"] for r in rows}
    assert len(rows) == N_ALL - 64


def test_every_club_down_writes_nothing(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({k: None for k in ALL_FIXTURES}))
    monkeypatch.setattr(run, "collect_padelpoint", lambda *a, **k: (_ for _ in ()).throw(FetchError("pp down")))
    with pytest.raises(CollectError, match="zero rows collected"):
        collect(list(cfg.clubs), [DAY], now, log=lambda *_: None)


def test_failure_on_one_date_keeps_the_other(cfg, now, monkeypatch):
    """A club that fails for tomorrow still contributes today."""
    calls = []

    def flaky(club, url, throttle, browser, log=print):
        calls.append(url)
        if club.slug == "divi-padel" and url.endswith("2026-09-21"):
            raise FetchError("divi-padel: tomorrow timed out")
        return ALL_FIXTURES[club.slug], "html"

    monkeypatch.setattr(run, "fetch_grid", flaky)
    rows, failures = collect(list(cfg.clubs), [DAY, DAY + dt.timedelta(days=1)], now, log=lambda *_: None)
    assert len(failures) == 1 and "2026-09-21" in failures[0]
    divi = {r["slot_date"] for r in rows if r["club"] == "Divi Padel Club"}
    assert divi == {"2026-09-20"}
    assert len(rows) == 2 * N_ALL - 64


def test_budget_exceeded_keeps_what_was_collected(cfg, now, monkeypatch):
    """Over budget stops further fetching but no longer throws away the rows already in hand."""
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    budget = iter([0.0, 0.0, 999.0, 999.0, 999.0, 999.0])  # trips on the second club
    monkeypatch.setattr(run.time, "monotonic", lambda: next(budget, 999.0))
    rows, failures = collect(list(cfg.clubs), [DAY], now, log=lambda *_: None, budget_s=1.0)
    assert len(failures) == 1 and "budget" in failures[0]
    assert rows and {r["club"] for r in rows} == {"Divi Padel Club"}


def test_budget_exceeded_before_anything_still_raises(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    with pytest.raises(CollectError, match="zero rows collected"):
        collect(list(cfg.clubs), [DAY], now, log=lambda *_: None, budget_s=-1)


def test_main_dry_run_exit_codes(cfg, monkeypatch, capsys):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    assert run.main(["--dry-run"]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines()[0] == "\t".join(RAW_COLUMNS)
    assert "nothing written" in out.err

    # one club down: rows still printed, exit 1, failure named
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({**ALL_FIXTURES, "ursu-padel": None}))
    assert run.main(["--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "PARTIAL RUN" in err and "ursu-padel" in err

    # everything down: nothing to write at all
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({k: None for k in ALL_FIXTURES}))
    monkeypatch.setattr(run, "collect_padelpoint", lambda *a, **k: (_ for _ in ()).throw(FetchError("pp down")))
    assert run.main(["--dry-run"]) == 1
    assert "RUN FAILED, nothing written" in capsys.readouterr().err


def test_throttle_spaces_requests():
    clock = [0.0]
    slept = []
    t = Throttle(1.0, sleep=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)), clock=lambda: clock[0])
    t.wait(); t.wait(); clock[0] += 0.4; t.wait()
    assert slept == pytest.approx([1.0, 0.6])


def test_main_real_run_appends_then_pushes(cfg, monkeypatch, tmp_path):
    from collector import derive, sheets
    from collector.storage import read_all_rows

    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    pushed = {}
    monkeypatch.setattr(sheets, "push_tables", lambda final, daily, dash: pushed.update(final=final, daily=daily, dash=dash))
    assert run.main(["--raw-dir", str(tmp_path)]) == 0
    rows = read_all_rows(tmp_path)
    assert len(rows) == 2 * N_ALL
    assert set(pushed) == {"final", "daily", "dash"}
    assert {r["club"] for r in pushed["dash"]} <= {"Divi Padel Club", "Ursu Padel", "Primus Padel Costesti", "PadelPoint"}
    assert len(pushed["daily"]) >= 2  # both clubs, tomorrow's date at least

    # push failure keeps the raw rows and exits 1
    def boom(final, daily, dash=None):
        raise sheets.SheetsError("quota")
    monkeypatch.setattr(sheets, "push_tables", boom)
    assert run.main(["--raw-dir", str(tmp_path)]) == 1
    assert len(read_all_rows(tmp_path)) == 4 * N_ALL

    # --no-push never touches sheets
    monkeypatch.setattr(sheets, "push_tables", boom)
    assert run.main(["--raw-dir", str(tmp_path), "--no-push"]) == 0


def test_respect_window_skips_outside_hours(cfg, monkeypatch, capsys):
    import datetime as dt
    import zoneinfo

    class FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 12, 1, 5, 35, tzinfo=tz)  # 05:35 local: outside 06:30-23:59

    monkeypatch.setattr(run.dt, "datetime", FakeDatetime)
    called = []
    monkeypatch.setattr(run, "fetch_grid", lambda *a, **k: called.append(1) or (_ for _ in ()).throw(AssertionError("must not fetch")))
    assert run.main(["--respect-window", "--dry-run"]) == 0
    assert "skipped" in capsys.readouterr().err and not called

    class InWindow(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 12, 1, 6, 35, tzinfo=tz)

    monkeypatch.setattr(run.dt, "datetime", InWindow)
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    assert run.main(["--respect-window", "--dry-run"]) == 0
    assert f"{2 * N_ALL} rows" in capsys.readouterr().err
