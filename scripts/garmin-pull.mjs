#!/usr/bin/env node
// Holt Daily Summaries (Grundumsatz, Aktivkalorien) aus der Garmin Health API
// und schreibt sie als JSON-Datei, die das Kalorienkonto importieren kann.
//
// Nutzung:
//   GARMIN_ACCESS_TOKEN=... node scripts/garmin-pull.mjs --days 14 --out garmin-dailies.json
//
// Optionen:
//   --days N        Zeitraum in Tagen bis jetzt (Standard 7, höchstens 90)
//   --out DATEI     Zieldatei (Standard: Ausgabe auf stdout)
//   --mode upload   Abfrage nach Upload-Zeit (uploadStartTimeInSeconds, Standard)
//   --mode summary  Abfrage nach Zeitraum der Zusammenfassung (summaryStartTimeInSeconds)
//   --base URL      API-Basis (Standard https://apis.garmin.com/wellness-api/rest)
//
// Hinweise:
// - Endpunkt und Parameter folgen dem bekannten Pull-Format der Health API
//   (GET /dailies, Zeitfenster von höchstens 24 Stunden pro Anfrage).
//   Prüfe sie gegen die Doku, die du beim Anlegen des Personal-Keys auf
//   developer.garmin.com bekommst, und passe --base oder --mode bei Bedarf an.
// - Personal-Keys sind nur für deine eigenen Daten gedacht. Zugriffstoken gelten
//   24 Stunden und haben kein Refresh-Token. Das Token nie ins Repo committen.
// - Benötigt Node.js 18 oder neuer (eingebautes fetch). Keine Abhängigkeiten.

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf('--' + name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};

const token = process.env.GARMIN_ACCESS_TOKEN;
if (!token) {
  console.error('Fehlt: Umgebungsvariable GARMIN_ACCESS_TOKEN mit dem Zugriffstoken deines Personal-Keys.');
  process.exit(2);
}
const days = Math.min(90, Math.max(1, Number.parseInt(opt('days', '7'), 10) || 7));
const out = opt('out', null);
const mode = opt('mode', 'upload') === 'summary' ? 'summary' : 'upload';
const base = opt('base', 'https://apis.garmin.com/wellness-api/rest').replace(/\/$/, '');
const startParam = mode === 'summary' ? 'summaryStartTimeInSeconds' : 'uploadStartTimeInSeconds';
const endParam = mode === 'summary' ? 'summaryEndTimeInSeconds' : 'uploadEndTimeInSeconds';

const DAY = 86400;
const now = Math.floor(Date.now() / 1000);
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function fetchWindow(start, end, attempt = 1) {
  const url = `${base}/dailies?${startParam}=${start}&${endParam}=${end}`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' } });
  if (res.status === 401 || res.status === 403) {
    throw new Error(`Garmin lehnt das Token ab (HTTP ${res.status}). Personal-Tokens gelten 24 Stunden. Erzeuge ein neues.`);
  }
  if (res.status === 429 || res.status >= 500) {
    if (attempt >= 4) throw new Error(`Garmin antwortet mit HTTP ${res.status}. Versuch es später noch einmal.`);
    await sleep(1000 * 2 ** attempt);
    return fetchWindow(start, end, attempt + 1);
  }
  if (!res.ok) throw new Error(`HTTP ${res.status} für ${url}: ${(await res.text()).slice(0, 300)}`);
  const body = await res.json();
  return Array.isArray(body) ? body : (Array.isArray(body.dailies) ? body.dailies : []);
}

const all = [];
for (let end = now; end > now - days * DAY; end -= DAY) {
  const start = Math.max(end - DAY, now - days * DAY);
  const rows = await fetchWindow(start, end);
  all.push(...rows);
  process.stderr.write(`${new Date(start * 1000).toISOString().slice(0, 10)}: ${rows.length} Einträge\n`);
}

// Pro Kalendertag die vollständigste Zusammenfassung behalten.
const byDate = new Map();
for (const s of all) {
  if (!s || !s.calendarDate) continue;
  const prev = byDate.get(s.calendarDate);
  if (!prev || (s.durationInSeconds ?? 0) >= (prev.durationInSeconds ?? 0)) byDate.set(s.calendarDate, s);
}
const dailies = [...byDate.values()]
  .sort((a, b) => (a.calendarDate < b.calendarDate ? -1 : 1))
  .map(s => ({
    calendarDate: s.calendarDate,
    bmrKilocalories: s.bmrKilocalories ?? null,
    activeKilocalories: s.activeKilocalories ?? null,
    durationInSeconds: s.durationInSeconds ?? null,
    steps: s.steps ?? null,
    startTimeInSeconds: s.startTimeInSeconds ?? null,
    startTimeOffsetInSeconds: s.startTimeOffsetInSeconds ?? null,
  }));

const json = JSON.stringify({ source: 'garmin-health-api/dailies', fetchedAt: new Date().toISOString(), dailies }, null, 2);
if (out) {
  const { writeFile } = await import('node:fs/promises');
  await writeFile(out, json + '\n');
  process.stderr.write(`${dailies.length} Tage nach ${out} geschrieben.\n`);
} else {
  process.stdout.write(json + '\n');
}
