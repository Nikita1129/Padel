"""collect() end-to-end with fetch_grid replaced by fixture files: no network."""
import datetime as dt

import pytest

from collector import run
from collector.fetch import FetchError, Throttle
from collector.parse import ParseError
from collector.run import RAW_COLUMNS, CollectError, collect, dedup_key
from tests.conftest import read_fixture

DAY = dt.date(2026, 9, 20)


PP_BUTTONS = [(f"{h:02d}:{m:02d}\nAlege" if (h + m) % 3 else f"{h:02d}:{m:02d}\nOcupat", (h + m) % 3 == 0)
              for h in range(7, 23) for m in (0, 30)] + [("23:00\nSfarsit", True)]


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
N_ALL = N_COURTICA + 9 * 32


@pytest.fixture(autouse=True)
def _fake_padelpoint(monkeypatch):
    monkeypatch.setattr(run, "collect_padelpoint", fake_padelpoint)


def test_collect_two_clubs_two_days(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    rows = collect(list(cfg.clubs), [DAY, DAY + dt.timedelta(days=1)], now, log=lambda *_: None)
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


def test_one_blocked_club_fails_whole_run(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({**ALL_FIXTURES, "ursu-padel": None}))
    with pytest.raises(FetchError, match="simulated block"):
        collect(list(cfg.clubs), [DAY], now, log=lambda *_: None)


def test_bad_page_fails_whole_run(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({**ALL_FIXTURES, "divi-padel": read_fixture("bad", "unknown-status.html")}))
    with pytest.raises(ParseError):
        collect(list(cfg.clubs), [DAY], now, log=lambda *_: None)


def test_budget_exceeded_aborts(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    with pytest.raises(CollectError, match="budget"):
        collect(list(cfg.clubs), [DAY], now, log=lambda *_: None, budget_s=-1)


def test_main_dry_run_exit_codes(cfg, monkeypatch, capsys):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory(ALL_FIXTURES))
    assert run.main(["--dry-run"]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines()[0] == "\t".join(RAW_COLUMNS)
    assert "nothing written" in out.err

    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({k: None for k in ALL_FIXTURES}))
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
    monkeypatch.setattr(sheets, "push_tables", lambda final, daily: pushed.update(final=final, daily=daily))
    assert run.main(["--raw-dir", str(tmp_path)]) == 0
    rows = read_all_rows(tmp_path)
    assert len(rows) == 2 * N_ALL
    assert set(pushed) == {"final", "daily"}
    assert len(pushed["daily"]) >= 2  # both clubs, tomorrow's date at least

    # push failure keeps the raw rows and exits 1
    def boom(final, daily):
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
