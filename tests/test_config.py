import pytest

from collector.config import ConfigError, load_config


def test_default_config_has_two_courtica_clubs(cfg):
    assert cfg.timezone == "Europe/Chisinau"
    assert [c.slug for c in cfg.clubs] == ["divi-padel", "ursu-padel"]
    divi, ursu = cfg.clubs
    assert (divi.expected_courts, divi.slot_minutes) == (4, 60)
    assert (ursu.expected_courts, ursu.slot_minutes) == (1, 30)
    assert all(c.fetch == "auto" for c in cfg.clubs)


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
])
def test_invalid_config_is_rejected(tmp_path, body):
    p = tmp_path / "clubs.yaml"
    p.write_text(body)
    with pytest.raises(ConfigError):
        load_config(p)
