"""Append-only raw storage: one CSV per month under data/raw/YYYY-MM.csv."""
from __future__ import annotations

import csv
from pathlib import Path

RAW_COLUMNS = ["snapshot_ts", "club", "court", "slot_date", "slot_start", "slot_end",
               "status", "raw_status", "price", "booking_type", "source"]
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


class StorageError(RuntimeError):
    pass


def month_file(snapshot_ts: str, raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / f"{snapshot_ts[:7]}.csv"


def append_rows(rows: list[dict], raw_dir: Path = RAW_DIR) -> dict[Path, int]:
    """Append rows grouped by month file. Validates every row first; writes nothing on error."""
    if not rows:
        raise StorageError("refusing to write zero rows")
    by_file: dict[Path, list[dict]] = {}
    for i, row in enumerate(rows):
        if list(row) != RAW_COLUMNS:
            raise StorageError(f"row {i} has columns {list(row)}, expected {RAW_COLUMNS}")
        if any(row[c] == "" for c in ("snapshot_ts", "club", "court", "slot_date", "slot_start", "slot_end", "status", "source")):
            raise StorageError(f"row {i} has an empty required field: {row}")
        by_file.setdefault(month_file(row["snapshot_ts"], raw_dir), []).append(row)

    raw_dir.mkdir(parents=True, exist_ok=True)
    written: dict[Path, int] = {}
    for path, group in by_file.items():
        new_file = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=RAW_COLUMNS, lineterminator="\n")
            if new_file:
                w.writeheader()
            w.writerows(group)
        written[path] = len(group)
    return written


def read_all_rows(raw_dir: Path = RAW_DIR) -> list[dict]:
    """Read every monthly file in name order. Fails on a file whose header is not the raw schema."""
    rows: list[dict] = []
    for path in sorted(raw_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as fh:
            r = csv.DictReader(fh)
            if r.fieldnames != RAW_COLUMNS:
                raise StorageError(f"{path}: header {r.fieldnames} != {RAW_COLUMNS}")
            rows.extend(r)
    return rows
