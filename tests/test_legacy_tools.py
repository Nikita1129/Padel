import json
import zoneinfo

import pytest

from collector.storage import read_all_rows
from tests.test_storage import make_row
from tools import import_legacy, parity_check

TZ = zoneinfo.ZoneInfo("Europe/Chisinau")


def legacy_item(**over):
    item = {"runId": "r1", "data": "2026-09-05", "zi": "Sâmbătă", "club": "Divi Padel Club", "teren": "№1 - Blue",
            "ora": "19:00", "interval": "SEARA", "durata_ore": 1, "ocupat": 1, "ore_ocupate": 1, "viitor": 1,
            "temp_max": 25, "poza": "x", "status": "OK", "colectat_la": "2026-09-05T15:30:00.000Z"}
    item.update(over)
    return item


def test_mapping_rules():
    booked = import_legacy.map_row(legacy_item(), TZ)
    assert booked["snapshot_ts"] == "2026-09-05T18:30:00+03:00"
    assert (booked["slot_start"], booked["slot_end"], booked["status"]) == ("19:00", "20:00", "booked")
    assert booked["raw_status"] == "ocupat=1" and booked["source"] == "apify_legacy"
    assert booked["price"] == "" and booked["booking_type"] == ""
    assert import_legacy.map_row(legacy_item(ocupat=0, viitor=1), TZ)["status"] == "free"
    assert import_legacy.map_row(legacy_item(ocupat=1, viitor=0), TZ)["status"] == "past"
    half = import_legacy.map_row(legacy_item(club="Ursu Padel", teren="Padel (exterior)", ora="21:30", durata_ore=0.5), TZ)
    assert half["slot_end"] == "22:00"
    assert import_legacy.map_row(legacy_item(status="PARSE FAILED", teren=None, ora=None), TZ) is None
    with pytest.raises(ValueError):
        import_legacy.map_row(legacy_item(ocupat=None, viitor=None), TZ)


def test_convert_filters_clubs_and_dedups():
    items = [legacy_item(), legacy_item(), legacy_item(club="PadelPoint", teren="Court 1"),
             legacy_item(status="PARSE FAILED", teren=None, ora=None)]
    rows, stats = import_legacy.convert(items, TZ, {"Divi Padel Club", "Ursu Padel"})
    assert len(rows) == 1
    assert stats == {"input": 4, "skipped_not_ok": 1, "skipped_other_club": 1, "duplicates": 1, "mapped": 1}
    rows_all, _ = import_legacy.convert(items, TZ, None)
    assert len(rows_all) == 2


def test_import_cli_refuses_second_run(tmp_path):
    export = tmp_path / "export.json"
    export.write_text(json.dumps([legacy_item(), legacy_item(ora="20:00")]))
    raw = tmp_path / "raw"
    assert import_legacy.main([str(export), "--raw-dir", str(raw), "--dry-run"]) == 0
    assert not raw.exists()
    assert import_legacy.main([str(export), "--raw-dir", str(raw)]) == 0
    assert len(read_all_rows(raw)) == 2
    assert import_legacy.main([str(export), "--raw-dir", str(raw)]) == 1
    assert len(read_all_rows(raw)) == 2
    # a club not imported yet can still be added later
    export2 = tmp_path / "export2.json"
    export2.write_text(json.dumps([legacy_item(club="Ursu Padel", teren="Padel (exterior)", durata_ore=0.5)]))
    assert import_legacy.main([str(export2), "--raw-dir", str(raw)]) == 0
    assert len(read_all_rows(raw)) == 3


def test_parity_compare_reports_mismatches_only_on_overlapping_dates():
    rows = [
        # overlapping date 2026-09-05: slot 19:00 agrees, slot 20:00 disagrees, slot 21:00 only in legacy
        make_row(snapshot_ts="2026-09-05T18:00:00+03:00", slot_date="2026-09-05", slot_start="19:00", status="booked", source="apify_legacy"),
        make_row(snapshot_ts="2026-09-05T18:00:00+03:00", slot_date="2026-09-05", slot_start="19:00", status="booked", source="browser"),
        make_row(snapshot_ts="2026-09-05T18:00:00+03:00", slot_date="2026-09-05", slot_start="20:00", status="booked", source="apify_legacy"),
        make_row(snapshot_ts="2026-09-05T18:00:00+03:00", slot_date="2026-09-05", slot_start="20:00", status="free", source="browser"),
        make_row(snapshot_ts="2026-09-05T18:00:00+03:00", slot_date="2026-09-05", slot_start="21:00", status="free", source="apify_legacy"),
        # non-overlapping date: only new rows, must not be reported
        make_row(snapshot_ts="2026-09-21T18:00:00+03:00", slot_date="2026-09-21", slot_start="19:00", status="booked", source="browser"),
    ]
    r = parity_check.compare(rows, TZ)
    assert r["overlap_dates"] == [("Divi Padel Club", "2026-09-05")]
    assert r["compared"] == 2
    assert r["mismatches"] == [(("Divi Padel Club", "№1 - Blue", "2026-09-05", "20:00"), "booked", "free")]
    assert r["only_old"] == [("Divi Padel Club", "№1 - Blue", "2026-09-05", "21:00")] and r["only_new"] == []
