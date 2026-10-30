"""Überführt die Garmin-Rohdaten in aufgeräumte Tabellen.

Die Garmin-Antworten sind tief verschachtelt und ändern sich gelegentlich.
Alle Zugriffe laufen daher über ``pick`` mit mehreren möglichen Pfaden;
fehlende Felder werden zu NaN statt zu einem Absturz.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

SPORT_GROUPS = {
    "run": ("running", "trail", "treadmill", "track", "ultra"),
    "ride": ("cycling", "biking", "ride", "gravel", "mountain", "virtual_ride", "indoor_cycling"),
    "swim": ("swim",),
    "strength": ("strength", "fitness_equipment", "hiit", "pilates", "yoga", "cardio"),
    "walk": ("walking", "hiking"),
}

PR_TYPES = {
    1: "1 km",
    2: "1 Meile",
    3: "5 km",
    4: "10 km",
    5: "Halbmarathon",
    6: "Marathon",
    7: "Längster Lauf",
    8: "Längste Radfahrt",
    9: "Höhenmeter (Rad)",
    10: "Max. 20-min-Leistung",
    12: "Meiste Schritte (Tag)",
    13: "Meiste Schritte (Woche)",
    14: "Meiste Schritte (Monat)",
    15: "Längste Ziel-Serie",
    17: "Schnellster 40-km-Ritt",
}


def pick(obj: Any, *paths: str, default: Any = None) -> Any:
    """Liefert den ersten vorhandenen Wert aus mehreren Punkt-Pfaden.

    ``*`` steht für "den ersten Eintrag" eines Dicts oder einer Liste, was bei
    Garmins geräte-ID-basierten Maps (``{"3456789": {...}}``) nötig ist.
    """
    for path in paths:
        cur = obj
        ok = True
        for key in path.split("."):
            if cur is None:
                ok = False
                break
            if key == "*":
                if isinstance(cur, dict) and cur:
                    cur = next(iter(cur.values()))
                elif isinstance(cur, list) and cur:
                    cur = cur[0]
                else:
                    ok = False
                    break
            elif isinstance(cur, dict):
                if key not in cur:
                    ok = False
                    break
                cur = cur[key]
            elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
                cur = cur[int(key)]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return default


def num(x: Any) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else np.nan
    except (TypeError, ValueError):
        return np.nan


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _daily_files(raw: Path, name: str) -> Iterable[tuple[str, Any]]:
    folder = raw / "daily" / name
    if not folder.exists():
        return []
    return ((p.stem, _read(p)) for p in sorted(folder.glob("*.json")))


def _range(raw: Path, name: str) -> list[Any]:
    data = _read(raw / "range" / f"{name}.json")
    return data if isinstance(data, list) else ([] if data is None else [data])


def _records_with_date(obj: Any, out: list | None = None) -> list[dict]:
    """Sucht rekursiv alle Dicts, die ein ``calendarDate`` enthalten."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        if "calendarDate" in obj:
            out.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _records_with_date(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _records_with_date(v, out)
    return out


def _local_clock(ms: Any) -> float:
    """Garmins *Local*-Zeitstempel sind Millisekunden "als wäre es UTC"."""
    v = num(ms)
    if np.isnan(v):
        return np.nan
    t = dt.datetime.fromtimestamp(v / 1000, tz=dt.timezone.utc)
    return t.hour + t.minute / 60


def sport_group(type_key: str) -> str:
    key = (type_key or "").lower()
    for group, needles in SPORT_GROUPS.items():
        if any(n in key for n in needles):
            return group
    return "other"


# --------------------------------------------------------------------------- #
# Tageswerte


def _daily_rows(raw: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    def row(d: str) -> dict[str, Any]:
        return rows.setdefault(d, {"date": d})

    for d, s in _daily_files(raw, "summary"):
        if not isinstance(s, dict):
            continue
        r = row(d)
        r.update(
            steps=num(s.get("totalSteps")),
            step_goal=num(s.get("dailyStepGoal")),
            kcal_total=num(s.get("totalKilocalories")),
            kcal_active=num(s.get("activeKilocalories")),
            distance_km=num(s.get("totalDistanceMeters")) / 1000,
            floors=num(s.get("floorsAscended")),
            rhr=num(s.get("restingHeartRate")),
            hr_min=num(s.get("minHeartRate")),
            hr_max=num(s.get("maxHeartRate")),
            stress_avg=num(s.get("averageStressLevel")),
            stress_max=num(s.get("maxStressLevel")),
            stress_high_min=num(s.get("highStressDuration")) / 60,
            stress_rest_min=num(s.get("restStressDuration")) / 60,
            bb_high=num(s.get("bodyBatteryHighestValue")),
            bb_low=num(s.get("bodyBatteryLowestValue")),
            bb_charged=num(s.get("bodyBatteryChargedValue")),
            bb_drained=num(s.get("bodyBatteryDrainedValue")),
            bb_wake=num(s.get("bodyBatteryAtWakeTime")),
            im_moderate=num(s.get("moderateIntensityMinutes")),
            im_vigorous=num(s.get("vigorousIntensityMinutes")),
            sedentary_h=num(s.get("sedentarySeconds")) / 3600,
            spo2_avg=num(s.get("averageSpo2")),
            spo2_low=num(s.get("lowestSpo2")),
            resp_waking=num(s.get("avgWakingRespirationValue")),
        )

    sleep_end_gmt: dict[str, float] = {}
    for d, s in _daily_files(raw, "sleep"):
        # garminconnect liefert {"dailySleepDTO": {...}}, die Personal API das DTO direkt
        dto = pick(s, "dailySleepDTO") or (s if isinstance(s, dict) and "sleepTimeSeconds" in s else {})
        total = num(dto.get("sleepTimeSeconds"))
        if np.isnan(total) or total <= 0:
            continue
        r = row(d)
        start = _local_clock(dto.get("sleepStartTimestampLocal"))
        end = _local_clock(dto.get("sleepEndTimestampLocal"))
        # Einschlafzeit auf eine durchgehende Skala bringen (18 Uhr .. 42 Uhr)
        if not np.isnan(start) and start < 18:
            start += 24
        r.update(
            sleep_h=total / 3600,
            deep_h=num(dto.get("deepSleepSeconds")) / 3600,
            light_h=num(dto.get("lightSleepSeconds")) / 3600,
            rem_h=num(dto.get("remSleepSeconds")) / 3600,
            awake_h=num(dto.get("awakeSleepSeconds")) / 3600,
            sleep_score=num(pick(dto, "sleepScores.overall.value", "sleepScore.value")),
            sleep_start=start,
            sleep_end=end,
            sleep_resp=num(dto.get("averageRespirationValue")),
            sleep_stress=num(dto.get("avgSleepStress")),
            sleep_spo2=num(dto.get("averageSpO2Value")),
            sleep_hrv=num(pick(s, "avgOvernightHrv")),
            sleep_bb_change=num(pick(s, "bodyBatteryChange")),
            restless=num(pick(s, "restlessMomentsCount")),
            sleep_need_h=num(pick(s, "sleepNeed.actual", "dailySleepDTO.sleepNeed.actual")) / 60,
        )
        sleep_end_gmt[d] = num(dto.get("sleepEndTimestampGMT"))

    for d, h in _daily_files(raw, "hrv"):
        summ = pick(h, "hrvSummary") or {}
        if not summ:
            continue
        r = row(d)
        r.update(
            hrv=num(summ.get("lastNightAvg")),
            hrv_weekly=num(summ.get("weeklyAvg")),
            hrv_5min_high=num(summ.get("lastNight5MinHigh")),
            hrv_base_low=num(pick(summ, "baseline.balancedLow")),
            hrv_base_high=num(pick(summ, "baseline.balancedUpper")),
            hrv_status=summ.get("status"),
        )

    for d, t in _daily_files(raw, "readiness"):
        items = t if isinstance(t, list) else [t]
        items = [i for i in items if isinstance(i, dict) and i.get("score") is not None]
        if not items:
            continue
        # Der Morgenwert (nach dem Aufwachen) ist am aussagekräftigsten.
        morning = [i for i in items if "WAKEUP" in str(i.get("inputContext", ""))]
        it = (morning or items)[-1]
        r = row(d)
        r.update(
            readiness=num(it.get("score")),
            readiness_level=it.get("level"),
            readiness_feedback=it.get("feedbackShort"),
            recovery_time_h=num(it.get("recoveryTime")) / 60,
            readiness_acute_load=num(it.get("acuteLoad")),
        )

    for d, t in _daily_files(raw, "training_status"):
        if not isinstance(t, dict):
            continue
        r = row(d)
        status = pick(t, "mostRecentTrainingStatus.latestTrainingStatusData.*") or {}
        acute = status.get("acuteTrainingLoadDTO") or {}
        bal = pick(t, "mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap.*") or {}
        r.update(
            vo2max=num(pick(t, "mostRecentVO2Max.generic.vo2MaxPreciseValue", "mostRecentVO2Max.generic.vo2MaxValue")),
            vo2max_cycling=num(pick(t, "mostRecentVO2Max.cycling.vo2MaxPreciseValue", "mostRecentVO2Max.cycling.vo2MaxValue")),
            training_status=status.get("trainingStatusFeedbackPhrase"),
            garmin_acute_load=num(acute.get("dailyTrainingLoadAcute")),
            garmin_chronic_load=num(acute.get("dailyTrainingLoadChronic")),
            garmin_acwr=num(acute.get("dailyAcuteChronicWorkloadRatio")),
            load_aer_low=num(bal.get("monthlyLoadAerobicLow")),
            load_aer_high=num(bal.get("monthlyLoadAerobicHigh")),
            load_anaer=num(bal.get("monthlyLoadAnaerobic")),
            load_balance=bal.get("trainingBalanceFeedbackPhrase"),
        )

    for d, m in _daily_files(raw, "max_metrics"):
        if not m:
            continue
        r = row(d)
        v = num(pick(m, "*.generic.vo2MaxPreciseValue", "*.generic.vo2MaxValue"))
        if np.isnan(r.get("vo2max", np.nan)):
            r["vo2max"] = v
        r["fitness_age"] = num(pick(m, "*.generic.fitnessAge"))

    for d, x in _daily_files(raw, "respiration"):
        if isinstance(x, dict):
            row(d)["resp_sleep"] = num(x.get("avgSleepRespirationValue"))

    for d, x in _daily_files(raw, "spo2"):
        if isinstance(x, dict):
            row(d)["spo2_sleep"] = num(x.get("avgSleepSpO2"))

    for d, x in _daily_files(raw, "hydration"):
        if isinstance(x, dict) and x.get("valueInML"):
            row(d)["water_l"] = num(x.get("valueInML")) / 1000

    _body_battery(raw, row, sleep_end_gmt)
    return rows


def _body_battery(raw: Path, row: Any, sleep_end_gmt: dict[str, float]) -> None:
    """Body Battery aus dem Personal-API-Bericht (Zeitreihe je Tag).

    Füllt nur Felder, die die Tageszusammenfassung nicht schon geliefert hat.
    """
    for chunk in _range(raw, "body_battery"):
        for day in chunk if isinstance(chunk, list) else []:
            d = day.get("date") if isinstance(day, dict) else None
            if not d:
                continue
            desc = {x.get("bodyBatteryValueDescriptorKey"): x.get("bodyBatteryValueDescriptorIndex") for x in day.get("bodyBatteryValueDescriptorDTOList") or []}
            ti, vi = desc.get("timestamp", 0), desc.get("bodyBatteryLevel", 1)
            pts = [
                (num(v[ti]), num(v[vi]))
                for v in day.get("bodyBatteryValuesArray") or []
                if isinstance(v, list) and len(v) > max(ti, vi)
            ]
            pts = [(t, v) for t, v in pts if not (np.isnan(t) or np.isnan(v))]
            if not pts:
                continue
            values = [v for _, v in pts]
            wake = np.nan
            end = sleep_end_gmt.get(d, np.nan)
            if not np.isnan(end):
                after = [v for t, v in pts if t >= end]
                wake = after[0] if after else np.nan
            r = row(d)
            for key, val in (
                ("bb_high", max(values)),
                ("bb_low", min(values)),
                ("bb_charged", num(day.get("charged"))),
                ("bb_drained", num(day.get("drained"))),
                ("bb_wake", wake),
            ):
                if np.isnan(num(r.get(key))):
                    r[key] = val


def _body(raw: Path) -> pd.DataFrame:
    recs = []
    for chunk in _range(raw, "body_composition"):
        for w in pick(chunk, "dateWeightList", default=[]) or []:
            weight = num(w.get("weight"))
            if np.isnan(weight):
                continue
            recs.append(
                {
                    "date": w.get("calendarDate"),
                    "weight_kg": weight / 1000 if weight > 1000 else weight,
                    "body_fat": num(w.get("bodyFat")),
                    "muscle_kg": num(w.get("muscleMass")) / 1000,
                    "body_water": num(w.get("bodyWater")),
                    "bmi": num(w.get("bmi")),
                }
            )
    # Personal API: weight/range -> dailyWeightSummaries[].allWeightMetrics / latestWeight
    for chunk in _range(raw, "weigh_ins"):
        for summ in pick(chunk, "dailyWeightSummaries", default=[]) or []:
            metrics = summ.get("allWeightMetrics") or ([summ["latestWeight"]] if summ.get("latestWeight") else [])
            for w in metrics:
                weight = num(w.get("weight"))
                if np.isnan(weight):
                    continue
                recs.append(
                    {
                        "date": w.get("calendarDate") or summ.get("summaryDate"),
                        "weight_kg": weight / 1000 if weight > 1000 else weight,
                        "body_fat": num(w.get("bodyFat")),
                        "muscle_kg": num(w.get("muscleMass")) / 1000,
                        "body_water": num(w.get("bodyWater")),
                        "bmi": num(w.get("bmi")),
                    }
                )
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.groupby("date").mean(numeric_only=True).sort_index()


def _activities(raw: Path) -> pd.DataFrame:
    seen = set()
    recs = []
    for chunk in _range(raw, "activities"):
        for a in chunk or []:
            aid = a.get("activityId")
            if aid in seen:
                continue
            seen.add(aid)
            type_key = pick(a, "activityType.typeKey", default="other")
            start = pd.to_datetime(a.get("startTimeLocal"), errors="coerce")
            if pd.isna(start):
                continue
            dur = num(a.get("duration"))
            recs.append(
                {
                    "id": aid,
                    "date": start.normalize(),
                    "start": start,
                    "end_hour": start.hour + start.minute / 60 + (dur / 3600 if not np.isnan(dur) else 0),
                    "name": a.get("activityName") or type_key,
                    "type": type_key,
                    "sport": sport_group(type_key),
                    "duration_min": dur / 60,
                    "distance_km": num(a.get("distance")) / 1000,
                    "elev_m": num(a.get("elevationGain")),
                    "avg_hr": num(a.get("averageHR")),
                    "max_hr": num(a.get("maxHR")),
                    "avg_speed": num(a.get("averageSpeed")),  # m/s
                    "avg_power": num(a.get("avgPower")),
                    "norm_power": num(a.get("normPower")),
                    "cadence": num(a.get("averageRunningCadenceInStepsPerMinute")),
                    "kcal": num(a.get("calories")),
                    "te_aer": num(a.get("aerobicTrainingEffect")),
                    "te_anaer": num(a.get("anaerobicTrainingEffect")),
                    "garmin_load": num(a.get("activityTrainingLoad")),
                    "te_label": a.get("trainingEffectLabel"),
                    "vo2max": num(a.get("vO2MaxValue")),
                    "gct": num(a.get("avgGroundContactTime")),
                    "vert_osc": num(a.get("avgVerticalOscillation")),
                    "stride_m": num(a.get("avgStrideLength")) / 100,
                    "bb_delta": num(a.get("differenceBodyBattery")),
                    **{f"z{i}_min": num(a.get(f"hrTimeInZone_{i}")) / 60 for i in range(1, 6)},
                }
            )
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    return df.sort_values("start").reset_index(drop=True)


def _race_predictions(raw: Path) -> pd.DataFrame:
    recs = []
    for r in _records_with_date(_range(raw, "race_predictions")):
        recs.append(
            {
                "date": r.get("calendarDate"),
                "t5k": num(r.get("time5K")),
                "t10k": num(r.get("time10K")),
                "thm": num(r.get("timeHalfMarathon")),
                "tm": num(r.get("timeMarathon")),
            }
        )
    latest = _read(raw / "static" / "race_predictions_latest.json")
    if isinstance(latest, dict) and latest.get("time5K"):
        recs.append(
            {
                "date": latest.get("calendarDate") or dt.date.today().isoformat(),
                "t5k": num(latest.get("time5K")),
                "t10k": num(latest.get("time10K")),
                "thm": num(latest.get("timeHalfMarathon")),
                "tm": num(latest.get("timeMarathon")),
            }
        )
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"]).groupby("date").last().sort_index()


def _score_history(raw: Path, name: str, keys: tuple[str, ...]) -> pd.DataFrame:
    recs = []
    for r in _records_with_date(_range(raw, name)):
        val = next((num(r.get(k)) for k in keys if not np.isnan(num(r.get(k)))), np.nan)
        if not np.isnan(val):
            recs.append({"date": r.get("calendarDate"), "value": val})
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna().groupby("date").last().sort_index()


def _static(raw: Path) -> dict[str, Any]:
    st: dict[str, Any] = {}
    folder = raw / "static"
    if folder.exists():
        for p in folder.glob("*.json"):
            st[p.stem] = _read(p)
    return st


def _profile(st: dict[str, Any]) -> dict[str, Any]:
    info = st.get("personal_information") or {}
    bio = pick(info, "biometricProfile") or {}
    prof = st.get("profile") or {}
    zones = st.get("heart_rate_zones") or []
    zone = next((z for z in zones if isinstance(z, dict) and z.get("sport") in ("DEFAULT", None)), zones[0] if zones else {})
    lt = st.get("lactate_threshold_latest") or {}
    lt_speed = num(pick(lt, "speed_and_heart_rate.speed"))
    fa = st.get("fitness_age") or {}
    birth = pick(info, "userInfo.birthDate")
    age = np.nan
    if birth:
        try:
            b = dt.date.fromisoformat(str(birth)[:10])
            today = dt.date.today()
            age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
        except ValueError:
            pass
    return {
        "name": pick(st, "full_name.fullName") or pick(prof, "fullName") or _readable_name(prof.get("displayName")) or "Athlet:in",
        "age": num(age if not np.isnan(age) else fa.get("chronologicalAge")),
        "gender": pick(info, "userInfo.genderType"),
        "height_cm": num(bio.get("height")),
        "weight_kg": num(bio.get("weight")) / (1000 if num(bio.get("weight")) > 1000 else 1),
        "vo2max_profile": num(bio.get("vo2Max")),
        "max_hr": num(zone.get("maxHeartRateUsed")),
        "rest_hr": num(zone.get("restingHeartRateUsed")),
        "lthr": num(pick(lt, "speed_and_heart_rate.heartRate", default=zone.get("lactateThresholdHeartRateUsed"))),
        "lt_speed": lt_speed if 1.5 < lt_speed < 7 else np.nan,
        "ftp": num(pick(st, "cycling_ftp.functionalThresholdPower", "cycling_ftp.0.functionalThresholdPower", "lactate_threshold_latest.power.functionalThresholdPower")),
        "fitness_age": num(fa.get("fitnessAge")),
        "achievable_fitness_age": num(fa.get("achievableFitnessAge")),
        "hr_zones": {
            "z1": num(zone.get("zone1Floor")),
            "z2": num(zone.get("zone2Floor")),
            "z3": num(zone.get("zone3Floor")),
            "z4": num(zone.get("zone4Floor")),
            "z5": num(zone.get("zone5Floor")),
        },
        "devices": [
            d.get("productDisplayName") or d.get("displayName")
            for d in (st.get("devices") or [])
            if isinstance(d, dict) and (d.get("productDisplayName") or d.get("displayName"))
        ],
    }


def _readable_name(name: Any) -> str | None:
    """displayName ist bei der Personal API eine UUID – die zeigen wir nicht an."""
    if not isinstance(name, str) or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27}", name.lower()):
        return None
    return name


def _personal_records(st: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for pr in st.get("personal_records") or []:
        if not isinstance(pr, dict):
            continue
        tid = pr.get("typeId")
        out.append(
            {
                "type_id": tid,
                "label": PR_TYPES.get(tid, f"Rekord {tid}"),
                "value": num(pr.get("value")),
                "date": str(pr.get("actStartDateTimeInGMTFormatted") or pr.get("prStartTimeGmtFormatted") or "")[:10],
                "activity": pr.get("activityName"),
            }
        )
    return out


def load(data_dir: Path) -> dict[str, Any]:
    raw = Path(data_dir) / "raw"
    if not raw.exists():
        raise FileNotFoundError(
            f"Keine Rohdaten unter {raw}. Erst `python -m garmin_dashboard fetch` "
            "oder `python -m garmin_dashboard demo` ausführen."
        )
    rows = _daily_rows(raw)
    daily = pd.DataFrame(list(rows.values()))
    if not daily.empty:
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.set_index("date").sort_index()
        daily = daily.asfreq("D")
    st = _static(raw)

    details = {}
    det_dir = raw / "activity_details"
    if det_dir.exists():
        for p in det_dir.glob("*.json"):
            details[p.stem] = _read(p)

    return {
        "meta": _read(raw / "meta.json") or {},
        "daily": daily,
        "activities": _activities(raw),
        "body": _body(raw),
        "race_predictions": _race_predictions(raw),
        "endurance_score": _score_history(raw, "endurance_score", ("overallScore", "groupAverage", "enduranceScore")),
        "hill_score": _score_history(raw, "hill_score", ("overallScore", "hillScore")),
        "profile": _profile(st),
        "personal_records": _personal_records(st),
        "activity_details": details,
        "static": st,
    }


def activity_stream(details: Any) -> pd.DataFrame:
    """Wandelt ``get_activity_details`` in eine Zeitreihe (t, hr, speed, power)."""
    descs = pick(details, "metricDescriptors") or []
    rows = pick(details, "activityDetailMetrics") or []
    if not descs or not rows:
        return pd.DataFrame()
    idx = {d.get("key"): d.get("metricsIndex") for d in descs if isinstance(d, dict)}
    wanted = {
        "t": ("sumDuration", "sumElapsedDuration"),
        "hr": ("directHeartRate",),
        "speed": ("directSpeed", "directEnhancedSpeed"),
        "power": ("directPower",),
    }
    cols = {name: next((idx[k] for k in keys if k in idx), None) for name, keys in wanted.items()}
    recs = []
    for r in rows:
        m = r.get("metrics") if isinstance(r, dict) else None
        if not m:
            continue
        recs.append({name: (num(m[i]) if i is not None and i < len(m) else np.nan) for name, i in cols.items()})
    return pd.DataFrame(recs)
