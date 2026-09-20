"""Load and validate config/clubs.yaml."""
from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import yaml

FETCH_MODES = ("auto", "html", "browser")
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "clubs.yaml"


class ConfigError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Club:
    slug: str
    name: str
    url: str
    expected_courts: int
    slot_minutes: int
    fetch: str = "auto"


@dataclasses.dataclass(frozen=True)
class Config:
    timezone: str
    clubs: tuple[Club, ...]
    window_start: dt.time = dt.time(6, 30)
    window_end: dt.time = dt.time(23, 59)


def parse_window(text: str) -> tuple[dt.time, dt.time]:
    try:
        a, b = str(text).split("-")
        start, end = dt.time.fromisoformat(a.strip()), dt.time.fromisoformat(b.strip())
    except ValueError as exc:
        raise ConfigError(f"collection_window must look like 'HH:MM-HH:MM', got {text!r}") from exc
    if start >= end:
        raise ConfigError(f"collection_window start must be before end, got {text!r}")
    return start, end


def load_config(path: Path | str = DEFAULT_CONFIG) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("clubs"), list) or not raw["clubs"]:
        raise ConfigError(f"{path}: expected a mapping with a non-empty 'clubs' list")
    tz = raw.get("timezone") or "Europe/Chisinau"
    window = parse_window(raw.get("collection_window") or "06:30-23:59")
    clubs = []
    for i, item in enumerate(raw["clubs"]):
        try:
            club = Club(
                slug=str(item["slug"]),
                name=str(item["name"]),
                url=str(item["url"]),
                expected_courts=int(item["expected_courts"]),
                slot_minutes=int(item["slot_minutes"]),
                fetch=str(item.get("fetch", "auto")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"{path}: club #{i} is invalid: {exc!r}") from exc
        if club.expected_courts < 1 or club.slot_minutes < 1:
            raise ConfigError(f"{path}: club {club.slug}: expected_courts and slot_minutes must be >= 1")
        if club.fetch not in FETCH_MODES:
            raise ConfigError(f"{path}: club {club.slug}: fetch must be one of {FETCH_MODES}")
        if not club.url.startswith("https://"):
            raise ConfigError(f"{path}: club {club.slug}: url must start with https://")
        clubs.append(club)
    if len({c.slug for c in clubs}) != len(clubs):
        raise ConfigError(f"{path}: duplicate club slugs")
    return Config(timezone=tz, clubs=tuple(clubs), window_start=window[0], window_end=window[1])
