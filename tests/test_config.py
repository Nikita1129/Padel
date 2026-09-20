import pytest

from collector.config import ConfigError, load_config


def test_default_config_clubs(cfg):
    assert cfg.timezone == "Europe/Chisinau"
    assert [c.slug for c in cfg.clubs] == ["divi-padel", "ursu-padel", "primus-padel-costesti", "padelpoint"]
    divi, ursu, primus, pp = cfg.clubs
    assert (divi.expected_courts, divi.slot_minutes, divi.platform) == (4, 60, "courtica")
    assert (ursu.expected_courts, ursu.slot_minutes) == (1, 30)
    assert (primus.expected_courts, primus.slot_minutes, primus.fetch) == (2, 30, "auto")
    assert (pp.platform, pp.fetch, pp.expected_courts, pp.slot_minutes) == ("padelpoint", "browser", 9, 30)
    assert cfg.run_budget_seconds == 240


def test_adding_a_club_is_config_only(tmp_path):
    p = tmp_path / "clubs.yaml"
    p.write_text(
        "clubs:\n"
        "  - {slug: x, name: X Club, url: 'https://www.courtica.md/en-MD/clubs/x?sport=padel',"
        " expected_courts: 2, slot_minutes: 90}\n"
    )
    cfg = load_config(p)
    assert cfg.clubs[0].name == "X Club" and cfg.clubs[0].slot_minutes == 90


@pytest.mark.parametrize("body", [
    "clubs: []\n",
    "clubs:\n  - {slug: x, name: X, url: https://a, expected_courts: 0, slot_minutes: 60}\n",
    "clubs:\n  - {slug: x, name: X, url: https://a, expected_courts: 1, slot_minutes: 60, fetch: magic}\n",
    "clubs:\n  - {slug: x, name: X, url: http://a, expected_courts: 1, slot_minutes: 60}\n",
    "clubs:\n  - {slug: x, name: X, url: https://a, expected_courts: 1, slot_minutes: 60}\n"
    "  - {slug: x, name: Y, url: https://b, expected_courts: 1, slot_minutes: 60}\n",
    "clubs:\n  - {slug: x, name: X, url: https://a, expected_courts: 1, slot_minutes: 60, platform: other}\n",
    "clubs:\n  - {slug: x, name: X, url: https://a, expected_courts: 1, slot_minutes: 60, platform: padelpoint}\n",
    "run_budget_seconds: 0\nclubs:\n  - {slug: x, name: X, url: https://a, expected_courts: 1, slot_minutes: 60}\n",
])
def test_invalid_config_is_rejected(tmp_path, body):
    p = tmp_path / "clubs.yaml"
    p.write_text(body)
    with pytest.raises(ConfigError):
        load_config(p)


def test_window_and_sheet_id_from_default_config(cfg):
    import datetime as dt
    assert (cfg.window_start, cfg.window_end) == (dt.time(6, 30), dt.time(23, 59))


@pytest.mark.parametrize("window", ["nonsense", "23:00-06:00", "6:30"])
def test_bad_window_rejected(tmp_path, window):
    p = tmp_path / "clubs.yaml"
    p.write_text(f"collection_window: '{window}'\nclubs:\n  - {{slug: x, name: X, url: https://a, expected_courts: 1, slot_minutes: 60}}\n")
    with pytest.raises(ConfigError, match="collection_window"):
        load_config(p)
