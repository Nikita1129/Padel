# Courtica occupancy tracker — plan

Status: **all phases implemented.** Real pages were captured by the first GitHub
Actions run (2026-09-20 12:48 local) and are in `fixtures/real/`; the parser is
tested against them. Remaining: the Google secret in GitHub, the first non-dry run,
and switching the default branch to `main`.

## Phase 0 answers (verified on fixtures/real/, captured from GitHub Actions)

- (a) **No JSON endpoint is needed: the grid is server-rendered.** A plain `requests`
  GET from a GitHub runner returned the full grid for both clubs and both days in
  ~1 s per page (`source=html`). No browser, no proxy. Playwright stays as an
  automatic fallback only.
- (b) Per-cell status values: `data-available` ∈ {true, false} and `data-pending`
  ∈ {false} (no `true` seen yet). Both are kept verbatim in `raw_status`.
- (c) Late in the day a started slot and a booked one are both `data-available=false`;
  the site does not distinguish them. The collector labels `past` by time, and
  `slots_final` only uses observations made before the slot started, so the
  ambiguity never reaches the derived tables.
- (d) Price and booking type are **not exposed** per slot; the words appear only in
  the app's translation strings. Both columns stay empty.
- `?date=YYYY-MM-DD` selects the day: today's and tomorrow's captures differ.
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
2. **Legacy data received** (JSON exports, in `legacy/data/`): 270 runs every 30 min
   over 2026-09-02..09-10, 4 clubs, real court names and slot grids for Divi and
   Ursu, 1078/1080 club-runs OK **through Apify datacenter proxies with a headless
   browser** — so a real browser from a datacenter IP was not blocked.
3. **This environment cannot reach courtica.md**: `curl` gets `CONNECT tunnel
   failed, 403` from the egress proxy and WebFetch returns `EGRESS_BLOCKED` —
   organisation network policy, not Courtica. PyPI/GitHub work.
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

## How to get real fixtures without the user running anything

Proposed: Phase 1 builds the parser against **synthetic fixtures that reproduce
the DOM documented in the legacy actor** (`td[data-time][data-available]`, `<th>`
court names), populated with the real court names and slot grids from the export.
The collector gets a `--save-html DIR` flag. The first GitHub Actions
`workflow_dispatch` run (Phase 3, triggered by me through the GitHub tool) saves
the real HTML as a workflow artifact; I commit it to `fixtures/` and re-run the
parser tests against it before any scheduled run writes data. If GitHub's IPs are
blocked, the fallback (launchd / VPS) is produced as the spec already requires.

Optional shortcut: allowing `courtica.md` in this environment's network policy
(claude.ai/code → environment settings → network access, see
https://code.claude.com/docs/en/claude-code-on-the-web) would let me capture real
fixtures right away with `tools/discover_courtica.py`.

## Proposed approach (conditional on fixtures)

- **Collector** (`collector/`): `config/clubs.yaml` lists clubs with `name`, `slug`,
  `url`, `expected_courts`, `slot_minutes`. Strategy order, decided by fixtures:
  (1) plain `requests` GET of the page; if the HTML contains the grid cells, parse
  them (no browser); (2) otherwise Playwright headless (Chromium) renders the
  page and the same HTML parser runs on the DOM. Both paths share one parser.
  The legacy evidence makes (2) the expected path; (1) costs one request and
  self-discovers a server-rendered grid if Courtica ever exposes one.
  One run = today + tomorrow per club, ≤1 req/s, hard 60 s budget.
- **Validation before any write**: HTTP ≠ 200, zero slots, court count ≠
  `expected_courts`, unknown status value, or unexpected response shape → exit 1,
  nothing written.
- **Raw schema** exactly as specified, with one question: `source` is specified as
  `api | browser`; a plain-HTML GET is neither. Proposal: `html` for that path,
  `browser` for Playwright, `api` reserved for a JSON endpoint. **Needs approval.** `status` mapping from the DOM:
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
