"""collect() end-to-end with fetch_grid replaced by fixture files: no network."""
import datetime as dt

import pytest

from collector import run
from collector.fetch import FetchError, Throttle
from collector.parse import ParseError
from collector.run import RAW_COLUMNS, CollectError, collect, dedup_key
from tests.conftest import read_fixture

DAY = dt.date(2026, 9, 20)


def fake_fetch_factory(mapping):
    def fake_fetch(club, url, throttle, browser, log=print):
        html = mapping[club.slug]
        if html is None:
            raise FetchError(f"{club.slug}: simulated block")
        return html, "html"
    return fake_fetch


def test_collect_two_clubs_two_days(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({
        "divi-padel": read_fixture("divi-padel", "synthetic-2026-09-20.html"),
        "ursu-padel": read_fixture("ursu-padel", "synthetic-2026-09-20.html"),
    }))
    rows = collect(list(cfg.clubs), [DAY, DAY + dt.timedelta(days=1)], now, log=lambda *_: None)
    assert len(rows) == 2 * (64 + 28)
    assert all(list(r) == RAW_COLUMNS for r in rows)
    assert {r["snapshot_ts"] for r in rows} == {"2026-09-20T14:30:00+03:00"}
    assert {r["slot_date"] for r in rows} == {"2026-09-20", "2026-09-21"}
    assert {r["source"] for r in rows} == {"html"}
    assert len({dedup_key(r) for r in rows}) == len(rows)


def test_collect_saves_html(cfg, now, monkeypatch, tmp_path):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({
        "divi-padel": read_fixture("divi-padel", "synthetic-2026-09-20.html"),
        "ursu-padel": read_fixture("ursu-padel", "synthetic-2026-09-20.html"),
    }))
    collect(list(cfg.clubs), [DAY], now, save_html=tmp_path, log=lambda *_: None)
    assert (tmp_path / "divi-padel" / "2026-09-20_html.html").exists()
    assert (tmp_path / "ursu-padel" / "2026-09-20_html.html").exists()


def test_one_blocked_club_fails_whole_run(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({
        "divi-padel": read_fixture("divi-padel", "synthetic-2026-09-20.html"),
        "ursu-padel": None,
    }))
    with pytest.raises(FetchError, match="simulated block"):
        collect(list(cfg.clubs), [DAY], now, log=lambda *_: None)


def test_bad_page_fails_whole_run(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({
        "divi-padel": read_fixture("bad", "unknown-status.html"),
        "ursu-padel": read_fixture("ursu-padel", "synthetic-2026-09-20.html"),
    }))
    with pytest.raises(ParseError):
        collect(list(cfg.clubs), [DAY], now, log=lambda *_: None)


def test_budget_exceeded_aborts(cfg, now, monkeypatch):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({
        "divi-padel": read_fixture("divi-padel", "synthetic-2026-09-20.html"),
        "ursu-padel": read_fixture("ursu-padel", "synthetic-2026-09-20.html"),
    }))
    with pytest.raises(CollectError, match="budget"):
        collect(list(cfg.clubs), [DAY], now, log=lambda *_: None, budget_s=-1)


def test_main_dry_run_exit_codes(cfg, monkeypatch, capsys):
    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({
        "divi-padel": read_fixture("divi-padel", "synthetic-2026-09-20.html"),
        "ursu-padel": read_fixture("ursu-padel", "synthetic-2026-09-20.html"),
    }))
    assert run.main(["--dry-run"]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines()[0] == "\t".join(RAW_COLUMNS)
    assert "nothing written" in out.err

    monkeypatch.setattr(run, "fetch_grid", fake_fetch_factory({"divi-padel": None, "ursu-padel": None}))
    assert run.main(["--dry-run"]) == 1
    assert "RUN FAILED, nothing written" in capsys.readouterr().err


def test_throttle_spaces_requests():
    clock = [0.0]
    slept = []
    t = Throttle(1.0, sleep=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)), clock=lambda: clock[0])
    t.wait(); t.wait(); clock[0] += 0.4; t.wait()
    assert slept == pytest.approx([1.0, 0.6])
