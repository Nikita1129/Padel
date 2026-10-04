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

## Pasul 1 — tokenul

Aici e singura acțiune care cere un om: GitHub nu permite crearea de tokenuri
prin API, deci pasul acesta se face o dată, din browser. Alege una din două.

### Varianta recomandată: classic PAT, fără expirare

GitHub → Settings → Developer settings → Personal access tokens →
**Tokens (classic)** → Generate new token (classic).

| Câmp | Valoare |
|---|---|
| Note | `padel-collect-dispatch` |
| Expiration | **No expiration** |
| Scopes | doar **`public_repo`** |

De ce asta și nu varianta fine-grained: un token care expiră e o pană
programată. Ziua în care expiră, colectarea se oprește în liniște și tu afli
peste o săptămână. `public_repo` e mai larg decât strictul necesar (poate
scrie în repo-urile tale publice), dar acest repo e public, nu conține
secrete în cod, iar secretul de Actions
(`GOOGLE_SERVICE_ACCOUNT_JSON`) nu poate fi citit de niciun PAT. Compromisul
merită: scoate din ecuație singurul mod de eșec programat.

### Varianta cu scope minim: fine-grained

Dacă preferi permisiuni minime și accepți să-l reînnoiești manual:
**Fine-grained tokens** → Generate new token, `Only select repositories` →
`Nikita1129/Padel`, Permissions → Repository → **Actions: Read and write**,
nimic altceva. Maximul de expirare e 1 an — pune-ți reminder în calendar, și
contează pe alarma din Pasul 4 ca plasă de siguranță.

Copiază valoarea o singură dată. Nu o pune în repo, în chat, în `.env` comis
sau într-un screenshot.

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

## Pasul 4 — alarma pentru tăcere (deja activă)

`collect.yml` are un ultim pas, `Alarm on stale data`, care rulează doar la
declanșările de tip `schedule` și execută `tools/check_freshness.py`: dacă cel
mai recent `snapshot_ts` din `data/raw/` e mai vechi de 6 ore, rularea iese
non-zero și GitHub îți trimite email de workflow failure.

Cum acoperă exact gaura din septembrie: dacă atât cron-ul extern cât și rutina
din sesiune se opresc, cron-ul propriu al GitHub tot pornește de câteva ori pe
zi — suficient ca pasul acesta să observe vechimea și să țipe. O verificare
dinăuntrul workflow-ului nu poate detecta „n-a rulat absolut nimic" (dacă nu
pornește nicio rulare, nu rulează nici verificarea), dar cron-ul GitHub e
destul de prezent ca să servească drept puls.

Verificarea e sărită în primele 6 ore ale ferestrei (până la 12:30 local) și
după închiderea ei, altfel ar da alarme false pe datele legitime de peste
noapte. Deci tăcerea totală e prinsă zilnic, cel târziu la 12:30.

Asigură-te că ai notificările de workflow failure active:
GitHub → Settings → Notifications → Actions → bifat **Email**. Fără ele,
alarma scrie într-un jurnal pe care nu-l citește nimeni.

După ce cron-ul extern e confirmat că bate orar, poți strânge pragul de la 6 la
3 ore în `.github/workflows/collect.yml`.

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

Alarma din Pasul 4 prinde tăcerea, nu și degradarea parțială: dacă un singur
club din patru începe să dea zero sloturi (Courtica își schimbă grila doar
pentru el), restul datelor rămân proaspete și nimic nu se plânge. Un check per
club ar fi pasul următor; încă nu există.

Nici cron-job.org nu are SLA: dacă *el* cade, singurul lucru care rămâne în
picioare e cron-ul leneș al GitHub, iar alarma din Pasul 4 te prinde în
maximum o zi. Două surse independente de declanșare sunt mai bune ca una, dar
nu fac sistemul redundant la propriu.
