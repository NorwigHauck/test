# Kalorienkonto mit Garmin-Daten

Kalorientracker, der Essen protokolliert und den Verbrauch aus Garmin-Tageswerten übernimmt.

- App (Artefakt auf claude.ai, privat): https://claude.ai/artifact/YYzseUMsmzH3HMGy3BEVLZ
- Quelltext der App: [app/kalorienkonto.html](app/kalorienkonto.html)
- Garmin-Abrufskript: [scripts/garmin-pull.mjs](scripts/garmin-pull.mjs)
- Plan und Umsetzung: [docs/PLAN.md](docs/PLAN.md)

## Garmin-Daten holen

```sh
GARMIN_ACCESS_TOKEN=... node scripts/garmin-pull.mjs --days 14 --out garmin-dailies.json
```

Die Datei dann in der App unter „Garmin-Daten“ importieren. Endpunkt und Parameter
vor dem ersten Einsatz mit der Doku zum Personal-Key abgleichen (siehe Kopf des Skripts).

## Tests

```sh
node --test tests/garmin-import.test.mjs
```
