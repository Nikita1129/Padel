"""Parse a Courtica booking grid (HTML) into slot observations.

Grid structure (real pages captured 2026-09-20, see fixtures/real/; the grid is
server-rendered, so a plain GET is enough):

    <tr><th scope="row">№1 - Blue</th>
        <td data-court="<uuid>" data-time="07:00" data-available="false" data-pending="false">07:00</td>
        <td data-court="<uuid>" data-time="08:00" data-available="true"  data-pending="false">08:00</td> …</tr>

data-available="true"  -> free
data-available="false" -> not bookable: booked, blocked OR already started.
The site does not distinguish those three; this parser labels a "false" cell
`past` when its start time is <= now and `booked` otherwise. data-pending is
kept verbatim in raw_status (always "false" so far). No price or booking type
is exposed per cell.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import re
from html.parser import HTMLParser

from .config import Club

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
STATUS_BY_RAW = {"true": "free", "false": "booked"}  # "booked" becomes "past" once started


class ParseError(ValueError):
    """Raised when the page does not look like the expected booking grid."""


@dataclasses.dataclass(frozen=True)
class Slot:
    club: str
    court: str
    slot_date: dt.date
    slot_start: dt.time
    slot_end: dt.time
    status: str        # free | booked | blocked | past | unknown
    raw_status: str    # literal attribute value, e.g. data-available=false
    price: str = ""
    booking_type: str = ""


@dataclasses.dataclass
class _Row:
    court_parts: list[str] = dataclasses.field(default_factory=list)
    cells: list[tuple[str, str, str]] = dataclasses.field(default_factory=list)  # (data-time, data-available, data-pending)


class _GridParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_Row] = []
        self._row: _Row | None = None
        self._in_th = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = _Row()
        elif tag == "th" and self._row is not None:
            self._in_th = True
        elif tag == "td" and self._row is not None:
            a = dict(attrs)
            if "data-time" in a and "data-available" in a:
                self._row.cells.append((a["data-time"] or "", a["data-available"] or "", a.get("data-pending") or ""))

    def handle_endtag(self, tag):
        if tag == "th":
            self._in_th = False
        elif tag == "tr" and self._row is not None:
            if self._row.cells:
                self.rows.append(self._row)
            self._row = None
            self._in_th = False

    def handle_data(self, data):
        if self._in_th and self._row is not None:
            self._row.court_parts.append(data)


def build_slots(club: Club, courts: dict[str, list[tuple[str, str, str]]], slot_date: dt.date,
                now: dt.datetime) -> list[Slot]:
    """Shared validation for every platform.

    `courts` maps court name -> list of (HH:MM, status, raw_status) where status is
    free | booked | blocked | past | unknown as reported by the site; booked cells
    whose start is <= now become past. Raises ParseError on any inconsistency.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not courts:
        raise ParseError(f"{club.slug} {slot_date}: no courts")
    if len(courts) != club.expected_courts:
        raise ParseError(
            f"{club.slug} {slot_date}: expected {club.expected_courts} courts, found {len(courts)}: {sorted(courts)}"
        )
    step = dt.timedelta(minutes=club.slot_minutes)
    slots: list[Slot] = []
    for court, cells in courts.items():
        if not court:
            raise ParseError(f"{club.slug} {slot_date}: a court with {len(cells)} slots has no name")
        if not cells:
            raise ParseError(f"{club.slug} {slot_date} {court}: no slots")
        times: list[dt.time] = []
        for raw_time, status, raw_status in cells:
            if not TIME_RE.match(raw_time):
                raise ParseError(f"{club.slug} {slot_date} {court}: bad time {raw_time!r}")
            if status not in ("free", "booked", "blocked", "past", "unknown"):
                raise ParseError(f"{club.slug} {slot_date} {court}: unknown status {status!r}")
            if status == "unknown":
                raise ParseError(f"{club.slug} {slot_date} {court} {raw_time}: unrecognised state {raw_status!r}")
            start = dt.time.fromisoformat(raw_time)
            start_dt = dt.datetime.combine(slot_date, start, tzinfo=now.tzinfo)
            if status == "booked" and start_dt <= now:
                status = "past"
            end_dt = start_dt + step
            if end_dt.date() != slot_date and end_dt.time() != dt.time(0, 0):
                raise ParseError(f"{club.slug} {slot_date} {court}: slot {raw_time} + {club.slot_minutes} min crosses midnight")
            times.append(start)
            slots.append(Slot(club=club.name, court=court, slot_date=slot_date, slot_start=start,
                              slot_end=end_dt.time(), status=status, raw_status=raw_status))
        if len(set(times)) != len(times):
            raise ParseError(f"{club.slug} {slot_date} {court}: duplicate slot times")
        ordered = sorted(dt.datetime.combine(slot_date, t) for t in times)
        gaps = {int((b - a).total_seconds() // 60) for a, b in zip(ordered, ordered[1:])}
        if gaps and min(gaps) != club.slot_minutes:
            raise ParseError(
                f"{club.slug} {slot_date} {court}: grid step is {min(gaps)} min, config says {club.slot_minutes}"
            )
    return slots


def has_grid(html: str) -> bool:
    """Cheap check used to decide whether a plain GET already contains the grid."""
    return "data-available=" in html


def parse_grid(html: str, club: Club, slot_date: dt.date, now: dt.datetime) -> list[Slot]:
    """Return one Slot per grid cell, or raise ParseError. Never returns an empty list."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    p = _GridParser()
    p.feed(html)
    p.close()
    if not p.rows:
        raise ParseError(f"{club.slug} {slot_date}: no grid cells (td[data-time][data-available]) found")

    courts: dict[str, list[tuple[str, str, str]]] = {}
    for row in p.rows:
        court = " ".join(" ".join(row.court_parts).split())
        if not court:
            raise ParseError(f"{club.slug} {slot_date}: a row with {len(row.cells)} cells has no <th> court name")
        if court in courts:
            raise ParseError(f"{club.slug} {slot_date}: court {court!r} appears twice")
        courts[court] = row.cells

    normalised: dict[str, list[tuple[str, str, str]]] = {}
    for court, cells in courts.items():
        out = []
        for raw_time, raw_avail, raw_pending in cells:
            status = STATUS_BY_RAW.get(raw_avail.strip().lower())
            if status is None:
                raise ParseError(f"{club.slug} {slot_date} {court}: unknown data-available value {raw_avail!r}")
            if raw_pending not in ("", "true", "false"):
                raise ParseError(f"{club.slug} {slot_date} {court}: unknown data-pending value {raw_pending!r}")
            raw_status = f"available={raw_avail}" + (f";pending={raw_pending}" if raw_pending else "")
            out.append((raw_time, status, raw_status))
        normalised[court] = out
    return build_slots(club, normalised, slot_date, now)
