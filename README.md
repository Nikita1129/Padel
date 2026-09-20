# Padel occupancy tracker

Colectează, la fiecare oră (06:30–23:59, ora Chișinăului), starea fiecărui slot de
teren pentru azi și mâine de pe grilele publice Courtica, o păstrează brut în
`data/raw/AAAA-LL.csv` (append-only, commit automat în repo) și publică două tabele
derivate într-un Google Sheet: `slots_final` (ultima stare observată înainte de
începerea slotului + când a fost văzut prima dată rezervat) și `daily_occupancy`
(ocupare pe zi și club, împărțită dimineață / după-amiază / seară).

Rulează pe GitHub Actions (`.github/workflows/collect.yml`), fără browser (grila vine
în HTML), fără servicii plătite. Playwright rămâne doar ca rezervă automată.

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
3. Commit. Următoarea rulare programată îl include. Dacă nu știi numărul de terenuri
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

O rulare eșuată nu scrie nimic. Nu apar rânduri goale sau parțiale.

1. GitHub → Actions → `collect` → rularea roșie → pasul **Collect**. Prima linie cu
   `RUN FAILED` spune cauza:
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
   startului, oricare ar fi ea. Zilele fără nicio observație nu apar deloc.

Teste (fără rețea): `pytest`. Verificare de paritate cu datele vechi Apify:
`python tools/parity_check.py`. Import unic al exportului vechi:
`python tools/import_legacy.py legacy/data/dataset_padel-ocupare_*.json`.
