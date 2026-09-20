import json

import pytest

from collector import sheets
from collector.derive import DAILY_COLUMNS, SLOTS_FINAL_COLUMNS


class FakeWorksheet:
    def __init__(self):
        self.calls = []

    def clear(self):
        self.calls.append("clear")

    def update(self, values, rng, value_input_option=None):
        self.calls.append(("update", rng, values, value_input_option))


class FakeSpreadsheet:
    def __init__(self, existing=()):
        self.tabs = {t: FakeWorksheet() for t in existing}

    def worksheet(self, title):
        if title not in self.tabs:
            raise KeyError(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet()
        return self.tabs[title]


def test_push_rewrites_both_tabs_and_creates_missing_ones():
    ss = FakeSpreadsheet(existing=["slots_final"])
    final = [{c: f"f-{c}" for c in SLOTS_FINAL_COLUMNS}]
    daily = [{c: f"d-{c}" for c in DAILY_COLUMNS}]
    sheets.push_tables(final, daily, spreadsheet=ss)
    assert set(ss.tabs) == {"slots_final", "daily_occupancy"}
    for title, cols, rows in (("slots_final", SLOTS_FINAL_COLUMNS, final), ("daily_occupancy", DAILY_COLUMNS, daily)):
        calls = ss.tabs[title].calls
        assert calls[0] == "clear"
        _, rng, values, opt = calls[1]
        assert rng == "A1" and opt == "RAW"
        assert values[0] == cols and values[1] == [rows[0][c] for c in cols]


def test_missing_credentials_is_loud(monkeypatch):
    monkeypatch.delenv(sheets.ENV_CREDENTIALS, raising=False)
    with pytest.raises(sheets.SheetsError, match="GOOGLE_SERVICE_ACCOUNT_JSON"):
        sheets.open_spreadsheet("abc")
    monkeypatch.setenv(sheets.ENV_CREDENTIALS, "{not json")
    with pytest.raises(sheets.SheetsError, match="not valid JSON"):
        sheets.open_spreadsheet("abc")


def test_sheet_id_required_in_config(tmp_path):
    p = tmp_path / "clubs.yaml"
    p.write_text("clubs: []\n")
    with pytest.raises(sheets.SheetsError, match="sheet_id"):
        sheets.sheet_id_from_config(p)
    p.write_text("sheet_id: 1AbC\nclubs: []\n")
    assert sheets.sheet_id_from_config(p) == "1AbC"
