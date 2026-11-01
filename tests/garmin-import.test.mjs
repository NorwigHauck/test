// Tests für den Garmin-Import im Kalorienkonto.
// Liest den Parser direkt aus app/kalorienkonto.html, damit Seite und Test nicht auseinanderlaufen.
// Ausführen: node --test tests/garmin-import.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const html = await readFile(new URL('../app/kalorienkonto.html', import.meta.url), 'utf8');
const block = html.split('/* PARSER START */')[1]?.split('/* PARSER END */')[0];
assert.ok(block, 'Parser-Block nicht gefunden');
const { parseGarminImport, parseLooseNum, normDate } = new Function(`${block}; return { parseGarminImport, parseLooseNum, normDate };`)();

test('Health-API-Dailies: Teil-Tage werden durch den vollständigsten Eintrag ersetzt', () => {
  const res = parseGarminImport(JSON.stringify([
    { summaryId: 'a', calendarDate: '2026-10-30', bmrKilocalories: 900, activeKilocalories: 210, durationInSeconds: 43200, steps: 5000 },
    { summaryId: 'b', calendarDate: '2026-10-30', bmrKilocalories: 1802, activeKilocalories: 640, durationInSeconds: 86400, steps: 11200 },
    { summaryId: 'c', calendarDate: '2026-10-31', bmrKilocalories: 1790, activeKilocalories: 455, durationInSeconds: 86400 },
  ]));
  assert.equal(res.format, 'JSON');
  assert.equal(res.error, '');
  assert.deepEqual(res.days.map(d => [d.date, d.bmr, d.active, d.durationS]), [
    ['2026-10-30', 1802, 640, 86400],
    ['2026-10-31', 1790, 455, 86400],
  ]);
});

test('Ausgabe des Abrufskripts (verschachtelt unter dailies)', () => {
  const res = parseGarminImport(JSON.stringify({ source: 'garmin-health-api/dailies', dailies: [
    { calendarDate: '2026-11-01', bmrKilocalories: 1010, activeKilocalories: 300, durationInSeconds: 50400 },
  ] }));
  assert.equal(res.days.length, 1);
  assert.equal(res.days[0].durationS, 50400);
});

test('Garmin-Datenexport: Gesamt minus Aktiv ergibt Grundumsatz, Datum als Objekt', () => {
  const res = parseGarminImport(JSON.stringify([
    { calendarDate: { date: '2026-10-29' }, totalKilocalories: 2500, activeKilocalories: 700, totalSteps: 9000 },
    { calendarDate: '2026-10-28', bmrKilocalories: 1795, activeKilocalories: 410, wellnessKilocalories: 2205 },
  ]));
  assert.deepEqual(res.days.map(d => [d.date, d.bmr, d.active, d.steps]), [
    ['2026-10-28', 1795, 410, null],
    ['2026-10-29', 1800, 700, 9000],
  ]);
});

test('Aktivitäten und Epochs ohne Kalenderdatum werden ignoriert', () => {
  const res = parseGarminImport(JSON.stringify({
    activities: [{ activityType: 'RUNNING', activeKilocalories: 450, startTimeInSeconds: 1761900000, durationInSeconds: 3600 }],
    epochs: [{ activeKilocalories: 12, startTimeInSeconds: 1761900000, durationInSeconds: 900 }],
  }));
  assert.equal(res.days.length, 0);
  assert.match(res.error, /Keine Tageswerte/);
});

test('Daily Summary ohne calendarDate: Datum aus Startzeit plus Offset', () => {
  // 2026-10-31 23:30 UTC, Offset +1 h => lokaler Tag 2026-11-01
  const start = Date.UTC(2026, 9, 31, 23, 30) / 1000;
  const res = parseGarminImport(JSON.stringify([{ bmrKilocalories: 1700, activeKilocalories: 100, startTimeInSeconds: start, startTimeOffsetInSeconds: 3600 }]));
  assert.equal(res.days[0].date, '2026-11-01');
});

test('CSV mit Semikolon, deutschem Datum und Tausenderpunkt', () => {
  const res = parseGarminImport('Datum;Grundumsatz;Aktivkalorien;Schritte\n30.10.2026;1.805;612;10.234\n31.10.2026;1790;--;\nkaputt;1;2\n');
  assert.equal(res.format, 'CSV');
  assert.equal(res.skipped, 1);
  assert.deepEqual(res.days.map(d => [d.date, d.bmr, d.active, d.steps]), [
    ['2026-10-30', 1805, 612, 10234],
    ['2026-10-31', 1790, null, null],
  ]);
});

test('CSV ohne passende Spalten liefert eine verständliche Fehlermeldung', () => {
  const res = parseGarminImport('Activity Type,Date,Calories\nRunning,2026-10-30,450\n');
  assert.equal(res.days.length, 0);
  assert.match(res.error, /Spalten nicht erkannt/);
});

test('Ungültiges JSON und leere Eingabe', () => {
  assert.match(parseGarminImport('{"calendarDate": ').error, /JSON/);
  assert.match(parseGarminImport('   ').error, /leer/);
});

test('Zahlen und Datumsformate', () => {
  assert.equal(parseLooseNum('1.234,5'), 1234.5);
  assert.equal(parseLooseNum('1,234'), 1234);
  assert.equal(parseLooseNum('612 kcal'), 612);
  assert.equal(parseLooseNum('1,5'), 1.5);
  assert.equal(parseLooseNum('--'), null);
  assert.equal(normDate('2026-02-30'), null);
  assert.equal(normDate('1.11.26'), '2026-11-01');
  assert.equal(normDate('2026-10-30T00:00:00.0'), '2026-10-30');
});
