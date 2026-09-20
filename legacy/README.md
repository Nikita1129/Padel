# Legacy system: Apify actor (Puppeteer) — analysis

Source: `legacy/actor/` (unzipped from the upload on 2026-09-20). The zip
contained only the actor code; **the CSV export of collected data is not here yet**.

## What it did (src/main.js)

1. One run = for each `dateOffsets` entry (default `[0]` = today only) × each club
   in `clubs` (default list has **4 clubs**: divi-padel, ursu-padel, padelpoint,
   primus-padel-costesti), open a new Puppeteer page through Apify DATACENTER proxy
   (country RO), viewport 2400×1500.
2. Courtica URL pattern: `https://www.courtica.md/en-MD/clubs/<slug>?sport=padel&date=YYYY-MM-DD`.
   Waits `networkidle2` (90 s timeout), then `td[data-time][data-available]`
   (30 s timeout), then a fixed `renderWaitMs` = 6 s sleep, then 0.8 s more.
3. Takes a full-page JPEG screenshot of every club/date and stores it in a named
   key-value store (`padel-screenshots`, key `<slug>-<date>-<HHMM>`).
4. Parses the grid from the DOM:
   - one `<td data-time="HH:MM" data-available="true|false">` per slot;
   - court name = the row's `<th>` text;
   - `data-available="false"` → `ocupat=1`; `"true"` → `ocupat=0`;
   - slot length = smallest gap between consecutive times of the first court;
   - `temp_max` = largest `NN°` text found inside the table.
   Header comment: verified 64/64 cells on Divi (4 courts × 16 one-hour slots) and
   28/28 on Ursu (1 court × 28 half-hour slots). `disabled`/`aria-disabled`/CSS are
   identical for booked and free — only `data-available` works.
5. **Past-slot trap (documented in the code):** Courtica sets
   `data-available="false"` for every slot whose start time has passed, so a past
   slot is indistinguishable from a booked one in the DOM. The actor adds
   `viitor=1` when `slot start > now` and only trusts `viitor=1` rows.
6. Pushes all slot rows to dataset `padel-ocupare` and one summary row per
   (run, club) to `padel-rezumat`. Optional POST of all rows to a Google Apps
   Script webhook (append).
7. Re-reads the whole `padel-ocupare` history (up to 400 000 rows) to rebuild
   two CSVs in the key-value store (`terenuri.csv` per court/day, `zilnic.csv`
   per club/day split at 14:00), then runs a coverage check against the last 7
   days and calls `Actor.fail()` if any club has 0 slots or fewer courts than its
   7-day record.

## Old dataset schema (`padel-ocupare`, one row per slot per run)

| field | meaning |
|---|---|
| runId | Apify run id |
| data | slot date YYYY-MM-DD (Europe/Chisinau) |
| zi | Romanian weekday name |
| club | club display name (`nume`), e.g. "Divi Padel Club" |
| teren | court label from the grid `<th>` |
| ora | slot start HH:MM |
| interval | `ZI` if hour < 17 else `SEARA` |
| durata_ore | slot length in hours (inferred, fallback config) |
| ocupat | 1 = `data-available="false"` (booked OR blocked OR past), 0 = free |
| ore_ocupate | `durata_ore` if ocupat else 0 |
| viitor | 1 = slot start was still in the future at collection time |
| temp_max | max temperature shown in the grid, or null |
| poza | screenshot key |
| status | `OK` or `PARSE FAILED` (sentinel row with null slot fields) |
| eroare | error text on PARSE FAILED rows |
| colectat_la | collection timestamp, ISO UTC |

Mapping to the new raw schema (Phase 4): `snapshot_ts ← colectat_la` (convert to
Europe/Chisinau), `club ← club`, `court ← teren`, `slot_date ← data`,
`slot_start ← ora`, `slot_end ← ora + durata_ore`, `status ← booked if ocupat=1 and
viitor=1; free if ocupat=0; past if ocupat=1 and viitor=0`, `raw_status ←
data-available=false|true` (reconstructed, marked as such), `price` and
`booking_type` empty (never exposed), `source = apify_legacy`. PARSE FAILED rows
are dropped (they carry no slot).

## Cost bug 1 — ~170 dataset reads per row written

`src/main.js` lines 426–437: **every run** calls `dataset.getInfo()` and then
`dataset.getData({ offset, limit: 400000, clean: true })`, i.e. it reads the entire
`padel-ocupare` history (capped at 400 k rows) to rebuild `terenuri.csv` /
`zilnic.csv` and to run the 7-day coverage check. It is not a per-push dedup (there
is no dedup at all — `pushData(toateRandurile)` is a single unconditional write),
but the cost shape is the same: reads per run = dataset size, so total reads grow
quadratically with time. The new design computes derived tables from local CSVs
in git, so remote reads are zero.

## Cost bug 2 — 59 compute units / month

Visible in code/config (verified):

- **Four clubs by default**, not two: `DEFAULT_CLUBS` and the input `prefill` both
  list divi, ursu, padelpoint and primus. The PadelPoint adapter alone clicks
  through 9 courts sequentially with fixed sleeps (1.1 s × up to 7 attempts per
  court + 1.2 s + 2.5 s), i.e. tens of seconds of browser time per run for a club
  you are not tracking.
- **Fixed waits stack up per club/date**: `networkidle2` up to 90 s +
  `waitForSelector` up to 30 s + 6 s `renderWaitMs` + 0.8 s. A blocked or slow
  page burns ~2 minutes of browser time per club before failing.
- **Heavy browser work per run**: 2400×1500 viewport, full-page JPEG screenshot per
  club/date, full-history dataset read (bug 1), `apify/actor-node-puppeteer-chrome`
  image (full Chrome).
- **`Actor.fail()` on incomplete coverage** makes any partially blocked run count
  as a failed run while still consuming its full duration.

Not visible in the code (assumptions, need your Apify console): the schedule's
frequency and the run memory setting. Compute units = GB × hours, so a 4 GB
default with ~2 min runs, hourly, is ≈ 60 CU/month — consistent with 59, but I
cannot confirm without the run list.

Also present: `routes.js` and `test/main.test.js` are untouched template
boilerplate (they crawl apify.com); they are not used by `main.js`.
