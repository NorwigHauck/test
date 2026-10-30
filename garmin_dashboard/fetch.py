"""Lädt Rohdaten aus Garmin Connect und speichert sie unverändert als JSON.

Garmin bietet für Privatpersonen keine offizielle API an. Wir verwenden die
inoffizielle Bibliothek `garminconnect`, die dieselben Endpunkte nutzt wie
die Garmin-Connect-Weboberfläche.

Ablage unter ``<data_dir>/raw``::

    daily/<endpunkt>/<YYYY-MM-DD>.json   ein Abruf pro Tag
    range/<endpunkt>.json                ein Abruf für den ganzen Zeitraum
    static/<endpunkt>.json               Profil, Rekorde, Zonen, Geräte, ...
    activity_details/<id>.json           Zeitreihen einzelner Aktivitäten

Tageswerte älter als ``REFRESH_DAYS`` werden nicht erneut geladen, so dass
nach dem ersten (langsamen) Lauf nur noch die letzten Tage abgefragt werden.
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

REFRESH_DAYS = 3
PAUSE_S = 0.35

# Endpunkte, die nur ein Datum annehmen.
DAILY_ENDPOINTS: dict[str, Callable[[Any, str], Any]] = {
    "summary": lambda g, d: g.get_user_summary(d),
    "sleep": lambda g, d: g.get_sleep_data(d),
    "hrv": lambda g, d: g.get_hrv_data(d),
    "readiness": lambda g, d: g.get_training_readiness(d),
    "training_status": lambda g, d: g.get_training_status(d),
    "max_metrics": lambda g, d: g.get_max_metrics(d),
    "respiration": lambda g, d: g.get_respiration_data(d),
    "spo2": lambda g, d: g.get_spo2_data(d),
    "hydration": lambda g, d: g.get_hydration_data(d),
}

# Endpunkte mit Zeitraum (start, end).
RANGE_ENDPOINTS: dict[str, Callable[[Any, str, str], Any]] = {
    "activities": lambda g, s, e: g.get_activities_by_date(s, e),
    "body_composition": lambda g, s, e: g.get_body_composition(s, e),
    "weigh_ins": lambda g, s, e: g.get_weigh_ins(s, e),
    "blood_pressure": lambda g, s, e: g.get_blood_pressure(s, e),
    "race_predictions": lambda g, s, e: g.get_race_predictions(s, e, _type="daily"),
    "endurance_score": lambda g, s, e: g.get_endurance_score(s, e),
    "hill_score": lambda g, s, e: g.get_hill_score(s, e),
    "lactate_threshold": lambda g, s, e: g.get_lactate_threshold(
        latest=False, start_date=s, end_date=e
    ),
    "running_tolerance": lambda g, s, e: g.get_running_tolerance(s, e),
    "weekly_intensity_minutes": lambda g, s, e: g.get_weekly_intensity_minutes(s, e),
    "weekly_training_status": lambda g, s, e: g.get_weekly_training_status(s, e),
}

# Endpunkte ohne Datum bzw. mit "heute".
STATIC_ENDPOINTS: dict[str, Callable[[Any, str], Any]] = {
    "profile": lambda g, today: g.get_user_profile(),
    "personal_information": lambda g, today: g.get_personal_information(),
    "full_name": lambda g, today: {"fullName": g.get_full_name()},
    "devices": lambda g, today: g.get_devices(),
    "primary_training_device": lambda g, today: g.get_primary_training_device(),
    "personal_records": lambda g, today: g.get_personal_record(),
    "race_predictions_latest": lambda g, today: g.get_race_predictions(),
    "lactate_threshold_latest": lambda g, today: g.get_lactate_threshold(),
    "heart_rate_zones": lambda g, today: g.get_heart_rate_zones(),
    "fitness_age": lambda g, today: g.get_fitnessage_data(today),
    "lifetime_totals": lambda g, today: g.get_lifetime_totals(),
    "cycling_ftp": lambda g, today: g.get_cycling_ftp(),
    "goals": lambda g, today: g.get_goals(),
    "earned_badges": lambda g, today: g.get_earned_badges(),
    "upcoming_events": lambda g, today: g.get_upcoming_events(),
    "training_plans": lambda g, today: g.get_active_training_plans(),
}

DETAIL_SPORTS = ("running", "cycling", "trail", "treadmill", "virtual_ride", "road")


def login(tokenstore: str) -> Any:
    """Meldet sich an – bevorzugt mit gespeicherten Tokens, sonst mit Passwort."""
    from garminconnect import Garmin

    tokenstore = str(Path(tokenstore).expanduser())
    try:
        garmin = Garmin()
        garmin.login(tokenstore)
        log.info("Mit gespeicherten Tokens angemeldet (%s)", tokenstore)
        return garmin
    except Exception as exc:  # keine/abgelaufene Tokens
        log.info("Keine gültigen Tokens (%s) – Anmeldung mit Zugangsdaten.", exc)

    email = os.getenv("GARMIN_EMAIL") or input("Garmin-E-Mail: ")
    password = os.getenv("GARMIN_PASSWORD") or getpass.getpass("Garmin-Passwort: ")
    garmin = Garmin(email, password, prompt_mfa=lambda: input("MFA-Code: "))
    Path(tokenstore).mkdir(parents=True, exist_ok=True, mode=0o700)
    garmin.login(tokenstore)
    log.info("Angemeldet, Tokens gespeichert unter %s", tokenstore)
    return garmin


class Fetcher:
    def __init__(self, garmin: Any, data_dir: Path):
        self.g = garmin
        self.raw = Path(data_dir) / "raw"
        self.errors: list[str] = []
        self.calls = 0

    def _call(self, label: str, fn: Callable[[], Any]) -> Any:
        from garminconnect import (
            GarminConnectNotFoundError,
            GarminConnectTooManyRequestsError,
        )

        for attempt in range(3):
            try:
                self.calls += 1
                result = fn()
                time.sleep(PAUSE_S)
                return result
            except GarminConnectTooManyRequestsError:
                wait = 60 * (attempt + 1)
                log.warning("Rate-Limit bei %s – warte %ss", label, wait)
                time.sleep(wait)
            except GarminConnectNotFoundError:
                return None
            except Exception as exc:  # einzelne Endpunkte dürfen fehlschlagen
                self.errors.append(f"{label}: {type(exc).__name__}: {exc}")
                log.debug("Fehler bei %s: %s", label, exc)
                return None
        self.errors.append(f"{label}: Rate-Limit")
        return None

    @staticmethod
    def _save(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, default=str))

    def fetch_daily(self, start: dt.date, end: dt.date) -> None:
        today = dt.date.today()
        days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
        for i, day in enumerate(days, 1):
            ds = day.isoformat()
            fresh = (today - day).days < REFRESH_DAYS
            for name, fn in DAILY_ENDPOINTS.items():
                path = self.raw / "daily" / name / f"{ds}.json"
                if path.exists() and not fresh:
                    continue
                data = self._call(f"{name} {ds}", lambda fn=fn: fn(self.g, ds))
                if data is not None:
                    self._save(path, data)
            if i % 10 == 0 or i == len(days):
                log.info("Tageswerte: %d/%d Tage", i, len(days))

    def fetch_ranges(self, start: dt.date, end: dt.date) -> None:
        # Manche Endpunkte erlauben max. 1 Jahr – daher in Blöcken laden.
        for name, fn in RANGE_ENDPOINTS.items():
            chunks = []
            s = start
            while s <= end:
                e = min(end, s + dt.timedelta(days=360))
                data = self._call(
                    f"{name} {s}..{e}",
                    lambda fn=fn, s=s, e=e: fn(self.g, s.isoformat(), e.isoformat()),
                )
                if data is not None:
                    chunks.append(data)
                s = e + dt.timedelta(days=1)
            if chunks:
                self._save(self.raw / "range" / f"{name}.json", chunks)
            log.info("Zeitraum-Endpunkt %s geladen", name)

    def fetch_static(self) -> None:
        today = dt.date.today().isoformat()
        for name, fn in STATIC_ENDPOINTS.items():
            data = self._call(name, lambda fn=fn: fn(self.g, today))
            if data is not None:
                self._save(self.raw / "static" / f"{name}.json", data)
        log.info("Profil, Rekorde, Zonen und Geräte geladen")

    def fetch_activity_details(self, limit: int) -> None:
        """Lädt Zeitreihen (HF, Tempo, Leistung) für Ausdauer-Aktivitäten.

        Werden für die aerobe Entkopplung (Pa:HR) benötigt.
        """
        path = self.raw / "range" / "activities.json"
        if not path.exists() or limit <= 0:
            return
        acts = [a for chunk in json.loads(path.read_text()) for a in (chunk or [])]
        acts.sort(key=lambda a: a.get("startTimeLocal") or "", reverse=True)
        todo = [
            a
            for a in acts
            if any(s in ((a.get("activityType") or {}).get("typeKey") or "") for s in DETAIL_SPORTS)
            and (a.get("duration") or 0) >= 30 * 60
        ][:limit]
        for a in todo:
            aid = a["activityId"]
            target = self.raw / "activity_details" / f"{aid}.json"
            if target.exists():
                continue
            data = self._call(f"details {aid}", lambda aid=aid: self.g.get_activity_details(aid))
            if data is not None:
                self._save(target, data)
        log.info("Aktivitätsdetails: %d Aktivitäten geprüft", len(todo))


def fetch_all(data_dir: Path, days: int, tokenstore: str, details: int = 40) -> None:
    garmin = login(tokenstore)
    end = dt.date.today()
    start = end - dt.timedelta(days=days - 1)
    f = Fetcher(garmin, data_dir)
    log.info("Lade Garmin-Daten %s bis %s", start, end)
    f.fetch_static()
    f.fetch_ranges(start, end)
    f.fetch_daily(start, end)
    f.fetch_activity_details(details)
    meta = {
        "source": "garmin",
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "api_calls": f.calls,
        "errors": f.errors[-200:],
    }
    Fetcher._save(Path(data_dir) / "raw" / "meta.json", meta)
    log.info("Fertig: %d API-Aufrufe, %d Fehler", f.calls, len(f.errors))
