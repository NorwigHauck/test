# Plan: Kalorien-Tracking-App mit Garmin-Daten

Stand: 01.11.2026

## 1. Ziel

Eine App, in der man Essen protokolliert (Kalorien-**Aufnahme**) und die den
**Verbrauch** automatisch aus Garmin bezieht. Daraus ergibt sich die
Tagesbilanz: *Ziel – gegessen + verbrannt = übrig*.

Wichtigste Erkenntnis vorweg: **Garmin liefert nur den Verbrauch, nicht die
Ernährung.** Es gibt keine offizielle API für Garmins eigenes
Ernährungstagebuch. Lebensmittel-Logging, Lebensmitteldatenbank und
Barcode-Scan baut die App deshalb selbst.

## 2. Garmin-API: was es gibt (Stand Okt./Nov. 2026)

Garmin hat das *Connect Developer Program* im März 2026 für Neuanmeldungen
geschlossen und am **21.10.2026 mit drei Stufen wieder geöffnet**:

| Stufe | Für wen | APIs | Datenzugriff | Kosten |
|---|---|---|---|---|
| **Personal** | eine Person, eigene Daten | Activity, Health, Women's Health | nur Pull | kostenlos, Key direkt in Garmin Connect erstellen |
| **Starter** | Organisationen, bis ca. 1.000 Nutzer | + Courses, Training | nur Push (Webhooks) | kostenlos, Antrag mit Datenschutzerklärung |
| **Enterprise** | ab 1.000 Nutzern | + Golf, Historie-Backfill, Support | Push oder Ping/Pull | ca. 1.000 $ Einstieg + Preis pro aktivem Nutzer, Abrechnung ab 01.04.2027 |

Angaben zur Personal-Stufe aus Berichten: Keys gelten 30 Tage bis 1 Jahr,
Access-Tokens 24 h, **keine Refresh-Tokens**, nur Lesezugriff, ausdrücklich
nicht für kommerzielle Apps gedacht, die anderen Personen dienen.

> ⚠️ Die Details stammen aus Fachartikeln (DC Rainmaker, Sahha), nicht aus der
> offiziellen Doku. Feldnamen, Endpunkte und den Token-Ablauf beim Anlegen des
> Keys auf developer.garmin.com prüfen.

### Relevante Daten für Kalorien

| Garmin-Daten | Felder (laut Doku/Data Dictionary, prüfen) | Nutzung in der App |
|---|---|---|
| Daily Summary (Health API) | `calendarDate`, `bmrKilocalories`, `activeKilocalories`, `durationInSeconds`, `startTimeOffsetInSeconds` | Grundumsatz + aktive Kalorien pro Tag |
| Activities (Activity API) | Typ, Dauer, `activeKilocalories` | Einzelne Trainings in der Tagesansicht |
| Body Composition (Health API) | Gewicht, ggf. Körperfett (Index-Waage) | Gewichtsverlauf, falls vorhanden |

Wichtig: Daily Summaries kommen **mehrfach am Tag** mit wachsenden Werten
(Teil-Tag nach jeder Synchronisierung). Die App muss sie pro Tag
überschreiben (Upsert) und zwischen „bisher verbrannt" und „Prognose
Tagesende" unterscheiden.

### Nicht empfohlen: inoffizielle Bibliotheken

`python-garminconnect` kann auch Garmins Ernährungstagebuch lesen und
schreiben, nutzt aber private Endpunkte über den Login der Garmin-Mobile-App.
Das ist nicht offiziell erlaubt und bricht regelmäßig: `garth` ist seit 2025
eingestellt, und laut einem Bericht hat Garmin im März 2026 den Login
nochmals geändert und Cloudflare-Fingerprinting eingeführt. Höchstens als
optionales, abschaltbares Experiment einplanen.

## 3. Vorschlag zur Architektur

**Empfehlung für den Start: Personal-Stufe, Web-App als PWA, nur für dich.**
Das geht sofort und kostenlos. Für andere Nutzer später auf Starter
(Webhooks) wechseln, ohne die Kernlogik neu zu schreiben.

```
┌──────────────┐     HTTPS      ┌───────────────────────────┐
│ PWA (Handy/  │ ─────────────▶ │ Backend (API + Sync-Job)  │
│ Desktop)     │                │                           │
└──────────────┘                │  ┌─────────────────────┐  │      ┌──────────────┐
                                │  │ DataSource-Adapter  │──┼────▶ │ Garmin API   │
                                │  │  - GarminPull (P1)  │  │      └──────────────┘
                                │  │  - GarminPush (P3)  │◀─┼──── Webhooks (Starter)
                                │  │  - Manuell          │  │
                                │  └─────────────────────┘  │      ┌──────────────┐
                                │  Lebensmittel-Suche ──────┼────▶ │ Open Food    │
                                │                           │      │ Facts / USDA │
                                └────────────┬──────────────┘      └──────────────┘
                                             ▼
                                       PostgreSQL
```

### Tech-Stack (Empfehlung)

- **TypeScript durchgängig:** Next.js (App Router) als PWA mit API-Routen
- **PostgreSQL** + Drizzle ORM (Migrationen im Repo)
- **Sync-Job:** zeitgesteuert (z. B. alle 15 Min.) und beim Öffnen der App
- **Barcode-Scan:** `@zxing/browser` (funktioniert auch in iOS Safari)
- **Lebensmittel:** Open Food Facts (gut für deutsche Markenprodukte mit
  Barcode, Lizenz ODbL → Quellenangabe), USDA FoodData Central für
  Grundnahrungsmittel, dazu eigene Lebensmittel
- **Hosting:** kleiner Server oder PaaS mit Postgres; für die Starter-Stufe
  ist ein öffentlicher HTTPS-Endpunkt für Webhooks Pflicht

Eine native App (React Native/Expo) lohnt sich erst, wenn Push-Benachrichtigungen
oder Offline-Betrieb wichtig werden. Die Kernlogik bleibt dann wiederverwendbar.

### Projektstruktur

```
src/
  app/                 UI-Seiten + API-Routen (Next.js)
  lib/calories/        Bilanz-, Ziel- und Prognoselogik (reine Funktionen, getestet)
  lib/datasources/     Adapter-Interface + Garmin-/Manuell-Implementierung
  lib/foods/           Open Food Facts / USDA Clients, Normalisierung auf 100 g
  db/                  Schema + Migrationen
  jobs/sync.ts         Garmin-Pull
```

Der **DataSource-Adapter** ist bewusst eingeplant: Garmin kann den Vertrag mit
30 Tagen Frist kündigen und Gebühren einführen. Mit dem Adapter lassen sich
später auch Apple Health oder Health Connect anbinden.

## 4. Datenmodell

```
users              id, timezone, created_at
garmin_connections user_id, key/token (verschlüsselt), expires_at, tier, last_sync_at
energy_days        user_id, date, bmr_kcal, active_kcal, duration_s,
                   source, source_summary_id, raw jsonb, updated_at
                   UNIQUE(user_id, date, source)
activities         user_id, source_id, start_at, type, duration_s, active_kcal
foods              id, source (off|usda|custom), barcode, name, brand,
                   kcal_100g, protein_100g, carbs_100g, fat_100g, servings jsonb
food_entries       id, user_id, date, meal (frühstück|mittag|abend|snack),
                   food_id, amount_g, kcal, protein, carbs, fat   ← Snapshot!
weights            user_id, date, kg, source (garmin|manual)
goals              user_id, valid_from, mode, target_kcal, deficit_kcal,
                   active_factor, macro_split
```

- **Snapshot der Nährwerte** im Eintrag: wird ein Lebensmittel später
  korrigiert, bleibt die Historie gleich.
- `raw jsonb` speichert die Original-Payload von Garmin, damit man später
  nachrechnen kann.
- Daten immer als **lokaler Kalendertag** des Nutzers speichern (Garmin liefert
  `calendarDate` und den Zeitzonen-Offset mit).

## 5. Kalorienlogik

```
verbrannt_bisher   = bmr_kcal + active_kcal                 (aus Garmin)
bmr_prognose       = bmr_kcal * 86400 / duration_s           (Teil-Tag hochrechnen)
verbrannt_prognose = bmr_prognose + active_kcal

Ziel (Modus „dynamisch"):
  ziel = bmr_prognose + active_kcal * active_factor − deficit_kcal
Ziel (Modus „fix"):
  ziel = target_kcal

übrig = ziel − gegessen
```

- `active_factor` einstellbar (z. B. 0,75), weil Uhren aktive Kalorien oft
  überschätzen.
- Fehlen noch Garmin-Daten für heute, den BMR vom Vortag als Schätzung
  nehmen und in der UI als Schätzung kennzeichnen.
- Später: **adaptive Kalibrierung.** Über 2–4 Wochen die Gewichtsveränderung
  (geglätteter Trend) mit der Kalorienbilanz vergleichen und daraus einen
  persönlichen Korrekturfaktor für Garmins Verbrauch berechnen.

Diese Logik kommt als reine Funktionen nach `lib/calories` und bekommt die
meisten Unit-Tests: Teil-Tage, fehlende Tage, Zeitzonenwechsel, Upsert mit
älteren Payloads.

## 6. Funktionen nach Phasen

### Phase 0: Vorbereitung
- [ ] Personal-Key in Garmin Connect anlegen, Token-Ablauf und Endpunkte prüfen
- [ ] Mit einem Skript echte Daily Summaries abrufen und eine Beispiel-Payload speichern
- [ ] Repo-Setup: Next.js, Postgres (Docker), Linting, Tests, CI

### Phase 1: MVP (nur für dich)
- [ ] Garmin-Pull-Sync: Daily Summaries + Activities, Upsert pro Tag
- [ ] Lebensmittel suchen (Open Food Facts), Barcode-Scan, eigene Lebensmittel
- [ ] Einträge nach Mahlzeit, „Zuletzt verwendet", Favoriten, Schnelleingabe nur kcal
- [ ] Dashboard heute: Ziel / gegessen / verbrannt (bisher + Prognose) / übrig, Makros
- [ ] Ziel-Einstellungen (fix oder dynamisch, Defizit, Aktiv-Faktor)
- [ ] Hinweis „Daten von Garmin" in der UI (Garmin verlangt eine Quellenangabe)

### Phase 2: Auswertung
- [ ] Wochen- und Monatsansicht (Bilanz-Balken, Durchschnitt)
- [ ] Gewicht: aus Garmin-Waage oder manuell, geglätteter Trend
- [ ] Rezepte und gespeicherte Mahlzeiten
- [ ] CSV-Export
- [ ] Adaptive Kalibrierung des Verbrauchs

### Phase 3: Für andere Nutzer (nur bei Bedarf)
- [ ] Antrag auf Starter-Stufe (Datenschutzerklärung, Geschäftsmodell, Dienstleister angeben)
- [ ] OAuth-Verbindung pro Nutzer, Webhook-Endpunkte (Push) mit Signaturprüfung und Idempotenz
- [ ] Accounts, DSGVO: Datenexport, Löschung, Widerruf der Garmin-Verbindung
- [ ] Optional: native App

### Optional: KI-Funktionen
- Mahlzeit per Foto oder Freitext erfassen („2 Brötchen mit Käse")
- **Achtung, Garmin-AGB 15.10:** KI-Verarbeitung von Garmin-Daten braucht einen
  deutlich sichtbaren Transparenzhinweis und eine ausdrückliche, widerrufbare
  Einwilligung. Am einfachsten: der KI nur Essensdaten schicken, keine Garmin-Daten.

## 7. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Garmin ändert Bedingungen, Preise oder kündigt (30 Tage Frist) | DataSource-Adapter, manuelle Eingabe als Fallback, Rohdaten lokal speichern |
| Personal-Key läuft ab (max. 1 Jahr, keine Refresh-Tokens) | Ablaufdatum speichern, rechtzeitig in der App erinnern |
| Teil-Tage und mehrfach gelieferte Summaries | Upsert per (user, date, source), Prognoselogik, Tests |
| Aktive Kalorien überschätzt | Aktiv-Faktor, später adaptive Kalibrierung |
| Lebensmitteldaten unvollständig oder falsch (Open Food Facts) | Eigene Korrekturen speichern, Werte plausibilisieren (kcal ≈ 4·P + 4·KH + 9·F) |
| Gesundheitsdaten = besondere Kategorie (DSGVO Art. 9) | Ab Phase 3: Einwilligung, Verschlüsselung, Hosting in der EU |

## 8. Offene Entscheidungen

1. **Nur für dich oder auch für andere?** → Personal- oder Starter-Stufe (bestimmt Pull/Push und Aufwand)
2. **Plattform:** reicht eine PWA im Handy-Browser, oder muss es eine native App sein?
3. **Soll das Essen auch in Garmin Connect auftauchen?** Offiziell geht das nicht (keine Ernährungs-API).
4. **Stack:** passt TypeScript/Next.js, oder lieber z. B. Python (FastAPI)?

## 9. Umsetzung: Artefakt „Kalorienkonto“ (01.11.2026)

Die erste Version läuft als Artefakt auf claude.ai:
https://claude.ai/artifact/YYzseUMsmzH3HMGy3BEVLZ (privat, nur für dich sichtbar)

Quelltext: `app/kalorienkonto.html`. Abrufskript: `scripts/garmin-pull.mjs`.
Tests für den Import: `node --test tests/garmin-import.test.mjs`.

### Was drin ist
- **Tag:** Kontoblatt *Ziel − gegessen = übrig*. Balken für den Verbrauch
  (Grundumsatz bisher, Aktivkalorien, Hochrechnung bis Tagesende) und für das
  Gegessene mit Zielmarke. Dazu Makros und Mahlzeiten (Frühstück, Mittag,
  Abend, Snacks). Löschen lässt sich rückgängig machen.
- **Eintragen:** Suche in rund 70 Lebensmitteln mit Richtwerten, eigene
  Lebensmittel, „Zuletzt gegessen“, direkte kcal-Eingabe und
  **„Claude schätzt“** aus Text oder Foto (Teller oder Nährwerttabelle).
- **Garmin-Daten:** Import von JSON oder CSV. Erkannt werden Daily Summaries
  der Health API, die Ausgabe von `garmin-pull.mjs`, der Garmin-Datenexport
  (`UDSFile_….json`) und CSV mit `Datum;Grundumsatz;Aktiv`. Teil-Tage werden
  hochgerechnet. Von mehreren Lieferungen für denselben Tag gewinnt die
  vollständigste. Werte lassen sich auch von Hand eintragen.
- **Woche:** Balkendiagramm gegessen gegen verbrannt mit Zielmarken, Ø-Bilanz,
  Gesamtbilanz (Faustregel 7.700 kcal ≈ 1 kg) und Tabelle.
- **Ziele:** dynamisches Ziel (Grundumsatz + Anteil der Aktivkalorien − Defizit,
  mit Untergrenze) oder fester Wert, Makro-Verteilung und Profil für die
  Grundumsatz-Schätzung nach Mifflin-St Jeor an Tagen ohne Garmin-Daten.
- **Speicher:** privater Bereich pro Person im Artefakt
  (`data/users/<id>/…`). Auch wer den Link bekommt, sieht deine Daten nicht.

### Abweichungen vom Plan und warum
| Plan | Umsetzung | Grund |
|---|---|---|
| Backend holt Garmin-Daten selbst ab | Import von Datei oder Einfügen, plus Abrufskript | Artefakte dürfen keine fremden Server aufrufen, und es gibt keinen Garmin-Connector für claude.ai |
| Open Food Facts als Lebensmitteldatenbank | Eingebaute Richtwertliste, eigene Lebensmittel, Schätzung durch Claude | Gleicher Grund, keine Netzwerkzugriffe aus dem Artefakt |
| Barcode-Scan | Foto der Nährwerttabelle, ausgewertet von Claude | Kamera ist im Artefakt nicht verfügbar |
| PostgreSQL | Datenbank des Artefakts | Kein eigener Server nötig |

Garmin-AGB 15.10 (KI-Verarbeitung): Claude bekommt nur Essensbeschreibungen
und Fotos, keine Garmin-Daten. Die Seite zeigt die Quelle „Garmin“ an und weist
darauf hin, dass sie kein Garmin-Produkt ist.

### Nächste Schritte
- [ ] Personal-Key anlegen und `garmin-pull.mjs` gegen die echte API prüfen
      (Endpunkt, Parameter, Token-Ablauf)
- [ ] Beispieldaten in der App entfernen und eigene Daten importieren
- [ ] Gewichtsverlauf und adaptive Kalibrierung (Phase 2)
- [ ] Falls der Import auf Dauer zu umständlich wird: kleiner Sync-Dienst mit
      Personal-Key, der die Werte regelmäßig abholt

## Quellen

- [DC Rainmaker: Garmin Revamps and Opens Up Connect Developer API Program](https://www.dcrainmaker.com/2026/10/garmin-connect-api-revamps-connect-developer.html)
- [Sahha: Garmin API Access in 2026: New Tiers, Fees and Terms](https://sahha.ai/blog/garmin-connect-api-reopened-fees/)
- [the5krunner: Garmin Freezes Developer API](https://the5krunner.com/2026/09/14/garmin-developer-api-access-paused/)
- [Terra Docs: Garmin (OAuth-1.0-Abschaltung 31.12.2026)](https://docs.tryterra.co/unified-api/garmin)
- [Fitabase Garmin Data Dictionary (Feldnamen Dailies)](https://fitabase.com/media/2193/fitabase-garmin-data-dictionary.pdf)
- [garth (eingestellt)](https://pypi.org/p/garth), [python-garminconnect](https://pypi.org/project/garminconnect/0.3.2/)
