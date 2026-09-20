"""Phase 0 discovery helper: capture what a Courtica booking grid loads.

Run this on a machine with a real (residential) IP, e.g. your Mac:

    python tools/discover_courtica.py \
        --club divi="https://www.courtica.md/en-MD/clubs/divi-padel?sport=padel" \
        --club ursu="https://www.courtica.md/en-MD/clubs/ursu-padel?sport=padel" \
        --with-dates

`--with-dates` visits the grid twice per club, appending `date=<today>` and
`date=<tomorrow>` (Europe/Chisinau) exactly as the legacy actor did, so no
manual clicking is needed. Output goes to fixtures/<club>/<YYYY-MM-DD>/.

For each club it:
  1. opens the booking grid in Chromium (Playwright),
  2. records every XHR/fetch request+response (URL, method, status, headers
     minus cookies/auth, post body, response body),
  3. saves JSON responses, the server-sent document (document.html, i.e. what
     plain `requests` would get), the rendered DOM (page.html), a screenshot
     and a plain `requests` GET of the page itself (page_requests.html) with a
     count of `data-available` cells in each, under fixtures/<club>/[date]/,
  4. optionally waits `--interact` seconds so you can click "tomorrow" in the
     headed browser and get those requests captured too,
  5. replays each captured JSON endpoint with plain `requests` (1 req/s) and
     records whether it answers the same without a browser.

No login, no evasion, no proxies. Output is meant to be committed to
fixtures/ so parsers can be written and unit-tested offline.
"""
from __future__ import annotations

import argparse
import datetime as dt
import zoneinfo
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
STRIP_HEADERS = {"cookie", "authorization", "set-cookie"}
CAPTURE_TYPES = {"xhr", "fetch"}
MAX_BODY_BYTES = 5_000_000


def safe_name(url: str, idx: int, method: str) -> str:
    parsed = urlparse(url)
    path = re.sub(r"[^A-Za-z0-9._-]+", "_", parsed.path.strip("/")) or "root"
    query = re.sub(r"[^A-Za-z0-9._=-]+", "_", parsed.query)[:60]
    name = f"{idx:03d}_{method}_{path}"
    if query:
        name += f"__{query}"
    return name[:150]


def clean_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in STRIP_HEADERS}


def capture_club(pw, club: str, url: str, headed: bool, interact: int, out_root: Path,
                 executable_path: str | None) -> list[dict]:
    out = out_root / club
    out.mkdir(parents=True, exist_ok=True)
    launch_kwargs = {"headless": not headed}
    if executable_path:
        launch_kwargs["executable_path"] = executable_path
    browser = pw.chromium.launch(**launch_kwargs)
    context = browser.new_context(
        user_agent=USER_AGENT, locale="ro-MD", timezone_id="Europe/Chisinau",
        viewport={"width": 1400, "height": 1000},
    )
    page = context.new_page()
    responses = []
    documents = []

    def on_response(r):
        rt = r.request.resource_type
        if rt in CAPTURE_TYPES:
            responses.append(r)
        elif rt == "document":
            documents.append(r)

    page.on("response", on_response)

    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"[{club}] opening {url}")
    page.goto(url, wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(3_000)
    if interact:
        print(f"[{club}] headed={headed}: you have {interact}s to click 'tomorrow' / scroll the grid ...")
        page.wait_for_timeout(interact * 1000)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

    rendered = page.content()
    (out / "page.html").write_text(rendered, encoding="utf-8")
    page.screenshot(path=str(out / "screenshot.png"), full_page=True)
    ssr_cells = None
    for d in documents:
        try:
            body = d.body().decode("utf-8", errors="replace")
        except Exception:
            continue
        (out / "document.html").write_text(body, encoding="utf-8")
        ssr_cells = body.count("data-available=")
        break
    print(f"[{club}] data-available cells: rendered DOM={rendered.count('data-available=')} "
          f"server document={ssr_cells}")

    log = []
    for idx, resp in enumerate(responses):
        req = resp.request
        entry = {
            "idx": idx,
            "method": req.method,
            "url": resp.url,
            "status": resp.status,
            "resource_type": req.resource_type,
            "request_headers": clean_headers(req.headers),
            "post_data": req.post_data,
            "response_headers": clean_headers(resp.headers),
            "content_type": resp.headers.get("content-type", ""),
            "saved_as": None,
            "body_len": None,
        }
        try:
            body = resp.body()
        except Exception as exc:  # redirects / aborted
            entry["body_error"] = repr(exc)
            log.append(entry)
            continue
        entry["body_len"] = len(body)
        if len(body) > MAX_BODY_BYTES:
            entry["body_error"] = "too large, not saved"
            log.append(entry)
            continue
        ctype = entry["content_type"].lower()
        text = body.decode("utf-8", errors="replace")
        is_json = "json" in ctype or text.lstrip()[:1] in "{["
        ext = ".json" if is_json else ".txt"
        fname = safe_name(resp.url, idx, req.method) + ext
        if is_json:
            try:
                (out / fname).write_text(json.dumps(json.loads(text), ensure_ascii=False, indent=2), encoding="utf-8")
            except json.JSONDecodeError:
                (out / fname).write_text(text, encoding="utf-8")
        else:
            (out / fname).write_text(text, encoding="utf-8")
        entry["saved_as"] = fname
        log.append(entry)

    context.close()
    browser.close()

    meta = {"club": club, "url": url, "captured_at": started, "user_agent": USER_AGENT,
            "rendered_data_available_cells": rendered.count("data-available="),
            "server_document_data_available_cells": ssr_cells, "requests": log}
    (out / "network_log.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{club}] captured {len(log)} xhr/fetch responses -> {out}")
    return log


def replay_with_requests(club: str, url: str, log: list[dict], out_root: Path) -> None:
    """Re-issue each captured JSON endpoint without a browser; 1 request/second."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*",
                            "Referer": url, "Accept-Language": "ro-MD,ro;q=0.9,ru;q=0.8,en;q=0.7"})
    results = []
    try:
        r = session.get(url, timeout=30)
        (out_root / club / "page_requests.html").write_text(r.text, encoding="utf-8")
        results.append({"idx": -1, "url": url, "method": "GET", "status": r.status_code,
                        "content_type": r.headers.get("content-type", ""), "body_len": len(r.content),
                        "data_available_cells": r.text.count("data-available="), "body_head": r.text[:300]})
        print(f"[{club}] requests GET page -> {r.status_code}, data-available cells={r.text.count('data-available=')}")
    except Exception as exc:
        results.append({"idx": -1, "url": url, "error": repr(exc)})
        print(f"[{club}] requests GET page -> ERROR {exc!r}")
    for entry in log:
        if not (entry.get("saved_as") or "").endswith(".json"):
            continue
        time.sleep(1.0)
        headers = {}
        for k in ("content-type", "accept", "x-requested-with", "origin", "apikey",
                  "x-client-info", "accept-profile", "content-profile", "prefer", "next-action",
                  "next-router-state-tree", "rsc"):
            if k in entry["request_headers"]:
                headers[k] = entry["request_headers"][k]
        try:
            if entry["method"] == "GET":
                r = session.get(entry["url"], headers=headers, timeout=30)
            else:
                r = session.request(entry["method"], entry["url"], data=entry.get("post_data"), headers=headers, timeout=30)
            same = None
            saved = (out_root / club / entry["saved_as"]).read_text(encoding="utf-8")
            try:
                same = json.loads(r.text) == json.loads(saved)
            except Exception:
                same = False
            results.append({"idx": entry["idx"], "url": entry["url"], "method": entry["method"],
                            "status": r.status_code, "content_type": r.headers.get("content-type", ""),
                            "body_len": len(r.content), "identical_to_browser": same,
                            "body_head": r.text[:300]})
            print(f"[{club}] requests {entry['method']} {entry['url'][:90]} -> {r.status_code} identical={same}")
        except Exception as exc:
            results.append({"idx": entry["idx"], "url": entry["url"], "error": repr(exc)})
            print(f"[{club}] requests {entry['url'][:90]} -> ERROR {exc!r}")
    (out_root / club / "requests_replay.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--club", action="append", required=True, metavar="NAME=URL",
                    help="club slug and booking grid URL; repeatable")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--interact", type=int, default=0, help="seconds to wait for manual clicks (headed)")
    ap.add_argument("--out", default="fixtures", help="output directory (default: fixtures)")
    ap.add_argument("--with-dates", action="store_true",
                    help="visit ?date=<today> and ?date=<tomorrow> (Europe/Chisinau) instead of the bare URL")
    ap.add_argument("--no-replay", action="store_true", help="skip the plain-requests replay")
    ap.add_argument("--executable-path", default=None, help="Chromium binary override (rarely needed)")
    args = ap.parse_args()

    clubs = []
    for item in args.club:
        if "=" not in item:
            ap.error(f"--club expects NAME=URL, got {item!r}")
        name, url = item.split("=", 1)
        name, url = name.strip(), url.strip()
        if args.with_dates:
            today = dt.datetime.now(zoneinfo.ZoneInfo("Europe/Chisinau")).date()
            for day in (today, today + dt.timedelta(days=1)):
                sep = "&" if "?" in url else "?"
                clubs.append((f"{name}/{day.isoformat()}", f"{url}{sep}date={day.isoformat()}"))
        else:
            clubs.append((name, url))

    out_root = Path(args.out)
    with sync_playwright() as pw:
        for name, url in clubs:
            log = capture_club(pw, name, url, args.headed, args.interact, out_root, args.executable_path)
            if not args.no_replay:
                replay_with_requests(name, url, log, out_root)
            time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
