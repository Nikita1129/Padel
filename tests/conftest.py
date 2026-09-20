import datetime as dt
import zoneinfo
from pathlib import Path

import pytest

from collector.config import load_config

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TZ = zoneinfo.ZoneInfo("Europe/Chisinau")


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def clubs(cfg):
    return {c.slug: c for c in cfg.clubs}


@pytest.fixture
def now():
    # Fixed observation time in the middle of the fixture day.
    return dt.datetime(2026, 9, 20, 14, 30, tzinfo=TZ)


def read_fixture(*parts) -> str:
    return FIXTURES.joinpath(*parts).read_text(encoding="utf-8")
