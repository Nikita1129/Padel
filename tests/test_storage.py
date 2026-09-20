import pytest

from collector.storage import RAW_COLUMNS, StorageError, append_rows, month_file, read_all_rows


def make_row(**over):
    row = {c: "" for c in RAW_COLUMNS}
    row.update({"snapshot_ts": "2026-11-03T18:00:00+02:00", "club": "Divi Padel Club", "court": "№1 - Blue",
                "slot_date": "2026-11-03", "slot_start": "19:00", "slot_end": "20:00", "status": "booked",
                "raw_status": "available=false", "source": "browser"})
    row.update(over)
    return row


def test_append_creates_monthly_files_with_header(tmp_path):
    rows = [make_row(), make_row(snapshot_ts="2026-12-01T07:00:00+02:00", slot_date="2026-12-01")]
    written = append_rows(rows, tmp_path)
    assert {p.name: n for p, n in written.items()} == {"2026-11.csv": 1, "2026-12.csv": 1}
    text = (tmp_path / "2026-11.csv").read_text()
    assert text.splitlines()[0] == ",".join(RAW_COLUMNS)
    assert len(text.splitlines()) == 2
    # second append: no second header, rows accumulate (append-only)
    append_rows([make_row(slot_start="20:00", slot_end="21:00")], tmp_path)
    lines = (tmp_path / "2026-11.csv").read_text().splitlines()
    assert len(lines) == 3 and lines.count(",".join(RAW_COLUMNS)) == 1
    assert len(read_all_rows(tmp_path)) == 3


def test_month_file_uses_snapshot_month():
    assert month_file("2027-02-28T23:30:00+02:00").name == "2027-02.csv"


def test_zero_rows_refused(tmp_path):
    with pytest.raises(StorageError):
        append_rows([], tmp_path)
    assert not list(tmp_path.glob("*.csv"))


def test_bad_row_writes_nothing(tmp_path):
    rows = [make_row(), make_row(status="")]
    with pytest.raises(StorageError, match="empty required field"):
        append_rows(rows, tmp_path)
    assert not list(tmp_path.glob("*.csv"))
    with pytest.raises(StorageError, match="columns"):
        append_rows([{"foo": "bar"}], tmp_path)
    assert not list(tmp_path.glob("*.csv"))


def test_foreign_header_rejected_on_read(tmp_path):
    (tmp_path / "2026-11.csv").write_text("a,b\n1,2\n")
    with pytest.raises(StorageError, match="header"):
        read_all_rows(tmp_path)
