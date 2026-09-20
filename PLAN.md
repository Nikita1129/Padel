# Courtica occupancy tracker — plan

Status: **Phase 0 blocked on inputs** (see "Blocked" below). Everything under
"Findings" is verified with a tool result; everything under "Assumptions" is not.

## Goal

Every hour (06:30–23:59 Europe/Chisinau) record the state of every court slot for
today and tomorrow at each configured Courtica club, append raw rows to
`data/raw/YYYY-MM.csv` (committed to git), and publish two derived tables
(`slots_final`, `daily_occupancy`) to a Google Sheet. Adding a club = editing
`config/clubs.yaml`. Zero runtime cost, no LLM, no database.

## Findings (verified)

1. **The repository is empty.** Local clone and remote (`Nikita1129/Padel`) have no
   commits, no branches. There is no `./legacy/` directory, no actor code, no CSV
   export. Nothing about the legacy system could be read.
2. **The two club URLs were not provided** — the prompt still contains
   `[PASTE DIVI URL]` / `[PASTE URSU URL]`.
3. **This environment cannot reach courtica.md.** `curl https://courtica.md/` fails at
   the proxy with `CONNECT tunnel failed, response 403`. Per the proxy docs this is an
   organisation egress-policy denial for that host, not Courtica's bot detection.
   PyPI and GitHub are reachable (dependencies installed fine). Do not confuse this
   with the datacenter-IP block noted in the brief; both mean the same thing for us:
   discovery must run from a residential machine.
4. **Python 3.12 works here** (`/usr/bin/python3.12`, venv in `.venv/`). All allowed
   dependencies install: requests, pyyaml, gspread, google-auth, pytest, playwright.
5. `tools/discover_courtica.py` is written and verified against a local dummy site:
   it captured the page's XHR, saved `fixtures/<club>/*.json`, `page.html`,
   `screenshot.png`, `network_log.json`, and replayed the endpoint with plain
   `requests` reporting `identical_to_browser=true`.

## Unanswered Phase 0 questions (need fixtures)

- (a) Is there a JSON endpoint and does it work with plain `requests`? — unknown.
- (b) Which status values exist? — unknown.
- (c) Late in the day, can "booked" be told apart from "past"? — unknown.
- (d) Is booking type / price exposed? — unknown.
- Legacy cost bugs (170 reads/row dedup pattern; 59 CU/month) — cannot be explained
  without `./legacy/`.

## Blocked — what I need

1. Copy the old actor code and the CSV export into `./legacy/` (or push them to the
   repo) so I can summarise the extraction logic, old schema and both cost bugs.
2. The two booking-grid URLs (Divi, Ursu).
3. Run discovery on your Mac (residential IP, real Chromium) and commit or send me
   the `fixtures/` folder:

       python3.12 -m venv .venv && source .venv/bin/activate
       pip install -r requirements.txt && playwright install chromium
       python tools/discover_courtica.py \
         --club divi=<DIVI_URL> --club ursu=<URSU_URL> \
         --headed --interact 45

   While the browser is open, click "tomorrow" (and, once, run it after ~21:00 so
   question (c) can be answered from a late-day sample). The script never logs in,
   strips cookies/auth headers from what it saves, and makes at most one plain
   request per second.
4. Optional: allow `courtica.md` in this environment's network policy
   (https://code.claude.com/docs/en/claude-code-on-the-web) — then I can re-run
   discovery here, though the datacenter-IP block may still apply.

## Proposed approach (conditional on fixtures)

- **Collector** (`collector/`): `config/clubs.yaml` lists clubs with `name`,
  `url`, `courts` (expected count), `parser`. If a JSON endpoint replays with
  `requests`, the collector uses it; otherwise Playwright headless with the same
  output schema. One run = today + tomorrow per club, ≤1 req/s, hard 60 s budget.
- **Validation before any write**: zero slots, HTTP status ≠ 200, HTML where JSON
  expected, unknown status value, or court count ≠ config → exit 1, nothing written.
- **Storage**: `data/raw/YYYY-MM.csv` append-only with the raw schema from the brief;
  in-memory `set` of `(club, court, slot_date, slot_start, snapshot_ts)` keys for
  dedup within a run. Derivation script rebuilds `slots_final` and `daily_occupancy`
  from all raw files and is idempotent.
- **Sheets**: gspread + service account from `GOOGLE_SERVICE_ACCOUNT_JSON`; tabs are
  cleared and rewritten from the derived tables (never appended).
- **Scheduling**: GitHub Actions cron every hour, wide UTC window; the script exits 0
  silently when the local time is outside 06:30–23:59 Chisinau. Fallback: launchd
  plist or VPS cron if GitHub IPs are blocked.
- **Continuity**: one-off legacy CSV mapper (`source=apify_legacy`) + parity check.

## Assumptions (unverified)

- Courtica's grid is rendered by a JavaScript frontend that fetches availability as
  JSON — this is the usual pattern but is NOT confirmed for courtica.md.
- The "past" status may only exist client-side (computed from the current time),
  in which case raw rows will carry `status=past` derived by the collector and
  `raw_status` will show the site's real value; `slots_final` therefore uses the
  last observation *before* slot start, exactly as the brief specifies.
