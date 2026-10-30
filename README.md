# Health & Training Dashboard für Garmin

Lädt deine Daten aus Garmin Connect, wertet sie nach sportwissenschaftlichen Modellen aus und erzeugt **ein einziges HTML-Dashboard**. Es läuft offline, ohne CDN und ohne Tracking, und alle Daten bleiben lokal.

```bash
pip install -r requirements.txt

# Ausprobieren ohne Garmin-Konto (simulierte Daten, 1 Jahr)
python -m garmin_dashboard demo --open

# Echte Daten: Login, 180 Tage laden, Dashboard bauen
python -m garmin_dashboard update --days 180 --open
```

Beim ersten Login fragt das Tool nach E-Mail, Passwort und ggf. MFA-Code. Alternativ setzt du die Umgebungsvariablen `GARMIN_EMAIL` und `GARMIN_PASSWORD`. Die Tokens landen in `~/.garminconnect`, danach ist kein Passwort mehr nötig. Weitere Läufe laden nur noch neue Tage, weil ältere Tageswerte zwischengespeichert werden.

| Befehl | Zweck |
|---|---|
| `fetch [--days N] [--details N]` | Rohdaten nach `data/raw` laden (`--details`: Zeitreihen der letzten N Ausdauereinheiten) |
| `build` | Dashboard aus vorhandenen Daten neu erzeugen → `output/dashboard.html` |
| `update` | `fetch` + `build` |
| `demo` | Demodaten erzeugen + Dashboard bauen |
| `--sleep-need 8.5` | persönlicher Schlafbedarf für die Schlafschuld-Berechnung |

> Garmin bietet Privatpersonen keine offizielle API an. Genutzt wird die inoffizielle Bibliothek [`garminconnect`](https://github.com/cyberjunky/python-garminconnect). Endpunkte können sich ändern, und einzelne Fehler werden in `data/raw/meta.json` protokolliert, ohne den Lauf abzubrechen.

## Welche Daten geladen werden

- **Täglich:** Tageszusammenfassung (Schritte, Kalorien, Ruhepuls, Stress, Body Battery, Intensitätsminuten, SpO₂, Atmung), Schlaf inkl. Phasen und Score, HRV, Training Readiness, Trainingsstatus (VO2max, akute/chronische Last, Lastbalance), Max-Metriken, Atmung, SpO₂, Flüssigkeit
- **Zeitraum:** Aktivitäten, Körperzusammensetzung, Wiegungen, Blutdruck, Wettkampfprognosen, Endurance-/Hill-Score, Laktatschwelle, Running Tolerance, Intensitätsminuten und Trainingsstatus pro Woche
- **Stammdaten:** Profil, Geräte, persönliche Rekorde, HF-Zonen, Fitnessalter, Lebenszeit-Statistik, FTP, Ziele, Abzeichen, Events, Trainingspläne
- **Aktivitätsdetails:** HF-, Tempo- und Leistungszeitreihen langer Einheiten (für die aerobe Entkopplung)

Alles wird unverändert als JSON gespeichert. `data/` und `output/` stehen in `.gitignore`, damit Gesundheitsdaten nie im Repository landen.

## Was das Dashboard auswertet

| Bereich | Modell | Quelle |
|---|---|---|
| Fitness / Ermüdung / Form | CTL (42 T.), ATL (7 T.), TSB, Garmin-Last bzw. Banister-TRIMP | Banister 1975, Coggan |
| Belastungssprünge | EWMA-ACWR, Monotonie & Strain, CTL-Rampe | Williams 2017, Foster 1998, Impellizzeri 2020 (Kritik) |
| HRV-gesteuertes Training | ln(rMSSD), 7-Tage-Schnitt vs. Normalbereich (60 T. ± 0,5 SD), CV-Trend | Plews 2012/2013, Kiviniemi 2007 |
| Erholung | eigener Erholungsindex aus HRV-, Ruhepuls- und Schlaf-z-Werten | – (experimentell) |
| Intensitätsverteilung | 3-Zonen-Modell, Polarisierungsindex | Seiler 2010, Treff 2019 |
| Schlaf | Schlafschuld, Regelmäßigkeit, sozialer Jetlag, Phasenanteile | Wittmann 2006, Windred 2024, Mah 2011 |
| Leistung | VO2max-Trend, aerobe Effizienz, Pa:HR-Entkopplung, Riegel-Exponent | Friel, Allen & Coggan, Riegel 1981 |
| Körper & Alltag | Gewichtstrend (RED-S-Warnung), Schritte, WHO-Intensitätsminuten | Mountjoy 2018, WHO 2020 |
| Zusammenhänge | zeitversetzte Spearman-Korrelationen (Bonferroni), Dosis-Wirkung Last → HRV, Wochentagsmuster | – |

Daraus entsteht eine **tägliche Coach-Empfehlung** (Qualität / locker / Erholung) mit Begründung pro Marker, dazu Trainer-Hinweise mit Literaturangaben.

## Aufbau

```
garmin_dashboard/
  fetch.py      Garmin-Login, Abruf aller Endpunkte, Caching
  normalize.py  Rohdaten → Tabellen (robust gegen fehlende Felder)
  analysis.py   Sportwissenschaftliche Modelle, Coach-Regeln
  report.py     Daten in die HTML-Vorlage einbetten
  template.html Dashboard (eigene SVG-Diagramme, Hell/Dunkel, Tabellenansichten)
  demo.py       realistische Demodaten im Garmin-Format
tests/          pytest (u. a. Signaturprüfung aller genutzten Garmin-Methoden)
```

Tests: `pip install pytest && python -m pytest`

*Kein Medizinprodukt. Bei auffälligen Werten oder Beschwerden bitte ärztlich abklären lassen.*
