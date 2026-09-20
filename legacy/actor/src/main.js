import { Actor, log } from 'apify';
import { launchPuppeteer } from '@crawlee/puppeteer';

/* -------------------------------------------------------------------------
   Courtica padel occupancy monitor
   Calibrated 2026-08-20 against www.courtica.md (Next.js + Supabase).

   BOOKED SIGNAL: <td data-time="HH:MM" data-available="true|false">
     data-available="false"  ->  slot is NOT bookable (booked / blocked / past)
     data-available="true"   ->  slot is free
   Verified 64/64 on Divi and 28/28 on Ursu against the visual diagonal-cross
   SVG background-image. NOTE: disabled / aria-disabled / pointer-events /
   cursor / opacity are IDENTICAL for both states - they are NOT usable.

   PAST-SLOT TRAP: courtica marks every slot whose start time has passed as
   data-available="false". A whole past date reads 64/64 booked = 100%.
   Therefore every row carries `viitor` (1 = slot was still in the future at
   collection time). Only viitor=1 rows are valid observations.
   ------------------------------------------------------------------------- */

const ZILE_RO = ['Duminică', 'Luni', 'Marți', 'Miercuri', 'Joi', 'Vineri', 'Sâmbătă'];

const DEFAULT_CLUBS = [
    { slug: 'divi-padel',  pret_ora: 500, nume: 'Divi Padel Club', platforma: 'courtica', durata_ore: 1,   url: 'https://www.courtica.md/en-MD/clubs/divi-padel?sport=padel' },
    { slug: 'ursu-padel',  pret_ora: 250, nume: 'Ursu Padel',      platforma: 'courtica', durata_ore: 0.5, url: 'https://www.courtica.md/en-MD/clubs/ursu-padel?sport=padel' },
    { slug: 'padelpoint', pret_ora: 500, nume: 'PadelPoint', platforma: 'padelpoint', durata_ore: 0.5, terenuri: 9, url: 'https://padelpoint.md/booking/ro/' },
    { slug: 'primus-padel-costesti', pret_ora: 300, nume: 'Primus Padel Costesti', platforma: 'courtica', durata_ore: 0.5, url: 'https://www.courtica.md/en-MD/clubs/primus-padel-costesti?sport=padel' },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** YYYY-MM-DD in a given IANA timezone, shifted by `offsetDays`. */
function zileInTz(tz, offsetDays = 0) {
    const now = new Date(Date.now() + offsetDays * 86400000);
    const data = new Intl.DateTimeFormat('en-CA', {
        timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(now);
    const wd = new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'short' }).format(now);
    const idx = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(wd);
    return { data, zi: ZILE_RO[idx] ?? '' };
}

/** Current wall-clock minutes-since-midnight in a timezone. */
function minuteAcumInTz(tz) {
    const p = new Intl.DateTimeFormat('en-GB', {
        timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(new Date());
    const h = Number(p.find((x) => x.type === 'hour').value);
    const m = Number(p.find((x) => x.type === 'minute').value);
    return h * 60 + m;
}


/* ---------------------------------------------------------------------------
   ADAPTER PADELPOINT  (padelpoint.md/booking/ro)
   Calibrat 2026-08-23. Structura este complet diferita de Courtica:
     - fiecare slot este un <button> cu textul "HH:MM\nAlege" sau "HH:MM\nOcupat"
     - OCUPAT  = textul "Ocupat" + button.disabled === true  (bg-gray-300)
     - LIBER   = textul "Alege"  + button.disabled === false (bg-emerald-50)
     - CAPCANA: butonul "23:00 / Sfarsit" este si el disabled, dar NU e slot,
       ci marcajul de inchidere. Se exclude dupa text, altfel fiecare teren
       apare cu un slot fals ocupat in fiecare zi.
     - URL-ul nu se schimba niciodata: terenul se alege prin click pe harta,
       deci trebuie parcurse C1..C9 pe rand.
   ------------------------------------------------------------------------- */

const ppAreSloturi = (page) => page.evaluate(() =>
    [...document.querySelectorAll('button')].some((b) => /^\d{1,2}:\d{2}/.test((b.innerText || '').trim())));

async function ppInapoiLaHarta(page) {
    if (!(await ppAreSloturi(page))) return;
    await page.evaluate(() => {
        const b = [...document.querySelectorAll('button')]
            .find((x) => /napoi la hart/i.test(x.getAttribute('aria-label') || ''));
        if (b) b.click();
    });
    await sleep(1200);
}

async function ppAlegeData(page, data) {
    await page.evaluate((d) => {
        const el = document.querySelector('input[type=date]');
        if (!el) return;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, d);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }, data);
    await sleep(2500);
}

/** Deschide terenul Cn urcand din eticheta spre parintele care raspunde la click. */
async function ppDeschideTeren(page, n) {
    for (let lvl = 0; lvl < 7; lvl++) {
        await page.evaluate((idx, l) => {
            const lbl = [...document.querySelectorAll('*')]
                .find((e) => e.children.length === 0 && (e.textContent || '').trim() === `C${idx}`);
            if (!lbl) return;
            let el = lbl;
            for (let k = 0; k < l && el.parentElement; k++) el = el.parentElement;
            el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        }, n, lvl);
        await sleep(1100);
        const potrivit = await page.evaluate((idx) => {
            const areSloturi = [...document.querySelectorAll('button')]
                .some((b) => /^\d{1,2}:\d{2}/.test((b.innerText || '').trim()));
            const cap = [...document.querySelectorAll('*')]
                .find((e) => e.children.length === 0 && /^Court\s+\d+$/i.test((e.textContent || '').trim()));
            return areSloturi && !!cap && cap.textContent.trim().toLowerCase() === `court ${idx}`;
        }, n);
        if (potrivit) return true;
    }
    return false;
}

async function ppCitesteSloturi(page) {
    return page.evaluate(() => {
        const btns = [...document.querySelectorAll('button')]
            .filter((b) => /^\d{1,2}:\d{2}/.test((b.innerText || '').trim()));
        const cap = [...document.querySelectorAll('*')]
            .find((e) => e.children.length === 0 && /^Court\s+\d+$/i.test((e.textContent || '').trim()));
        const sloturi = [];
        let neconcordante = 0;
        for (const b of btns) {
            const p = (b.innerText || '').trim().split('\n').map((s) => s.trim()).filter(Boolean);
            const ora = p[0];
            const stare = p[1] || '';
            if (/Sf/i.test(stare)) continue;            // marcajul de inchidere, nu e slot
            const ocupat = /Ocupat/i.test(stare) ? 1 : 0;
            if (!!b.disabled !== (ocupat === 1)) neconcordante++;
            sloturi.push({ ora, ocupat });
        }
        return { nume: cap ? cap.textContent.trim() : null, sloturi, neconcordante };
    });
}

async function extragePadelPoint(page, club) {
    const total = club.terenuri ?? 9;
    const toate = [];
    let neconcordante = 0;
    for (let n = 1; n <= total; n++) {
        await ppInapoiLaHarta(page);
        const deschis = await ppDeschideTeren(page, n);
        if (!deschis) { log.warning(`padelpoint: nu am putut deschide C${n}`); continue; }
        const r = await ppCitesteSloturi(page);
        neconcordante += r.neconcordante || 0;
        if (r.nume && r.nume.toLowerCase() !== `court ${n}`) {
            log.warning(`padelpoint: am cerut C${n} dar pagina arata "${r.nume}" - sar peste`);
            continue;
        }
        const teren = r.nume || `C${n}`;
        for (const s of r.sloturi) toate.push({ teren, ora: s.ora, ocupat: s.ocupat });
    }
    if (neconcordante) log.warning(`padelpoint: ${neconcordante} sloturi unde textul si atributul disabled nu coincid`);
    const mins = (t) => { const [h, mi] = t.split(':').map(Number); return h * 60 + mi; };
    const primul = toate.length ? toate.filter((x) => x.teren === toate[0].teren).map((x) => mins(x.ora)).sort((a, b) => a - b) : [];
    let pas = null;
    for (let i = 1; i < primul.length; i++) { const d = primul[i] - primul[i - 1]; if (d > 0 && (pas === null || d < pas)) pas = d; }
    return { sloturi: toate, pas_min: pas, temp_max: null };
}

await Actor.init();

const input = (await Actor.getInput()) ?? {};
const {
    clubs = DEFAULT_CLUBS,
    dateOffsets = [0],                 // 0 = today. [0,1] also snapshots tomorrow.
    timezone = 'Europe/Chisinau',
    useProxy = true,
    proxyCountry = 'RO',               // MD is usually absent from the DATACENTER pool
    proxyGroups = ['DATACENTER'],
    kvStoreName = 'padel-screenshots',
    datasetName = 'padel-ocupare',
    rezumatDatasetName = 'padel-rezumat',
    pretOra = 500,
    terenKey = 'terenuri.csv',
    zilnicKey = 'zilnic.csv',
    verificaAcoperire = true,
    istoricMaxRanduri = 400000,
    sheetsWebhookUrl = null,           // optional Google Apps Script /exec URL (append mode)
    renderWaitMs = 6000,
} = input;

const CLUBS = (Array.isArray(clubs) && clubs.length ? clubs : DEFAULT_CLUBS).map((c) => ({
    ...c,
    platforma: c.platforma ?? (String(c.url || '').includes('padelpoint') ? 'padelpoint' : 'courtica'),
}));

const kvs = await Actor.openKeyValueStore(kvStoreName);
const dataset = await Actor.openDataset(datasetName);
const rezumatDataset = await Actor.openDataset(rezumatDatasetName);
const runId = Actor.getEnv().actorRunId ?? null;

let proxyUrl;
if (useProxy) {
    try {
        const pc = await Actor.createProxyConfiguration({ groups: proxyGroups, countryCode: proxyCountry });
        proxyUrl = await pc.newUrl();
    } catch (e) {
        log.warning(`Proxy unavailable (${e.message}) - continuing without it.`);
    }
}

const browser = await launchPuppeteer({
    proxyUrl,
    launchOptions: {
        headless: true,
        defaultViewport: { width: 2400, height: 1500 },
        args: ['--no-sandbox', '--disable-dev-shm-usage', '--lang=en-US'],
    },
});

// Ora locala a rularii intra in cheia pozei, ca sa nu se mai suprascrie o singura
// poza pe zi. Fara asta, orice verificare vizuala facuta seara arata ziua plina,
// pentru ca Courtica marcheaza gri tot ce a trecut.
const oraRulare = (() => {
    const t = minuteAcumInTz(timezone);
    const h = Math.floor(t / 60);
    const m2 = (t % 60) < 30 ? 0 : 30;   // rotunjit la :00 / :30 -> cheia e previzibila
    return String(h).padStart(2, '0') + String(m2).padStart(2, '0');
})();

const toateRandurile = [];
const randuriRezumat = [];

for (const offset of dateOffsets) {
    const { data, zi } = zileInTz(timezone, offset);
    const minuteAcum = offset === 0 ? minuteAcumInTz(timezone) : (offset > 0 ? -1 : 24 * 60 + 1);
    // offset > 0  -> whole day is in the future  -> viitor = 1 everywhere
    // offset < 0  -> whole day is in the past    -> viitor = 0 everywhere

    for (const club of CLUBS) {
        const randuriClub = [];
        const cheie = `${club.slug}-${data}-${oraRulare}`;
        const colectat_la = new Date().toISOString();
        let screenshotUrl = null;
        let page;

        try {
            page = await browser.newPage();
            await page.setViewport({ width: 2400, height: 1500 });

            let parsed;

            if (club.platforma === 'padelpoint') {
                log.info(`${club.slug} ${data} -> ${club.url}`);
                await page.goto(club.url, { waitUntil: 'networkidle2', timeout: 90000 });
                await sleep(renderWaitMs);
                await ppAlegeData(page, data);
                await ppInapoiLaHarta(page);

                // ---- (A) POZA PRIMA, intotdeauna, inainte de orice parsare ----
                const bufP = await page.screenshot({ type: 'jpeg', quality: 72, fullPage: true });
                await kvs.setValue(cheie, bufP, { contentType: 'image/jpeg' });
                screenshotUrl = `https://api.apify.com/v2/key-value-stores/${kvs.id}/records/${cheie}`;
                log.info(`screenshot -> ${screenshotUrl}`);

                // ---- (B) PARSE: C1..C9, cate un click pe teren ----
                parsed = await extragePadelPoint(page, club);
            } else {
                const url = `${club.url}${club.url.includes('?') ? '&' : '?'}date=${data}`;
                log.info(`${club.slug} ${data} -> ${url}`);
                await page.goto(url, { waitUntil: 'networkidle2', timeout: 90000 });

                // Client-side grid: wait for the slot cells, then a settle pause.
                try {
                    await page.waitForSelector('td[data-time][data-available]', { timeout: 30000 });
                } catch {
                    log.warning(`${club.slug} ${data}: slot cells never appeared.`);
                }
                await sleep(renderWaitMs); // puppeteer 22+: page.waitForTimeout() is gone

                // The booking grid lives in a horizontally scrolling wrapper, so a plain
                // fullPage screenshot would cut off the evening hours. Unclip it first.
                await page.evaluate(() => {
                    const cell = document.querySelector('td[data-time]');
                    if (!cell) return;
                    let n = cell.parentElement;
                    while (n && n !== document.body) {
                        const cs = getComputedStyle(n);
                        if (cs.overflowX !== 'visible' || cs.maxWidth !== 'none') {
                            n.style.overflow = 'visible';
                            n.style.maxWidth = 'none';
                            n.style.width = 'max-content';
                        }
                        n = n.parentElement;
                    }
                    const t = cell.closest('table');
                    if (t) t.scrollIntoView({ block: 'center' });
                });
                await sleep(800);

                // ---- (A) SCREENSHOT FIRST, always, before any parsing ----
                const buf = await page.screenshot({ type: 'jpeg', quality: 72, fullPage: true });
                await kvs.setValue(cheie, buf, { contentType: 'image/jpeg' });
                screenshotUrl = `https://api.apify.com/v2/key-value-stores/${kvs.id}/records/${cheie}`;
                log.info(`screenshot -> ${screenshotUrl}`);

                // ---- (B) PARSE ----
                    parsed = await page.evaluate(() => {
                    const cells = [...document.querySelectorAll('td[data-time][data-available]')];
                    const sloturi = cells.map((c) => {
                        const tr = c.closest('tr');
                        const head = tr?.querySelector('th') ?? tr?.firstElementChild;
                        return {
                            teren: head && head !== c ? (head.textContent || '').trim() : 'n/a',
                            ora: c.getAttribute('data-time'),
                            ocupat: c.getAttribute('data-available') === 'false' ? 1 : 0,
                        };
                    });
                    const temps = [...document.querySelectorAll('table *')]
                        .map((e) => (e.textContent || '').trim())
                        .filter((t) => /^-?\d{1,2}°$/.test(t))
                        .map((t) => parseInt(t, 10));
                    const mins = (t) => { const [h, mi] = t.split(':').map(Number); return h * 60 + mi; };
                    const primul = sloturi.length ? sloturi.filter((x) => x.teren === sloturi[0].teren).map((x) => mins(x.ora)).sort((p, q) => p - q) : [];
                    let pas = null;
                    for (let i = 1; i < primul.length; i++) { const d = primul[i] - primul[i - 1]; if (d > 0 && (pas === null || d < pas)) pas = d; }
                    return { sloturi, pas_min: pas, temp_max: temps.length ? Math.max(...temps) : null };
                });
            }

            if (!parsed.sloturi.length) throw new Error('zero slots extracted');

            for (const s of parsed.sloturi) {
                const [hh, mm] = s.ora.split(':').map(Number);
                const durata = parsed.pas_min ? parsed.pas_min / 60 : club.durata_ore;
                randuriClub.push({
                    runId,
                    data, zi,
                    club: club.nume,
                    teren: s.teren,
                    ora: s.ora,
                    interval: hh < 17 ? 'ZI' : 'SEARA',
                    durata_ore: durata,
                    ocupat: s.ocupat,
                    ore_ocupate: s.ocupat ? durata : 0,
                    viitor: hh * 60 + mm > minuteAcum ? 1 : 0,
                    temp_max: parsed.temp_max,
                    poza: cheie,   // doar cheia; linkul se compune in sheet
                    status: 'OK',
                    colectat_la,
                });
            }
            log.info(`${club.slug} ${data}: ${parsed.sloturi.length} sloturi OK`);
        } catch (err) {
            // Never write a silent zero: emit exactly one sentinel row.
            log.exception(err, `${club.slug} ${data} PARSE FAILED`);
            randuriClub.push({
                runId,
                data, zi,
                club: club.nume,
                teren: null, ora: null, interval: null,
                durata_ore: club.durata_ore,
                ocupat: null, ore_ocupate: null, viitor: null,
                temp_max: null,
                poza: cheie,   // doar cheia; linkul se compune in sheet
                status: 'PARSE FAILED',
                eroare: String(err.message ?? err).slice(0, 300),
                colectat_la,
            });
        } finally {
            if (page) await page.close().catch(() => {});
        }

        // --- one summary row per (run, club) -> named dataset `padel-rezumat` ---
        toateRandurile.push(...randuriClub);
        const okC = randuriClub.filter((r) => r.status === 'OK');
        const s = (a, f) => a.reduce((t, x) => t + f(x), 0);
        const pct = (a) => (s(a, (r) => r.durata_ore) ? +(100 * s(a, (r) => r.ore_ocupate) / s(a, (r) => r.durata_ore)).toFixed(1) : null);
        const futC = okC.filter((r) => r.viitor === 1);
        const searaC = okC.filter((r) => r.interval === 'SEARA');
        const ziC = okC.filter((r) => r.interval === 'ZI');
        randuriRezumat.push({
            rulat_la: colectat_la,
            runId,
            data, zi,
            club: club.nume,
            status: okC.length ? 'OK' : 'PARSE FAILED',
            sloturi: okC.length,
            ore_totale: s(okC, (r) => r.durata_ore),
            ore_ocupate: s(okC, (r) => r.ore_ocupate),
            pct_brut: pct(okC),
            ore_viitoare: s(futC, (r) => r.durata_ore),
            ocupate_viitoare: s(futC, (r) => r.ore_ocupate),
            pct_viitor: pct(futC),
            ore_zi: s(ziC, (r) => r.durata_ore),
            ocupate_zi: s(ziC, (r) => r.ore_ocupate),
            pct_zi: pct(ziC),
            ore_seara: s(searaC, (r) => r.durata_ore),
            ocupate_seara: s(searaC, (r) => r.ore_ocupate),
            pct_seara: pct(searaC),
            temp_max: okC.length ? okC[0].temp_max : null,
            screenshot: screenshotUrl,
        });
    }
}

await dataset.pushData(toateRandurile);
await rezumatDataset.pushData(randuriRezumat);
await browser.close();

// ---- Optional: append straight into Google Sheets via an Apps Script web app ----
if (sheetsWebhookUrl) {
    try {
        const res = await fetch(sheetsWebhookUrl, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ rows: toateRandurile }),
        });
        log.info(`Sheets append: HTTP ${res.status}`);
    } catch (e) {
        log.warning(`Sheets append failed: ${e.message}`);
    }
}


// ---------------------------------------------------------------------------
// PER-COURT ROLLUP. Rebuilt from the whole history on every run and written as
// one CSV record in the key-value store, so the sheet needs zero formulas.
// The final state of a slot is its LAST observation while it was still in the
// future (viitor=1) - that can only be known across runs, never within one.
// ---------------------------------------------------------------------------
let istoric = [];
try {
    const info = await dataset.getInfo();
    const total = info?.itemCount ?? 0;
    const CAP = istoricMaxRanduri;
    const start = Math.max(0, total - CAP);
    const CAMPURI = ['data', 'zi', 'club', 'teren', 'ora', 'durata_ore', 'ocupat', 'viitor', 'status', 'colectat_la'];
    let items;
    try {
        ({ items } = await dataset.getData({ offset: start, limit: CAP, clean: true, fields: CAMPURI }));
    } catch (e) {
        log.warning(`getData cu fields a esuat (${e.message}), reincerc fara proiectie`);
        ({ items } = await dataset.getData({ offset: start, limit: CAP, clean: true }));
    }
    istoric = items;
    const zileAcoperite = new Set(items.filter((i) => i.status === 'OK' && i.data).map((i) => i.data));
    const primaZi = [...zileAcoperite].sort()[0] ?? 'n/a';
    log.info(`ISTORIC: ${items.length} randuri, ${zileAcoperite.size} zile, prima zi ${primaZi}`);
    if (total > CAP) {
        log.warning(`ISTORIC TRUNCHIAT: datasetul are ${total} randuri, folosesc ultimele ${CAP}. Rollup-urile acopera doar de la ${primaZi} incoace.`);
    }

    const final = new Map();                 // slot key -> last viitor=1 observation
    const vazute = new Map();                // court key -> Set of slots ever seen
    for (const it of items) {
        if (it.status !== 'OK' || !it.ora) continue;
        const curte = `${it.data}\u0000${it.club}\u0000${it.teren}`;
        if (!vazute.has(curte)) vazute.set(curte, new Set());
        vazute.get(curte).add(it.ora);
        if (it.viitor !== 1) continue;
        const k = `${curte}\u0000${it.ora}`;
        const prev = final.get(k);
        if (!prev || String(it.colectat_la) > String(prev.colectat_la)) final.set(k, it);
    }

    const PRET = new Map(CLUBS.map((c) => [c.nume, c.pret_ora ?? pretOra]));
    const pretPentru = (nume) => PRET.get(nume) ?? pretOra;

    const agg = new Map();
    for (const [k, it] of final) {
        const curte = k.slice(0, k.lastIndexOf('\u0000'));
        if (!agg.has(curte)) {
            agg.set(curte, {
                data: it.data, zi: it.zi, club: it.club, teren: it.teren,
                sloturi_observate: 0, sloturi_ocupate: 0, ore_totale: 0, ore_ocupate: 0,
            });
        }
        const a = agg.get(curte);
        a.sloturi_observate += 1;
        a.ore_totale += it.durata_ore;
        if (it.ocupat === 1) { a.sloturi_ocupate += 1; a.ore_ocupate += it.durata_ore; }
    }

    const COLS = ['data', 'zi', 'club', 'teren', 'sloturi_totale', 'sloturi_observate',
        'sloturi_ocupate', 'ore_totale', 'ore_ocupate', 'ocupancy_pct', 'pret_ora', 'venit_mdl'];
    const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const linii = [COLS.map(q).join(',')];
    const randuri = [...agg.entries()]
        .map(([curte, a]) => ({
            ...a,
            sloturi_totale: vazute.get(curte)?.size ?? a.sloturi_observate,
            ocupancy_pct: a.ore_totale ? +(100 * a.ore_ocupate / a.ore_totale).toFixed(1) : null,
            pret_ora: pretPentru(a.club),
            venit_mdl: Math.round(a.ore_ocupate * pretPentru(a.club)),
        }))
        .sort((x, y) => (y.data + y.club + y.teren).localeCompare(x.data + x.club + x.teren));
    for (const r of randuri) linii.push(COLS.map((c) => q(r[c])).join(','));

    // --- al doilea CSV: un rand per (zi, club), impartit la 14:00 ---
    const LUNI = ['ianuarie','februarie','martie','aprilie','mai','iunie','iulie','august','septembrie','octombrie','noiembrie','decembrie'];
    const dataText = (iso) => { const [yy, mm2, dd] = String(iso).split('-').map(Number); return `${dd} ${LUNI[mm2 - 1]} ${yy}`; };
    const zilnic = new Map();
    for (const it of final.values()) {
        const cz = `${it.data}||${it.club}`;
        if (!zilnic.has(cz)) zilnic.set(cz, { data: it.data, zi: it.zi, club: it.club, ore_dim: 0, ocup_dim: 0, ore_seara: 0, ocup_seara: 0, ore_totale: 0, ore_ocupate: 0, sloturi: 0 });
        const z = zilnic.get(cz);
        const h = Number(String(it.ora).split(':')[0]);
        const dim = h < 14;
        z.sloturi += 1;
        z.ore_totale += it.durata_ore;
        if (dim) z.ore_dim += it.durata_ore; else z.ore_seara += it.durata_ore;
        if (it.ocupat === 1) { z.ore_ocupate += it.durata_ore; if (dim) z.ocup_dim += it.durata_ore; else z.ocup_seara += it.durata_ore; }
    }
    const ZCOLS = ['data_text','zi','club','ocupare_06_14_pct','ocupare_14_24_pct','ocupare_zi_pct','ore_totale','ore_ocupate','pret_ora','venit_mdl','sloturi','data'];
    const zlinii = [ZCOLS.map(q).join(',')];
    const pc = (oc, tot) => (tot ? +(100 * oc / tot).toFixed(1) : null);
    const zrand = [...zilnic.values()].map((z) => ({
        data_text: dataText(z.data), zi: z.zi, club: z.club,
        ocupare_06_14_pct: pc(z.ocup_dim, z.ore_dim),
        ocupare_14_24_pct: pc(z.ocup_seara, z.ore_seara),
        ocupare_zi_pct: pc(z.ore_ocupate, z.ore_totale),
        ore_totale: +z.ore_totale.toFixed(2), ore_ocupate: +z.ore_ocupate.toFixed(2),
        pret_ora: pretPentru(z.club), venit_mdl: Math.round(z.ore_ocupate * pretPentru(z.club)),
        sloturi: z.sloturi, data: z.data,
    })).sort((x, y) => (y.data + y.club).localeCompare(x.data + x.club));
    for (const r of zrand) zlinii.push(ZCOLS.map((c) => q(r[c])).join(','));
    await kvs.setValue(zilnicKey, zlinii.join('\n'), { contentType: 'text/csv; charset=utf-8' });
    log.info(`ROLLUP zilnic: ${zrand.length} randuri -> https://api.apify.com/v2/key-value-stores/${kvs.id}/records/${zilnicKey}`);

    await kvs.setValue(terenKey, linii.join('\n'), { contentType: 'text/csv; charset=utf-8' });
    log.info(`ROLLUP terenuri: ${randuri.length} randuri -> https://api.apify.com/v2/key-value-stores/${kvs.id}/records/${terenKey}`);
} catch (e) {
    log.warning(`Rollup terenuri esuat: ${e.message}`);
}

// Run summary, court-hours normalised.
const ok = toateRandurile.filter((r) => r.status === 'OK');
const fut = ok.filter((r) => r.viitor === 1);
const sum = (a, f) => a.reduce((s, x) => s + f(x), 0);
const rezumat = {
    randuri: toateRandurile.length,
    parse_failed: toateRandurile.filter((r) => r.status === 'PARSE FAILED').length,
    ore_totale: sum(ok, (r) => r.durata_ore),
    ore_ocupate: sum(ok, (r) => r.ore_ocupate),
    grad_ocupare_brut: ok.length ? +(100 * sum(ok, (r) => r.ore_ocupate) / sum(ok, (r) => r.durata_ore)).toFixed(1) : null,
    grad_ocupare_viitor: fut.length ? +(100 * sum(fut, (r) => r.ore_ocupate) / sum(fut, (r) => r.durata_ore)).toFixed(1) : null,
};
log.info(`REZUMAT ${JSON.stringify(rezumat)}`);
await Actor.setValue('REZUMAT', rezumat);

// ---------------------------------------------------------------------------
// VERIFICARE DE ACOPERIRE.
// Un Actor care raporteaza Succeeded in timp ce colecteaza 1/9 din date este
// mai periculos decat unul care crapa. Comparam ce am prins azi cu maximul
// ultimelor 7 zile si oprim runul cu FAILED daca acoperirea a scazut.
// Datele deja colectate sunt salvate inainte de aceasta verificare.
// ---------------------------------------------------------------------------
const probleme = [];
if (verificaAcoperire) {
    try {
        for (const c of CLUBS) {
            const n = toateRandurile.filter((r) => r.club === c.nume && r.status === 'OK').length;
            if (!n) probleme.push(`${c.nume}: 0 sloturi in acest run`);
        }

        const perZiClub = new Map();                 // cheie data + club -> Set(teren)
        for (const it of istoric) {
            if (it.status !== 'OK' || !it.teren) continue;
            const k = `${it.data}||${it.club}`;
            if (!perZiClub.has(k)) perZiClub.set(k, new Set());
            perZiClub.get(k).add(it.teren);
        }

        const azi = zileInTz(timezone, 0).data;
        const cutoff = zileInTz(timezone, -7).data;  // fereastra mobila: se auto-vindeca
        const record = new Map();                    // club -> { n, zi }
        for (const [k, set] of perZiClub) {
            const idx = k.indexOf('||');
            const d = k.slice(0, idx);
            const club = k.slice(idx + 2);
            if (d >= azi || d < cutoff) continue;
            const prev = record.get(club);
            if (!prev || set.size > prev.n) record.set(club, { n: set.size, zi: d });
        }

        for (const c of CLUBS) {
            const aziN = perZiClub.get(`${azi}||${c.nume}`)?.size ?? 0;
            const ref = record.get(c.nume);
            log.info(`ACOPERIRE ${c.nume}: ${aziN} terenuri azi (record 7 zile: ${ref ? ref.n : 'n/a'})`);
            if (ref && aziN < ref.n) {
                probleme.push(`${c.nume}: azi ${aziN} terenuri, dar pe ${ref.zi} erau ${ref.n}`);
            }
        }
    } catch (e) {
        log.warning(`Verificarea de acoperire a esuat: ${e.message}`);
    }
}

if (probleme.length) {
    for (const p of probleme) log.error(`ACOPERIRE INCOMPLETA -> ${p}`);
    await Actor.setValue('PROBLEME', probleme);
    await Actor.fail(`Acoperire incompleta (datele colectate SUNT salvate): ${probleme.join(' | ')}`);
}

await Actor.exit();