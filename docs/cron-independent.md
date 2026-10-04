# Trigger independent (cron extern + PAT)

## De ce

Colectorul are două declanșatoare și niciunul nu e de încredere singur:

- **`schedule` din GitHub Actions** (`.github/workflows/collect.yml`, `cron: "35 3-21 * * *"`)
  e *best-effort*. Pe acest repo s-a observat că pornește o dată la 4–8 ore, nu
  orar — GitHub depriorizează cron-urile în orele de vârf. Rezultatul: găuri în
  `data/raw/`.
- **Rutina din sesiunea Claude** (`workflow_dispatch` la fiecare oră) acoperă
  golurile, dar e legată de o sesiune anume. Dacă sesiunea moare, colectarea se
  oprește fără niciun semnal — exact cum s-a întâmplat între 14 și 20 septembrie.

Pentru fereastra care contează (noiembrie–februarie) e nevoie de un declanșator
care nu depinde nici de capriciile cron-ului GitHub, nici de o sesiune vie.

Soluția: un cron extern care apelează `workflow_dispatch` cu un PAT.

Cele trei declanșatoare pot coexista. `collect.yml` are
`concurrency: group: collect, cancel-in-progress: false`, iar `data/raw/*.csv`
folosește driverul de merge `union` (`.gitattributes`), deci rulările
suprapuse se pun la coadă și nu pierd rânduri. **Nu scoate cron-ul din
GitHub Actions** — e backup gratuit.

## Pasul 1 — PAT fine-grained

GitHub → Settings → Developer settings → Personal access tokens →
**Fine-grained tokens** → Generate new token.

| Câmp | Valoare |
|---|---|
| Token name | `padel-collect-dispatch` |
| Expiration | 1 an (notează data în calendar — la expirare colectarea se oprește) |
| Resource owner | `Nikita1129` |
| Repository access | **Only select repositories** → `Nikita1129/Padel` |
| Permissions → Repository | **Actions: Read and write** (doar aceasta) |

Nimic altceva. Tokenul acesta poate doar să pornească workflow-uri în acest
repo: nu poate citi alte repo-uri, nu poate face push, nu poate schimba setări.

Copiază valoarea o singură dată (`github_pat_…`). Nu o pune în repo, în chat,
în `.env` comis sau într-un screenshot.

## Pasul 2 — jobul de cron

### Varianta A: cron-job.org (gratuit, fără server)

Creează cont, apoi **Create cronjob**:

- **Title**: `Padel collect`
- **URL**:
  `https://api.github.com/repos/Nikita1129/Padel/actions/workflows/collect.yml/dispatches`
- **Execution schedule**: Custom → minutul `15`, orele `6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23`, în fiecare zi
- **Timezone**: `Europe/Chisinau` (așa nu te atinge trecerea la ora de iarnă)
- Deschide **Advanced settings**:
  - **Request method**: `POST`
  - **Headers**:
    ```
    Accept: application/vnd.github+json
    Authorization: Bearer github_pat_TOKENUL_TAU
    X-GitHub-Api-Version: 2022-11-28
    Content-Type: application/json
    ```
  - **Request body**:
    ```json
    {"ref":"main","inputs":{"dry_run":"false","ignore_window":"false","commit_html":"false"}}
    ```
  - **Treat redirects as success**: nu
  - Lasă notificările pe email active: cron-job.org dezactivează jobul după
    eșecuri repetate și trimite alertă. Asta e singurul tău semnal că s-a rupt.

GitHub răspunde `204 No Content` la succes. cron-job.org tratează 204 ca OK.

### Varianta B: crontab pe orice server

Dacă există deja un server (sau un Raspberry Pi care stă pornit):

```sh
# în crontab -e, cu fusul serverului pe Europe/Chisinau
15 6-23 * * * PADEL_DISPATCH_TOKEN=github_pat_... /cale/catre/Padel/tools/dispatch_collect.sh >> /var/log/padel-dispatch.log 2>&1
```

Mai curat: pune tokenul într-un fișier citibil doar de owner
(`chmod 600 ~/.padel-dispatch.env`) și fă `. ~/.padel-dispatch.env` înainte de
script.

`tools/dispatch_collect.sh` iese cu 0 doar dacă GitHub a răspuns 204; altfel
scrie codul HTTP și corpul răspunsului pe stderr.

## Pasul 3 — verificare

1. În cron-job.org apasă **Test run** (sau rulează scriptul manual pe server).
   Trebuie să vezi `204`.
2. În 10 secunde apare o rulare nouă la
   https://github.com/Nikita1129/Padel/actions/workflows/collect.yml
   cu `Triggered via workflow dispatch`.
3. După ~2 minute, commit-ul `data: snapshot <timestamp>` trebuie să apară pe `main`.
4. A doua zi: verifică în `data/raw/` că ai snapshot-uri la fiecare oră din
   fereastră, nu doar la 4–8 ore.

**Atenție:** tokenul nu poate fi testat din sesiunea Claude. Containerul rulează
în spatele unui proxy care înlocuiește header-ul `Authorization` cu
credențialul sesiunii — orice token, chiar inventat, întoarce `204` de aici.
Deci verificarea de la Pasul 3 trebuie făcută de pe cron-job.org sau de pe
server, nu din chat.

## Ce înseamnă fiecare input

| Input | Valoare | Efect |
|---|---|---|
| `dry_run` | `"false"` | scrie în `data/raw/` și împinge în Google Sheet |
| `ignore_window` | `"false"` | păstrează poarta 06:30–23:59 Chișinău: rulările de noapte ies 0 imediat |
| `commit_html` | `"false"` | nu comite paginile descărcate în `fixtures/real/` |

Valorile trebuie trimise ca **string-uri** (`"false"`, nu `false`): REST API-ul
GitHub respinge boolean-ii JSON pentru inputuri de workflow.

Pentru că `ignore_window` e `"false"`, poți lăsa cron-ul să bată și 24/7 fără
efecte secundare — rulările din afara ferestrei nu scriu nimic. Repo-ul e
public, deci minutele de Actions sunt gratuite.

## Când se rupe

| Simptom | Cauză probabilă | Ce faci |
|---|---|---|
| `401 Bad credentials` | PAT expirat sau revocat | generează altul, actualizează jobul |
| `403` + `Resource not accessible` | PAT-ului îi lipsește `Actions: read and write` | corectează permisiunea |
| `404` | numele workflow-ului sau al repo-ului e greșit | verifică URL-ul |
| `422 Unexpected inputs provided` | inputuri trimise ca boolean în loc de string | pune ghilimele |
| 204 dar nicio rulare | ai dat dispatch pe un `ref` care nu are `collect.yml` | `"ref":"main"` |
| Jobul dezactivat de cron-job.org | eșecuri repetate | citește emailul de alertă, reactivează după ce repari |

## Ce rămâne neacoperit

Nimic din cele de mai sus nu te anunță dacă *colectorul* eșuează în liniște
(ex. Courtica își schimbă grila și runul iese non-zero). GitHub trimite email
la workflow failure pe `main` — ține-l activ. Un watchdog care verifică „am
primit cel puțin un snapshot în ultimele 3 ore" ar fi pasul următor; încă nu
există.
