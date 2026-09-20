"""Rewrite the two derived tabs in a Google Sheet through a service account.

Credentials: the JSON key of the service account, passed verbatim in the
environment variable GOOGLE_SERVICE_ACCOUNT_JSON (never a file in the repo).
Target sheet: the `sheet_id` key in config/clubs.yaml.
"""
from __future__ import annotations

import json
import os

import yaml

from .config import DEFAULT_CONFIG
from .derive import DAILY_COLUMNS, SLOTS_FINAL_COLUMNS

ENV_CREDENTIALS = "GOOGLE_SERVICE_ACCOUNT_JSON"
TAB_SLOTS = "slots_final"
TAB_DAILY = "daily_occupancy"


class SheetsError(RuntimeError):
    pass


def sheet_id_from_config(path=DEFAULT_CONFIG) -> str:
    raw = yaml.safe_load(open(path, encoding="utf-8"))
    sheet_id = (raw or {}).get("sheet_id")
    if not sheet_id:
        raise SheetsError(f"{path}: 'sheet_id' is not set")
    return str(sheet_id)


def open_spreadsheet(sheet_id: str):
    import gspread

    creds = os.environ.get(ENV_CREDENTIALS)
    if not creds:
        raise SheetsError(f"environment variable {ENV_CREDENTIALS} is not set")
    try:
        info = json.loads(creds)
    except json.JSONDecodeError as exc:
        raise SheetsError(f"{ENV_CREDENTIALS} is not valid JSON: {exc}") from exc
    client = gspread.service_account_from_dict(info)
    return client.open_by_key(sheet_id)


def rewrite_tab(spreadsheet, title: str, columns: list[str], rows: list[dict]) -> None:
    """Replace the whole tab content with header + rows (never appends)."""
    values = [columns] + [[row[c] for c in columns] for row in rows]
    try:
        ws = spreadsheet.worksheet(title)
    except Exception:
        ws = spreadsheet.add_worksheet(title=title, rows=max(len(values), 2), cols=len(columns))
    ws.clear()
    ws.update(values, "A1", value_input_option="RAW")


def push_tables(final: list[dict], daily: list[dict], sheet_id: str | None = None, spreadsheet=None) -> None:
    if spreadsheet is None:
        spreadsheet = open_spreadsheet(sheet_id or sheet_id_from_config())
    rewrite_tab(spreadsheet, TAB_SLOTS, SLOTS_FINAL_COLUMNS, final)
    rewrite_tab(spreadsheet, TAB_DAILY, DAILY_COLUMNS, daily)
