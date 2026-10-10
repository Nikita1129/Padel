# Padel occupancy tracker

Colectează, la fiecare oră (06:30–23:59, ora Chișinăului), starea fiecărui slot de
teren pentru azi și mâine de pe grilele publice Courtica, o păstrează brut în
`data/raw/AAAA-LL.csv` (append-only, commit automat în repo) și publică două tabele
derivate într-un Google Sheet: `dashboard` (un rând per club: cât a vândut în toată
perioada urmărită, doar zilele observate complet), `slots_final` (ultima stare observată înainte de
începerea slotului + când a fost văzut prima dată rezervat) și `daily_occupancy`
(ocupare pe zi și club, împărțită dimineață / după-amiază / seară, plus
`revenue_estimate_mdl` = ore-teren rezervate × prețul orei). Prețul e cel afișat de
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
   „Complet observată" se judecă per pas de grilă, nu per club: când PadelPoint a
   trecut de la sloturi de 30 min la o oră (2026-10-10), ziua plină a scăzut de la 288
   la 144 de sloturi, iar un singur maxim pe club ar fi exclus silențios toate zilele
   de după schimbare. Ocuparea din `dashboard` se măsoară în ore-teren, nu în sloturi,
   din același motiv.
6. Alarma de prospețime (`tools/check_freshness.py`, ultimul pas din workflow) verifică
   fiecare club separat: un club fără nicio înregistrare la mai puțin de 6 h de ultima
   colectare reușită face rularea să iasă 1, chiar dacă celelalte cluburi sunt la zi.

Teste (fără rețea): `pytest`. Verificare de paritate cu datele vechi Apify:
`python tools/parity_check.py`. Import unic al exportului vechi:
`python tools/import_legacy.py legacy/data/dataset_padel-ocupare_*.json`.
