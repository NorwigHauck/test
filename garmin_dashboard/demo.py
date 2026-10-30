"""Erzeugt realistische Demo-Rohdaten im Format der Garmin-API.

Damit lässt sich das Dashboard ohne Garmin-Konto ausprobieren und die
Verarbeitungskette testen. Simuliert wird ein Ausdauersportler mit
periodisiertem Training (3:1-Blöcke), einem Halbmarathon, einem Infekt und
physiologisch plausiblen Reaktionen von HRV, Ruhepuls und Schlaf.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import random
from pathlib import Path

MAX_HR = 188
AGE = 38

# Wochenplan: (Sport, Typ, Basisdauer in min) je Wochentag (Mo=0)
PLAN = {
    0: None,
    1: ("running", "intervals", 60),
    2: ("cycling", "easy", 75),
    3: ("running", "tempo", 55),
    4: ("strength_training", "strength", 45),
    5: ("cycling", "long", 150),
    6: ("running", "long", 95),
}

# Anteil Zeit in Garmin-HF-Zonen 1..5 je Einheitstyp
ZONES = {
    "easy": (0.30, 0.55, 0.13, 0.02, 0.0),
    "long": (0.25, 0.55, 0.16, 0.04, 0.0),
    "tempo": (0.15, 0.25, 0.25, 0.30, 0.05),
    "intervals": (0.25, 0.25, 0.12, 0.18, 0.20),
    "strength": (0.55, 0.30, 0.12, 0.03, 0.0),
    "race": (0.02, 0.05, 0.13, 0.50, 0.30),
}
ZONE_HR = (0.55, 0.65, 0.75, 0.85, 0.93)


def _ms_local(day: dt.date, hour: float) -> int:
    t = dt.datetime.combine(day, dt.time()) + dt.timedelta(hours=hour)
    return int(t.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def generate(data_dir: Path, days: int = 365, seed: int = 7) -> None:
    rnd = random.Random(seed)
    raw = Path(data_dir) / "raw"
    end = dt.date.today()
    start = end - dt.timedelta(days=days - 1)
    illness = set(end - dt.timedelta(days=d) for d in range(118, 124))
    race_day = end - dt.timedelta(days=52)
    race_day += dt.timedelta(days=(6 - race_day.weekday()))  # Sonntag

    ctl = atl = 45.0
    vo2 = 47.0
    weight = 78.5
    sleep_debt = 0.0
    prev_load = 0.0
    prev_late = False
    activities, weights, predictions, details = [], [], [], {}
    hrv_hist: list[float] = []
    aid = 10_000_000_000

    for i in range(days):
        day = start + dt.timedelta(days=i)
        ds = day.isoformat()
        week = i // 7
        block = week % 4
        scale = (1.0 + 0.07 * block) if block < 3 else 0.65
        scale *= 1.0 + 0.15 * min(i / days, 1.0)  # Aufbau über das Jahr
        to_race = (race_day - day).days
        weekend = day.weekday() >= 5
        sick = day in illness

        # ---------------- Nacht (Datum = Aufwachtag) ------------------------- #
        alcohol = weekend and rnd.random() < 0.25
        bed = (23.2 if not weekend else 0.2) + rnd.gauss(0, 0.45) + (0.5 if prev_late else 0)
        dur = 7.1 + (0.7 if weekend else 0) + rnd.gauss(0, 0.6) - (0.4 if alcohol else 0)
        dur = max(4.5, min(9.8, dur))
        sleep_debt = max(0.0, sleep_debt * 0.7 + (8 - dur))
        tsb = ctl - atl
        ln = (
            4.05
            + 0.004 * tsb
            - 0.0007 * max(prev_load - 80, 0)
            + 0.03 * (dur - 7.3)
            - (0.18 if alcohol else 0)
            - (0.06 if prev_late else 0)
            - (0.35 if sick else 0)
            + 0.002 * (vo2 - 47)
            + rnd.gauss(0, 0.07)
        )
        hrv = round(math.exp(ln))
        hrv_hist.append(hrv)
        rhr = round(50 - 0.08 * tsb + (7 if sick else 0) + (3 if alcohol else 0) - 0.15 * (vo2 - 47) + rnd.gauss(0, 1.2))
        score = max(30, min(98, round(62 + 7 * (dur - 6.8) + 30 * (ln - 4.0) - (8 if prev_late else 0) + rnd.gauss(0, 5))))
        awake = max(0.1, rnd.gauss(0.35, 0.12))
        deep = dur * max(0.08, rnd.gauss(0.17, 0.03))
        rem = dur * max(0.12, rnd.gauss(0.22, 0.03))
        light = dur - deep - rem
        start_ms = _ms_local(day - dt.timedelta(days=1), bed) if bed < 24 else _ms_local(day, bed - 24)
        end_ms = start_ms + int((dur + awake) * 3600 * 1000)
        _write(
            raw / "daily" / "sleep" / f"{ds}.json",
            {
                "dailySleepDTO": {
                    "calendarDate": ds,
                    "sleepTimeSeconds": int(dur * 3600),
                    "deepSleepSeconds": int(deep * 3600),
                    "lightSleepSeconds": int(light * 3600),
                    "remSleepSeconds": int(rem * 3600),
                    "awakeSleepSeconds": int(awake * 3600),
                    "sleepStartTimestampLocal": start_ms,
                    "sleepEndTimestampLocal": end_ms,
                    "averageRespirationValue": round(14 + rnd.gauss(0, 0.6) + (1.5 if sick else 0), 1),
                    "avgSleepStress": round(max(5, 18 - 0.3 * (score - 70) + rnd.gauss(0, 4))),
                    "averageSpO2Value": round(95 + rnd.gauss(0, 1)),
                    "sleepScores": {"overall": {"value": score, "qualifierKey": "GOOD"}},
                },
                "avgOvernightHrv": hrv,
                "restingHeartRate": rhr,
                "bodyBatteryChange": round(40 + 6 * (dur - 7) + 50 * (ln - 4.0)),
                "restlessMomentsCount": max(0, round(rnd.gauss(35, 10))),
            },
        )

        last7 = hrv_hist[-7:]
        base = hrv_hist[-60:]
        mean_b = sum(base) / len(base)
        _write(
            raw / "daily" / "hrv" / f"{ds}.json",
            {
                "hrvSummary": {
                    "calendarDate": ds,
                    "weeklyAvg": round(sum(last7) / len(last7)),
                    "lastNightAvg": hrv,
                    "lastNight5MinHigh": hrv + rnd.randint(8, 25),
                    "baseline": {"lowUpper": round(mean_b * 0.8), "balancedLow": round(mean_b * 0.88), "balancedUpper": round(mean_b * 1.12), "markerValue": 0.5},
                    "status": "BALANCED" if abs(sum(last7) / len(last7) - mean_b) < 0.12 * mean_b else "UNBALANCED",
                }
            },
        )

        # ---------------- Training ------------------------------------------ #
        sessions = []
        plan = PLAN[day.weekday()]
        if day == race_day:
            plan = ("running", "race", 0)
        elif 0 < to_race <= 7 and plan:
            plan = (plan[0], "easy" if plan[1] != "intervals" else "intervals", plan[2] * 0.6)
        if plan and not sick and rnd.random() > 0.07 and not (day.weekday() == 4 and rnd.random() < 0.3):
            sport, kind, base_min = plan
            minutes = base_min * scale * rnd.uniform(0.85, 1.15) if kind != "race" else 21.0975 / (vo2 * 0.0813 * 3.6 / 1.07) * 60
            if score < 50 and kind in ("intervals", "tempo"):
                kind = "easy"  # vernünftige Athleten passen an
            sessions.append((sport, kind, minutes))
        if day.weekday() == 2 and rnd.random() < 0.35 and not sick:
            sessions.append(("running", "easy", 35 * rnd.uniform(0.8, 1.2)))

        day_load = 0.0
        late = False
        for sport, kind, minutes in sessions:
            aid += rnd.randint(1, 999)
            zones = [max(0.0, z + rnd.gauss(0, 0.02)) for z in ZONES[kind]]
            tot = sum(zones)
            zones = [z / tot for z in zones]
            frac = sum(z * h for z, h in zip(zones, ZONE_HR))
            avg_hr = round(frac * MAX_HR + rnd.gauss(0, 2))
            hrr = (avg_hr - rhr) / (MAX_HR - rhr)
            trimp = minutes * hrr * 0.64 * math.exp(1.92 * hrr)
            load = round(trimp * 1.15 * rnd.uniform(0.9, 1.1))
            day_load += load
            hour = rnd.choice([6.5, 7.0, 12.2, 18.0, 19.5]) if not weekend else rnd.choice([8.5, 9.5, 10.0])
            late = late or hour + minutes / 60 >= 19
            easy_speed = vo2 * 0.0813 * 0.78
            speed = {
                "easy": easy_speed,
                "long": easy_speed * 0.98,
                "tempo": easy_speed * 1.15,
                "intervals": easy_speed * 1.12,
                "race": vo2 * 0.0813 / 1.07,
                "strength": 0,
            }[kind] * (1 + rnd.gauss(0, 0.02))
            if sport == "cycling":
                speed = (8.0 if kind == "easy" else 8.6) * (1 + (vo2 - 47) * 0.01) * (1 + rnd.gauss(0, 0.04))
            dur_s = int(minutes * 60)
            act = {
                "activityId": aid,
                "activityName": {"running": "Lauf", "cycling": "Radfahren", "strength_training": "Krafttraining"}[sport] + {"easy": " locker", "long": " lang", "tempo": " Tempo", "intervals": " Intervalle", "race": " – Halbmarathon", "strength": ""}[kind],
                "startTimeLocal": f"{ds} {int(hour):02d}:{int((hour % 1) * 60):02d}:00",
                "activityType": {"typeKey": sport},
                "duration": dur_s,
                "movingDuration": dur_s * 0.97,
                "distance": round(speed * dur_s, 1) if speed else 0.0,
                "elevationGain": round(rnd.uniform(20, 120) if sport == "running" else rnd.uniform(150, 900)) if speed else 0,
                "averageSpeed": round(speed, 3) if speed else None,
                "averageHR": avg_hr,
                "maxHR": min(MAX_HR, round(avg_hr + rnd.uniform(8, 22))),
                "calories": round(minutes * (11 if sport != "strength_training" else 6)),
                "aerobicTrainingEffect": round(min(5.0, 1.5 + load / 70), 1),
                "anaerobicTrainingEffect": round(min(5.0, (zones[4] + zones[3] * 0.4) * 9), 1),
                "activityTrainingLoad": load,
                "trainingEffectLabel": {"easy": "RECOVERY", "long": "AEROBIC_BASE", "tempo": "LACTATE_THRESHOLD", "intervals": "VO2MAX", "race": "LACTATE_THRESHOLD", "strength": "AEROBIC_BASE"}[kind],
                "averageRunningCadenceInStepsPerMinute": round(rnd.gauss(172, 3)) if sport == "running" else None,
                "avgPower": round(rnd.gauss(205, 15) * (1 + (vo2 - 47) * 0.01)) if sport == "cycling" else None,
                "vO2MaxValue": round(vo2) if sport == "running" else None,
                **{f"hrTimeInZone_{z + 1}": round(zones[z] * dur_s, 1) for z in range(5)},
            }
            activities.append(act)
            if sport in ("running", "cycling") and minutes >= 45 and kind in ("easy", "long") and (end - day).days < 120:
                drift = max(0.0, 0.09 - 0.0015 * (ctl - 40)) + rnd.gauss(0, 0.01)
                rows = []
                for k in range(0, dur_s, 30):
                    frac_t = k / dur_s
                    hr_k = avg_hr * (0.94 + drift * frac_t) + rnd.gauss(0, 2)
                    rows.append({"metrics": [k, round(hr_k), round(speed * (1 + rnd.gauss(0, 0.03)), 3), None]})
                details[str(aid)] = {
                    "metricDescriptors": [
                        {"metricsIndex": 0, "key": "sumDuration"},
                        {"metricsIndex": 1, "key": "directHeartRate"},
                        {"metricsIndex": 2, "key": "directSpeed"},
                        {"metricsIndex": 3, "key": "directPower"},
                    ],
                    "activityDetailMetrics": rows,
                }

        ctl += (day_load - ctl) / 42
        atl += (day_load - atl) / 7
        vo2 += (44 + 0.11 * ctl - vo2) / 60 + rnd.gauss(0, 0.03)
        prev_load, prev_late = day_load, late

        # ---------------- Tageszusammenfassung ------------------------------ #
        run_km = sum(a["distance"] for a in activities[-len(sessions):] if a["activityType"]["typeKey"] == "running") / 1000 if sessions else 0
        steps = max(1500, round(rnd.gauss(8500 if not weekend else 10500, 2500) + run_km * 800 - (4000 if sick else 0)))
        stress = max(8, round(32 - 2.5 * (dur - 7) + (6 if not weekend else -4) + (10 if sick else 0) + rnd.gauss(0, 5)))
        bb_wake = max(15, min(100, round(70 + 6 * (dur - 7.2) + 80 * (ln - 4.05) + rnd.gauss(0, 6))))
        mod = round(sum(m for _, k, m in sessions if k in ("easy", "long", "strength")) * 0.7 + rnd.uniform(5, 25))
        vig = round(sum(m for _, k, m in sessions if k in ("tempo", "intervals", "race")) * 0.6)
        _write(
            raw / "daily" / "summary" / f"{ds}.json",
            {
                "calendarDate": ds,
                "totalSteps": steps,
                "dailyStepGoal": 10000,
                "totalKilocalories": round(2350 + day_load * 4.5 + steps * 0.03),
                "activeKilocalories": round(day_load * 4.5 + steps * 0.03),
                "totalDistanceMeters": round(steps * 0.78),
                "floorsAscended": max(0, round(rnd.gauss(9, 4))),
                "restingHeartRate": rhr,
                "minHeartRate": rhr - 3,
                "maxHeartRate": max([a["maxHR"] for a in activities[-len(sessions):]] if sessions else [round(rnd.gauss(125, 10))]),
                "averageStressLevel": stress,
                "maxStressLevel": min(100, stress + round(rnd.uniform(40, 60))),
                "highStressDuration": round(max(0, stress - 20) * 60 * 2.5),
                "restStressDuration": round(dur * 3600 * 0.85),
                "bodyBatteryHighestValue": bb_wake,
                "bodyBatteryLowestValue": max(5, round(bb_wake - 45 - day_load * 0.15 + rnd.gauss(0, 5))),
                "bodyBatteryChargedValue": round(40 + 6 * (dur - 7)),
                "bodyBatteryDrainedValue": round(50 + day_load * 0.15),
                "bodyBatteryAtWakeTime": bb_wake,
                "moderateIntensityMinutes": mod,
                "vigorousIntensityMinutes": vig,
                "sedentarySeconds": round(rnd.gauss(9.5, 1.5) * 3600),
                "averageSpo2": round(96 + rnd.gauss(0, 0.8)),
                "lowestSpo2": round(90 + rnd.gauss(0, 1.5)),
                "avgWakingRespirationValue": round(15 + rnd.gauss(0, 0.6), 1),
            },
        )
        acute_daily = atl
        _write(
            raw / "daily" / "training_status" / f"{ds}.json",
            {
                "mostRecentVO2Max": {"generic": {"calendarDate": ds, "vo2MaxPreciseValue": round(vo2, 1), "vo2MaxValue": round(vo2)}},
                "mostRecentTrainingStatus": {
                    "latestTrainingStatusData": {
                        "3456789": {
                            "trainingStatusFeedbackPhrase": "PRODUCTIVE" if tsb > -25 and atl > ctl else ("RECOVERY" if atl < ctl * 0.8 else "MAINTAINING"),
                            "acuteTrainingLoadDTO": {
                                "dailyTrainingLoadAcute": round(acute_daily * 7),
                                "dailyTrainingLoadChronic": round(ctl * 7),
                                "dailyAcuteChronicWorkloadRatio": round(atl / max(ctl, 1), 2),
                            },
                        }
                    }
                },
                "mostRecentTrainingLoadBalance": {
                    "metricsTrainingLoadBalanceDTOMap": {
                        "3456789": {
                            "monthlyLoadAerobicLow": round(ctl * 28 * 0.55),
                            "monthlyLoadAerobicHigh": round(ctl * 28 * 0.33),
                            "monthlyLoadAnaerobic": round(ctl * 28 * 0.12),
                            "trainingBalanceFeedbackPhrase": "BALANCED",
                        }
                    }
                },
            },
        )
        readiness = max(1, min(100, round(55 + 0.9 * tsb + 60 * (ln - 4.05) + 4 * (dur - 7.3) + rnd.gauss(0, 6))))
        _write(
            raw / "daily" / "readiness" / f"{ds}.json",
            [{"calendarDate": ds, "score": readiness, "level": "HIGH" if readiness >= 75 else "MODERATE" if readiness >= 50 else "LOW", "inputContext": "AFTER_WAKEUP_RESET", "recoveryTime": max(0, round(-tsb * 60)), "acuteLoad": round(atl * 7)}],
        )
        _write(raw / "daily" / "respiration" / f"{ds}.json", {"avgSleepRespirationValue": round(14 + rnd.gauss(0, 0.5) + (1.5 if sick else 0), 1), "avgWakingRespirationValue": round(15 + rnd.gauss(0, 0.6), 1)})
        _write(raw / "daily" / "spo2" / f"{ds}.json", {"averageSpO2": 96, "lowestSpO2": 90, "avgSleepSpO2": round(95 + rnd.gauss(0, 0.8))})
        _write(raw / "daily" / "hydration" / f"{ds}.json", {"valueInML": round(max(800, rnd.gauss(2300, 450) + day_load * 3)), "goalInML": 2500})
        _write(raw / "daily" / "max_metrics" / f"{ds}.json", [{"generic": {"calendarDate": ds, "vo2MaxPreciseValue": round(vo2, 1), "fitnessAge": round(AGE - (vo2 - 45) * 0.9)}}])

        weight += -0.007 + rnd.gauss(0, 0.02)
        if rnd.random() < 0.55:
            w = weight + rnd.gauss(0, 0.35)
            weights.append({"calendarDate": ds, "weight": round(w * 1000), "bmi": round(w / 1.81**2, 1), "bodyFat": round(16 + (weight - 76) * 0.6 + rnd.gauss(0, 0.5), 1), "muscleMass": round((w * 0.44) * 1000), "bodyWater": round(58 + rnd.gauss(0, 0.6), 1)})
        t5 = 5000 / (vo2 * 0.0813)
        predictions.append({"calendarDate": ds, "time5K": round(t5), "time10K": round(t5 * 2**1.06), "timeHalfMarathon": round(t5 * (21.0975 / 5) ** 1.065), "timeMarathon": round(t5 * (42.195 / 5) ** 1.09)})

    # ---------------- Zeitraum- & Stammdaten -------------------------------- #
    _write(raw / "range" / "activities.json", [list(reversed(activities))])
    _write(raw / "range" / "body_composition.json", [{"dateWeightList": weights}])
    _write(raw / "range" / "race_predictions.json", [predictions])
    for aid_s, det in details.items():
        _write(raw / "activity_details" / f"{aid_s}.json", det)

    st = raw / "static"
    birth = dt.date(end.year - AGE, 3, 14).isoformat()
    _write(st / "full_name.json", {"fullName": "Demo Athlet"})
    _write(st / "personal_information.json", {"userInfo": {"birthDate": birth, "genderType": "MALE"}, "biometricProfile": {"height": 181, "weight": round(weight * 1000), "vo2Max": round(vo2)}})
    _write(st / "heart_rate_zones.json", [{"sport": "DEFAULT", "trainingMethod": "HR_MAX", "maxHeartRateUsed": MAX_HR, "restingHeartRateUsed": 49, "lactateThresholdHeartRateUsed": 168, "zone1Floor": 94, "zone2Floor": 113, "zone3Floor": 132, "zone4Floor": 150, "zone5Floor": 169}])
    _write(st / "lactate_threshold_latest.json", {"speed_and_heart_rate": {"calendarDate": end.isoformat(), "speed": round(vo2 * 0.0813 * 0.88, 3), "heartRate": 168}})
    _write(st / "fitness_age.json", {"chronologicalAge": AGE, "fitnessAge": round(AGE - (vo2 - 45) * 0.9, 1), "achievableFitnessAge": AGE - 6})
    _write(st / "devices.json", [{"productDisplayName": "Forerunner 965 (Demo)"}])
    _write(st / "race_predictions_latest.json", {"calendarDate": end.isoformat(), **{k: v for k, v in predictions[-1].items() if k != "calendarDate"}})
    race = next((a for a in activities if "Halbmarathon" in a["activityName"]), None)
    prs = [
        {"typeId": 3, "value": round(predictions[-1]["time5K"] * 0.99), "actStartDateTimeInGMTFormatted": (end - dt.timedelta(days=20)).isoformat(), "activityName": "Lauf Intervalle"},
        {"typeId": 7, "value": 24500.0, "actStartDateTimeInGMTFormatted": (end - dt.timedelta(days=40)).isoformat(), "activityName": "Lauf lang"},
        {"typeId": 12, "value": 31244, "actStartDateTimeInGMTFormatted": (end - dt.timedelta(days=200)).isoformat()},
    ]
    if race:
        prs.append({"typeId": 5, "value": race["duration"], "actStartDateTimeInGMTFormatted": race["startTimeLocal"][:10], "activityName": race["activityName"]})
    _write(st / "personal_records.json", prs)
    _write(raw / "meta.json", {"source": "demo", "fetched_at": dt.datetime.now().isoformat(timespec="seconds"), "start": start.isoformat(), "end": end.isoformat(), "errors": []})
