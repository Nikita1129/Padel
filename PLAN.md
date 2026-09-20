# Courtica occupancy tracker — plan

Status: **Phase 0 in progress** — legacy code analysed; still waiting for the
legacy CSV export and for real fixtures (this environment cannot reach courtica.md).
Everything under "Findings" is verified with a tool result; "Assumptions" are not.

## Goal

Every hour (06:30–23:59 Europe/Chisinau) record the state of every court slot for
today and tomorrow at each configured Courtica club, append raw rows to
`data/raw/YYYY-MM.csv` (committed to git), and publish two derived tables
(`slots_final`, `daily_occupancy`) to a Google Sheet. Adding a club = editing
`config/clubs.yaml`. Zero runtime cost, no LLM, no database.

## Findings (verified)

1. **Legacy code read** — see `legacy/README.md` for the extraction logic, old
   schema, and both cost bugs. Key facts reused here:
   - URLs: `https://www.courtica.md/en-MD/clubs/divi-padel?sport=padel` and
     `.../clubs/ursu-padel?sport=padel`; the day is selected with `&date=YYYY-MM-DD`.
   - Grid = `<td data-time="HH:MM" data-available="true|false">`, court name in the
     row `<th>`. Divi: 4 courts × 16 one-hour slots; Ursu: 1 court × 28 half-hour slots.
   - `data-available="false"` covers booked, blocked **and past** slots; the DOM
     cannot tell them apart (legacy comment, calibrated 2026-08-20).
   - Site stack per legacy comment: Next.js + Supabase.
2. **The legacy CSV export is missing** — the zip had only source code.
3. **This environment cannot reach courtica.md** (`CONNECT tunnel failed, 403` from
   the egress proxy = organisation network policy, not Courtica). PyPI/GitHub work.
4. Python 3.12 + all allowed dependencies install in `.venv/`.
5. `tools/discover_courtica.py` verified offline against a local dummy site: it
   captures XHR/fetch bodies, the server-sent document, the rendered DOM, a
   screenshot, then replays the page and every JSON endpoint with plain `requests`
   and reports whether the result is identical. `--with-dates` visits today and
   tomorrow automatically.

## Phase 0 questions — current state

- (a) JSON endpoint / plain `requests`? **Unknown.** Two candidates the script tests:
  the grid may be server-rendered (then `requests` + HTML parse of
  `data-available` works, no browser), or fetched from Supabase REST (then the
  public anon key appears as an `apikey` header and `requests` may work).
- (b) Status values: from the DOM only `true|false` exist. Whether a JSON endpoint
  exposes richer states (blocked/training/tournament) is **unknown**.
- (c) Booked vs past late in the day: **from the DOM, no** (legacy evidence). The
  design does not need it: `slots_final` takes the last observation *before* slot
  start, and the collector labels `status=past` itself when `slot_start <= now`.
  A JSON endpoint could improve this — unknown until fixtures.
- (d) Price / booking type: **not exposed in the DOM** (legacy never saw them);
  unknown for JSON.

## Blocked — what I need

1. The **legacy CSV export** of `padel-ocupare` into `legacy/` (needed for Phase 4
   mapping and parity, and to confirm the schema above with real rows).
2. Run discovery on your Mac (residential IP) and commit/send `fixtures/`:

       python3.12 -m venv .venv && source .venv/bin/activate
       pip install -r requirements.txt && playwright install chromium
       python tools/discover_courtica.py \
         --club divi="https://www.courtica.md/en-MD/clubs/divi-padel?sport=padel" \
         --club ursu="https://www.courtica.md/en-MD/clubs/ursu-padel?sport=padel" \
         --with-dates

   Run it once in the evening (after ~21:00) so today's fixture contains past
   slots next to booked ones. No login, no clicking needed, ≤1 request/s.
3. From the Apify console, for the 59 CU question: the schedule frequency and the
   run memory (MB) — a screenshot is enough.

## Proposed approach (conditional on fixtures)

- **Collector** (`collector/`): `config/clubs.yaml` lists clubs with `name`, `slug`,
  `url`, `expected_courts`, `slot_minutes`. Strategy order, decided by fixtures:
  (1) plain `requests` GET of the page if the grid is server-rendered → parse
  `data-available` cells; (2) plain `requests` to a JSON endpoint if one exists
  and replays; (3) Playwright headless fallback, same output schema.
  One run = today + tomorrow per club, ≤1 req/s, hard 60 s budget.
- **Validation before any write**: HTTP ≠ 200, zero slots, court count ≠
  `expected_courts`, unknown status value, or unexpected response shape → exit 1,
  nothing written.
- **Raw schema** exactly as specified. `status` mapping from the DOM:
  `true → free`; `false` and `slot_start > now → booked`; `false` and
  `slot_start <= now → past`; anything else → `unknown` (and the run fails if any
  `unknown` appears). `raw_status` = the literal attribute value.
- **Storage**: append to `data/raw/YYYY-MM.csv`; in-memory `set` of
  `(snapshot_ts, club, court, slot_date, slot_start)` for dedup within the run.
  Derivation script rebuilds `slots_final` and `daily_occupancy` from all raw files;
  idempotent.
- **Sheets**: gspread + service account from `GOOGLE_SERVICE_ACCOUNT_JSON`; both
  tabs cleared and rewritten from the derived tables (never appended).
- **Scheduling**: GitHub Actions cron hourly at :05 UTC over a wide window; the
  script exits 0 without touching anything when the Chisinau local time is outside
  06:30–23:59. Fallback: launchd plist / VPS cron if GitHub IPs are blocked.
- **Continuity**: legacy CSV mapper (`source=apify_legacy`) + parity check.

## Assumptions (unverified)

- The `?date=` query parameter still selects the day (legacy used it a month ago).
- The grid may be server-rendered by Next.js; if so no browser and no JSON endpoint
  are needed. Not confirmed until `document.html` / `page_requests.html` show
  `data-available` cells.
- The datacenter-IP block seen by the legacy actor also applies to GitHub Actions
  runners; to be tested in Phase 3.
