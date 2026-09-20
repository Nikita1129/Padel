"""PadelPoint adapter (padelpoint.md/booking/ro), ported from the legacy actor.

The page is a single-page app: the day is chosen in an <input type="date">,
each court is opened by clicking its label (C1..C9) on a map, and the slots are
<button> elements whose text is "HH:MM\\nAlege" (free), "HH:MM\\nOcupat"
(booked) or "HH:MM\\nExpirat" (already started: unlike Courtica, this site
does tell past from booked). The "23:00\\nSfarsit" button is the closing
marker, not a slot.
Everything needs a browser; nothing is server-rendered.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .config import Club
from .parse import ParseError, Slot, build_slots

SLOT_TEXT_RE = re.compile(r"^(\d{1,2}:\d{2})\s*\n?\s*(.*)$", re.S)
STATE_BY_TEXT = {"alege": "free", "ocupat": "booked", "expirat": "past"}
WAIT_AFTER_LOAD_MS = 6_000
WAIT_AFTER_DATE_MS = 2_500
WAIT_AFTER_CLICK_MS = 1_100
MAX_CLICK_LEVELS = 7

JS_HAS_SLOTS = """() => [...document.querySelectorAll('button')]
    .some(b => /^\\d{1,2}:\\d{2}/.test((b.innerText || '').trim()))"""
JS_BACK_TO_MAP = """() => {
    const b = [...document.querySelectorAll('button')]
        .find(x => /napoi la hart/i.test(x.getAttribute('aria-label') || ''));
    if (b) { b.click(); return true; }
    return false;
}"""
JS_SET_DATE = """(d) => {
    const el = document.querySelector('input[type=date]');
    if (!el) return null;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, d);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value;
}"""
JS_CLICK_COURT = """([idx, lvl]) => {
    const lbl = [...document.querySelectorAll('*')]
        .find(e => e.children.length === 0 && (e.textContent || '').trim() === `C${idx}`);
    if (!lbl) return false;
    let el = lbl;
    for (let k = 0; k < lvl && el.parentElement; k++) el = el.parentElement;
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    return true;
}"""
JS_READ = """() => {
    const btns = [...document.querySelectorAll('button')]
        .filter(b => /^\\d{1,2}:\\d{2}/.test((b.innerText || '').trim()));
    const cap = [...document.querySelectorAll('*')]
        .find(e => e.children.length === 0 && /^Court\\s+\\d+$/i.test((e.textContent || '').trim()));
    return { court: cap ? cap.textContent.trim() : null,
             buttons: btns.map(b => [(b.innerText || '').trim(), !!b.disabled]) };
}"""


def slots_from_buttons(club: Club, courts: dict[str, list[tuple[str, bool]]], slot_date: dt.date,
                       now: dt.datetime) -> list[Slot]:
    """Pure part: court name -> [(button text, disabled)] into validated slots."""
    normalised: dict[str, list[tuple[str, str, str]]] = {}
    for court, buttons in courts.items():
        out = []
        for text, disabled in buttons:
            m = SLOT_TEXT_RE.match(text)
            if not m:
                raise ParseError(f"{club.slug} {slot_date} {court}: unexpected button text {text!r}")
            time_txt, state_txt = m.group(1), m.group(2).strip()
            if state_txt.lower().startswith("sf"):
                continue  # "Sfarsit": closing marker, not a slot
            if len(time_txt) == 4:
                time_txt = "0" + time_txt
            status = STATE_BY_TEXT.get(state_txt.lower(), "unknown")
            out.append((time_txt, status, f"{state_txt};disabled={str(disabled).lower()}"))
        normalised[court] = out
    return build_slots(club, normalised, slot_date, now)


def _wait(page, ms: int) -> None:
    page.wait_for_timeout(ms)


def collect_padelpoint(club: Club, slot_date: dt.date, now: dt.datetime, browser, save_dir: Path | None = None,
                       log=print) -> list[Slot]:
    """Drive the booking page for one day and return its slots (or raise)."""
    page = browser.new_page()
    try:
        page.goto(club.url, wait_until="networkidle", timeout=60_000)
        _wait(page, WAIT_AFTER_LOAD_MS)
        value = page.evaluate(JS_SET_DATE, slot_date.isoformat())
        if value is None:
            raise ParseError(f"{club.slug} {slot_date}: date input not found on {club.url}")
        _wait(page, WAIT_AFTER_DATE_MS)
        shown = page.evaluate("() => (document.querySelector('input[type=date]') || {}).value")
        if shown != slot_date.isoformat():
            raise ParseError(f"{club.slug}: date input shows {shown!r} after selecting {slot_date}")

        courts: dict[str, list[tuple[str, bool]]] = {}
        for n in range(1, club.expected_courts + 1):
            if page.evaluate(JS_HAS_SLOTS):
                page.evaluate(JS_BACK_TO_MAP)
                _wait(page, WAIT_AFTER_CLICK_MS)
            opened = None
            for lvl in range(MAX_CLICK_LEVELS):
                if not page.evaluate(JS_CLICK_COURT, [n, lvl]):
                    raise ParseError(f"{club.slug} {slot_date}: court label C{n} not found on the map")
                _wait(page, WAIT_AFTER_CLICK_MS)
                data = page.evaluate(JS_READ)
                if data["buttons"] and data["court"] and data["court"].lower() == f"court {n}":
                    opened = data
                    break
            if opened is None:
                raise ParseError(f"{club.slug} {slot_date}: could not open court C{n}")
            if save_dir is not None:
                save_dir.mkdir(parents=True, exist_ok=True)
                (save_dir / f"{slot_date.isoformat()}_browser_C{n}.html").write_text(page.content(), encoding="utf-8")
            courts[opened["court"]] = [(t, bool(d)) for t, d in opened["buttons"]]
            log(f"[{club.slug}] {slot_date} {opened['court']}: {len(opened['buttons'])} buttons")
        return slots_from_buttons(club, courts, slot_date, now)
    finally:
        page.close()
