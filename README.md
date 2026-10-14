# Padel occupancy tracker

Colectează, la fiecare oră (06:30–23:59, ora Chișinăului), starea fiecărui slot de
teren pentru azi și mâine de pe grilele publice Courtica, o păstrează brut în
`data/raw/AAAA-LL.csv` (append-only, commit automat în repo) și publică două tabele
derivate într-un Google Sheet: `dashboard` (un rând per club: cât a vândut în toată
perioada urmărită, doar zilele observate complet), `slots_final` (ultima stare observată înainte de
începerea slotului + când a fost văzut prima dată rezervat) și `daily_occupancy`
(ocupare pe zi și club, împărțită dimineață / după-amiază / seară, plus
`revenue_estimate_mdl` = ore-teren rezervate × prețul orei și `released_pct` = cât din
ce arăta site-ul rezervat seara trecută a dispărut până la start). Prețul e cel afișat de
site pentru slotul respectiv, apoi cel afișat pentru aceeași oră la același club, iar
unde site-ul nu arată niciun preț se folosește `price_per_hour` din config —
`site_priced_pct` spune cât din ore a fost evaluat cu preț real. Rămâne o estimare,
iar pe Courtica „rezervat" include și blocările.

Rulează pe GitHub Actions (`.github/workflows/collect.yml`), fără servicii plătite.
Cluburile Courtica (Divi, Ursu, Primus) se citesc dintr-un GET simplu, grila vine în
HTML. PadelPoint are altă platformă: un browser headless deschide fiecare teren prin
click pe hartă (`collector/padelpoint.py`), de aceea rularea durează 1–2 minute.

Cron-ul din GitHub Actions e best-effort (s-a observat că pornește o dată la 4–8
ore, nu orar), deci cadența reală se ține cu un cron extern care apelează
`workflow_dispatch`: vezi [`docs/cron-independent.md`](docs/cron-independent.md).

## Cum adaugi un club

1. Deschide grila clubului pe courtica.md și copiază URL-ul (fără `&date=`).
2. Adaugă o intrare în `config/clubs.yaml`:
   ```yaml
   - slug: ipadel            # id scurt, fără spații
     name: iPadel            # exact cum vrei să apară în coloana club
     url: https://www.courtica.md/en-MD/clubs/ipadel?sport=padel
     expected_courts: 3      # rularea eșuează dacă grila arată alt număr
     slot_minutes: 60        # rularea eșuează dacă pasul grilei diferă
     fetch: auto
   ```
3. Commit. Următoarea rulare programată îl include. Valabil pentru orice club de pe
   Courtica. Un club de pe altă platformă are nevoie de un adaptor nou în `collector/`
   (vezi `padelpoint.py` ca model) și de `platform:` în config. Dacă nu știi numărul de terenuri
   sau pasul, pornește o rulare manuală în dry-run (vezi mai jos): mesajul de eroare
   spune ce a găsit în grilă.

## Cum refaci derivările

Tabelele derivate se reconstruiesc integral din `data/raw/` la fiecare rulare, deci
sunt idempotente. Manual, cu secretul în mediu:

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat cheia.json)"
python -m collector.derive            # doar afișează dimensiunile
python -m collector.derive --push     # rescrie ambele tab-uri din Sheet
```

Fără acces la Sheet: `python -m collector.derive` arată ultimele rânduri din
`daily_occupancy` pe stderr.

## Ce faci când o rulare eșuează

Un club care eșuează nu mai costă snapshot-ul celorlalte: ce s-a colectat se scrie,
clubul căzut lipsește din acel snapshot, iar rularea iese 1 cu `PARTIAL RUN` în log
(deci primești emailul de workflow failure). Un rând scris e întotdeauna complet;
nu apar rânduri goale sau parțiale. Doar o rulare care n-a colectat absolut nimic
nu scrie nimic.

1. GitHub → Actions → `collect` → rularea roșie → pasul **Collect**. Liniile `FAILED:`
   (și rezumatul `PARTIAL RUN`, sau `RUN FAILED` dacă n-a rămas nimic) spun care club
   și ce cauză:
   - `no grid cells` / `HTTP 403` / `browser fetch failed`: Courtica a blocat sau a
     schimbat pagina. Pornește o rulare manuală cu `commit_html` bifat; HTML-ul ajunge
     în `fixtures/real/` și se poate compara cu `fixtures/real/*` mai vechi.
   - `expected N courts, found M` / `grid step is X min`: clubul și-a schimbat grila.
     Actualizează `expected_courts` sau `slot_minutes` în `config/clubs.yaml`.
   - `unknown data-available value`: site-ul a introdus o stare nouă. Parserul din
     `collector/parse.py` trebuie extins; până atunci nimic nu se scrie.
   - `run budget of 60s exceeded`: site lent. Dacă se repetă, verifică pasul de fetch.
2. `SHEETS PUSH FAILED (raw rows were appended and are kept)`: datele brute sunt în
   repo; rezolvă cauza (secret expirat, Sheet nepartajat cu contul de serviciu, cotă)
   și rulează `python -m collector.derive --push`.
3. Rulare manuală: Actions → `collect` → Run workflow. `dry_run` bifat = doar
   afișează; debifat = scrie și publică. `ignore_window` bifat = rulează și în afara
   ferestrei orare.
4. O oră lipsă nu strică nimic: `slots_final` folosește ultima observație dinaintea
   startului, oricare ar fi ea. Zilele fără nicio observație nu apar deloc. La fel și
   un club care lipsește dintr-un snapshot: pierzi o observație, nu slotul.
5. `dashboard` numără doar zilele **încheiate** și complet observate. Azi și mâine au
   grila completă dar rezervările încă nu au venit, deci ar trage ocuparea în jos.
   „Complet observată" se măsoară în **ore-teren**, nu în sloturi: `total_hours` =
   terenuri × ore de program, deci nu se mișcă atunci când se schimbă pasul grilei
   (PadelPoint a trecut de la 288 sloturi de 30 min la 144 de o oră pe 2026-10-10 și a
   rămas la 144 ore-teren pe zi). Ziua intră dacă orele oferite ajung la cât oferă
   clubul **de obicei în jurul acelei date** — nu la maximul lui istoric: când
   PadelPoint a adăugat slotul 23:00–24:00 pe 2026-10-13 (144 → 153 ore-teren), un
   maxim pe club ar fi declarat „incomplete" toate zilele dinainte și ar fi tăiat 105
   ore rezervate din săptămâna de 5–11 octombrie. Ocuparea se măsoară în ore-teren din
   același motiv.
6. O zi în care site-ul **eliberează rezervările** pe care le arăta cu o zi înainte nu
   mai e dovadă a ce s-a vândut, deci iese din `dashboard` (`grid_released` în
   `daily_occupancy`). Grila Divi de pe Courtica a început să facă asta pe 2026-10-12:
   sloturi rezervate de o zi au trecut pe liber în cursul dimineții și au rămas așa, iar
   13 octombrie s-a închis cu 0 din 64 de sloturi rezervate la un club care rulează la
   ~60%. Pragul e „mai mult de jumătate din orele de ieri seară au dispărut", cu minim
   5 ore pe zi ca să fie judecabilă (vezi `RELEASE_*` în `collector/derive.py`):
   fluctuația normală e 10–30% la toate cluburile, iar cea mai mare zi nemarcată a fost
   37%. Zilele scoase se numără în `zile_incomplete` și `zile_eliberate`, ca un club să
   nu poată îngheța în tăcere.
7. Alarma de prospețime (`tools/check_freshness.py`, ultimul pas din workflow) verifică
   fiecare club separat: un club fără nicio înregistrare la mai puțin de 6 h de ultima
   colectare reușită face rularea să iasă 1, chiar dacă celelalte cluburi sunt la zi.
   Același pas iese 1 cât timp există o zi `grid_released` în ultimele 3 zile — altfel
   pierderea ar fi invizibilă, clubul doar s-ar opri din mișcare. Se taie cu
   `--release-lookback-days 0`.

Teste (fără rețea): `pytest`. Verificare de paritate cu datele vechi Apify:
`python tools/parity_check.py`. Import unic al exportului vechi:
`python tools/import_legacy.py legacy/data/dataset_padel-ocupare_*.json`.
