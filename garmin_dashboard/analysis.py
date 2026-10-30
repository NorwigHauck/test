"""Sportwissenschaftliche Auswertung der Garmin-Daten.

Alle Modelle sind bewusst einfach und nachvollziehbar gehalten; die Quellen
stehen jeweils an der Funktion. Ergebnisse sind Hinweise für die
Trainingssteuerung, keine medizinische Diagnose.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from .normalize import activity_stream

CTL_DAYS = 42  # Fitness / chronische Last (Banister 1975, Coggan PMC)
ATL_DAYS = 7  # Ermüdung / akute Last
ACWR_CHRONIC = 28  # EWMA-ACWR nach Williams et al. 2017
HRV_BASE_DAYS = 60
RHR_BASE_DAYS = 30


# --------------------------------------------------------------------------- #
# Hilfsfunktionen


def _f(x: Any, nd: int = 1) -> float | None:
    """JSON-taugliche Zahl (NaN -> None)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(v) else round(v, nd)


def _last(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else np.nan


def _slope_per_day(s: pd.Series) -> float:
    """Lineare Steigung (Einheit/Tag) einer Zeitreihe mit Datumsindex."""
    s = s.dropna()
    if len(s) < 4:
        return np.nan
    x = (s.index - s.index[0]).days.astype(float)
    if np.ptp(x) == 0:
        return np.nan
    return float(np.polyfit(x, s.values.astype(float), 1)[0])


def spearman(x: pd.Series, y: pd.Series) -> tuple[float, float, int]:
    """Spearman-Rangkorrelation mit p-Wert über die Fisher-z-Näherung."""
    m = pd.concat([x, y], axis=1).dropna()
    n = len(m)
    if n < 10 or m.iloc[:, 0].nunique() < 3 or m.iloc[:, 1].nunique() < 3:
        return np.nan, np.nan, n
    r = float(m.iloc[:, 0].rank().corr(m.iloc[:, 1].rank()))
    if not math.isfinite(r):
        return np.nan, np.nan, n
    z = math.atanh(max(min(r, 0.9999), -0.9999)) * math.sqrt(n - 3)
    p = math.erfc(abs(z) / math.sqrt(2))
    return r, p, n


def cohens_d(a: pd.Series, b: pd.Series) -> float:
    a, b = a.dropna(), b.dropna()
    if len(a) < 3 or len(b) < 3:
        return np.nan
    sp = math.sqrt(((len(a) - 1) * a.var() + (len(b) - 1) * b.var()) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else np.nan


def fmt_time(seconds: float) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "–"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_pace(speed_ms: float) -> str:
    if not speed_ms or not math.isfinite(speed_ms) or speed_ms <= 0:
        return "–"
    return fmt_time(1000 / speed_ms) + " /km"


# --------------------------------------------------------------------------- #
# Trainingslast


def _trimp(acts: pd.DataFrame, rest: float, hrmax: float, female: bool) -> pd.Series:
    """Banister-TRIMP (1991): Dauer × HRr × 0,64·e^(1,92·HRr) (Frauen 0,86·e^(1,67·HRr))."""
    hrr = ((acts["avg_hr"] - rest) / (hrmax - rest)).clip(0, 1)
    hrr = hrr.fillna(0.5)  # ohne Herzfrequenz: moderate Intensität annehmen
    a, b = (0.86, 1.67) if female else (0.64, 1.92)
    return acts["duration_min"].fillna(0) * hrr * a * np.exp(b * hrr)


def activity_load(acts: pd.DataFrame, daily: pd.DataFrame, profile: dict) -> tuple[pd.Series, str]:
    """Belastung pro Einheit: Garmin-Trainingslast (EPOC), Lücken per TRIMP."""
    if acts.empty:
        return pd.Series(dtype=float), "keine Aktivitäten"
    rest = profile.get("rest_hr")
    if not rest or not math.isfinite(rest):
        rest = float(daily["rhr"].median()) if "rhr" in daily and daily["rhr"].notna().any() else 55.0
    hrmax = profile.get("max_hr")
    if not hrmax or not math.isfinite(hrmax):
        observed = acts["max_hr"].quantile(0.98) if acts["max_hr"].notna().any() else np.nan
        age = profile.get("age") or 35
        hrmax = float(max(observed, 208 - 0.7 * age)) if math.isfinite(observed) else 208 - 0.7 * age
    trimp = _trimp(acts, rest, hrmax, str(profile.get("gender", "")).upper() == "FEMALE")
    garmin = acts["garmin_load"]
    if garmin.notna().mean() >= 0.5:
        both = garmin.notna() & (trimp > 0)
        scale = float((garmin[both] / trimp[both]).median()) if both.sum() >= 5 else 1.0
        return garmin.fillna(trimp * scale), "Garmin-Trainingslast (EPOC), Lücken per TRIMP"
    return trimp, "Banister-TRIMP aus Herzfrequenz"


def pmc(load: pd.Series) -> pd.DataFrame:
    """Performance-Management-Chart (Fitness-Ermüdungs-Modell nach Banister).

    CTL = 42-Tage-EWMA, ATL = 7-Tage-EWMA, TSB = CTL(gestern) − ATL(gestern).
    ACWR = 7-Tage- zu 28-Tage-EWMA (Williams et al. 2017).
    Monotonie & Strain nach Foster (1998).
    """
    seed = float(load.iloc[:28].mean()) if len(load) else 0.0
    out = pd.DataFrame(index=load.index)
    out["load"] = load

    def ewma(days: int) -> pd.Series:
        k = 1 / days
        vals, prev = [], seed
        for v in load.values:
            prev = prev + k * (v - prev)
            vals.append(prev)
        return pd.Series(vals, index=load.index)

    out["ctl"] = ewma(CTL_DAYS)
    out["atl"] = ewma(ATL_DAYS)
    out["tsb"] = (out["ctl"] - out["atl"]).shift(1)
    chronic = ewma(ACWR_CHRONIC)
    out["acwr"] = (out["atl"] / chronic.replace(0, np.nan)).where(chronic > 5)
    roll = load.rolling(7, min_periods=7)
    sd = roll.std(ddof=0)
    out["monotony"] = (roll.mean() / sd.replace(0, np.nan)).clip(upper=5)
    out["strain"] = roll.sum() * out["monotony"]
    out["ramp"] = out["ctl"] - out["ctl"].shift(7)
    # Die ersten Wochen sind Einschwingphase des Modells
    out.loc[out.index[: min(len(out), 21)], ["acwr", "ramp"]] = np.nan
    return out


# --------------------------------------------------------------------------- #
# Erholung


def hrv_model(hrv: pd.Series) -> pd.DataFrame:
    """HRV-gesteuertes Training nach Plews et al. (2012, 2013) / Kiviniemi (2007).

    Gearbeitet wird mit ln(rMSSD). Der 7-Tage-Schnitt wird mit einem
    individuellen Normalbereich verglichen: 60-Tage-Mittel ± 0,5·SD
    (smallest worthwhile change). Zusätzlich der Variationskoeffizient (CV) der
    letzten 7 Tage – ein steigender CV bei fallendem Mittel gilt als Zeichen
    unzureichender Anpassung.
    """
    x = np.log(hrv.where(hrv > 0))
    out = pd.DataFrame(index=hrv.index)
    out["hrv"] = hrv
    out["ln"] = x
    out["ln7"] = x.rolling(7, min_periods=3).mean()
    base = x.rolling(HRV_BASE_DAYS, min_periods=14)
    out["base"] = base.mean().shift(1)
    out["sd"] = base.std().shift(1)
    out["lo"] = out["base"] - 0.5 * out["sd"]
    out["hi"] = out["base"] + 0.5 * out["sd"]
    out["cv7"] = x.rolling(7, min_periods=5).std() / out["ln7"] * 100
    out["z"] = (x - out["base"]) / out["sd"]
    # In ms zurückrechnen für die Darstellung
    for c in ("ln7", "base", "lo", "hi"):
        out[c + "_ms"] = np.exp(out[c])
    return out


def rhr_model(rhr: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame(index=rhr.index)
    out["rhr"] = rhr
    base = rhr.rolling(RHR_BASE_DAYS, min_periods=10)
    out["base"] = base.mean().shift(1)
    out["sd"] = base.std().shift(1).clip(lower=1.0)
    out["delta"] = rhr - out["base"]
    out["z"] = out["delta"] / out["sd"]
    return out


def recovery_index(hrv_z: pd.Series, rhr_z: pd.Series, sleep: pd.Series) -> pd.Series:
    """Eigener, experimenteller Erholungsindex (0–100, 50 = persönlicher Normalwert).

    Gewichtete z-Werte: 50 % HRV, 30 % Ruhepuls (invertiert), 20 % Schlafscore.
    """
    s_base = sleep.rolling(30, min_periods=10)
    sleep_z = (sleep - s_base.mean().shift(1)) / s_base.std().shift(1)
    parts = pd.concat([hrv_z * 0.5, -rhr_z * 0.3, sleep_z * 0.2], axis=1)
    weights = pd.concat([hrv_z.notna() * 0.5, rhr_z.notna() * 0.3, sleep_z.notna() * 0.2], axis=1)
    z = parts.sum(axis=1, min_count=1) / weights.sum(axis=1).replace(0, np.nan)
    return (50 + 15 * z.clip(-3, 3)).clip(0, 100)


def sleep_model(daily: pd.DataFrame, need_h: float) -> dict[str, Any]:
    s = daily.get("sleep_h", pd.Series(dtype=float))
    if s.dropna().empty:
        return {}
    last7 = s.dropna().iloc[-7:]
    mid = (daily["sleep_start"] + (daily["sleep_h"] + daily.get("awake_h", 0).fillna(0)) / 2).dropna()
    recent_mid = mid.iloc[-14:]
    wk = mid.iloc[-56:]
    weekend = wk[wk.index.dayofweek >= 5]  # Aufwachdatum Sa/So = Nächte Fr/Sa
    weekday = wk[wk.index.dayofweek < 5]
    stages = daily[["deep_h", "rem_h", "light_h"]].iloc[-28:].sum()
    total = stages.sum()
    return {
        "avg7_h": _f(last7.mean(), 2),
        "avg28_h": _f(s.dropna().iloc[-28:].mean(), 2),
        "debt7_h": _f((need_h - last7).clip(lower=0).sum(), 1),
        "need_h": need_h,
        "nights_short7": int((last7 < 6).sum()),
        "regularity_min": _f(recent_mid.std() * 60, 0),
        "social_jetlag_h": _f(weekend.mean() - weekday.mean(), 2) if len(weekend) >= 3 and len(weekday) >= 5 else None,
        "midpoint_h": _f(recent_mid.mean() % 24, 2),
        "deep_pct": _f(stages["deep_h"] / total * 100, 0) if total else None,
        "rem_pct": _f(stages["rem_h"] / total * 100, 0) if total else None,
        "score28": _f(daily.get("sleep_score", pd.Series(dtype=float)).iloc[-28:].mean(), 0),
    }


# --------------------------------------------------------------------------- #
# Intensitätsverteilung & Leistung


def intensity_distribution(acts: pd.DataFrame, since: pd.Timestamp) -> dict[str, Any]:
    """3-Zonen-Modell (Seiler & Kjerland 2006) und Polarisierungsindex (Treff et al. 2019).

    Garmin-Zonen 1–2 ≈ Zone I (unter VT1), Zone 3 ≈ Zone II, Zonen 4–5 ≈ Zone III.
    """
    if acts.empty:
        return {}
    a = acts[acts["date"] >= since]
    low = float((a["z1_min"].fillna(0) + a["z2_min"].fillna(0)).sum())
    mid = float(a["z3_min"].fillna(0).sum())
    high = float((a["z4_min"].fillna(0) + a["z5_min"].fillna(0)).sum())
    total = low + mid + high
    if total < 60:
        return {}
    f1, f2, f3 = low / total, mid / total, high / total
    pi = None
    if f1 > f3 > f2 and f3 > 0.01:
        pi = math.log10(f1 / max(f2, 0.01) * f3 * 100)
    if pi is not None and pi > 2.0:
        kind = "polarisiert"
    elif f1 >= f2 >= f3:
        kind = "pyramidal" if f1 < 0.9 else "fast ausschließlich locker"
    elif f2 >= f1 and f2 >= f3:
        kind = "schwellenbetont"
    elif f3 >= f1:
        kind = "hochintensiv-lastig"
    else:
        kind = "gemischt"
    return {
        "low_pct": _f(f1 * 100, 0),
        "mid_pct": _f(f2 * 100, 0),
        "high_pct": _f(f3 * 100, 0),
        "hours": _f(total / 60, 1),
        "pi": _f(pi, 2),
        "kind": kind,
    }


def efficiency_factor(acts: pd.DataFrame) -> pd.DataFrame:
    """Aerobe Effizienz (EF): Geschwindigkeit (m/min) je Herzschlag bei lockeren Läufen.

    Steigt der EF bei gleicher Intensität, wird man bei gleicher Herzfrequenz
    schneller – ein klassischer Marker für aerobe Fitness (Allen & Coggan).
    """
    if acts.empty:
        return pd.DataFrame()
    r = acts[
        (acts["sport"] == "run")
        & (acts["duration_min"] >= 20)
        & acts["avg_hr"].notna()
        & acts["avg_speed"].notna()
        & (acts["te_anaer"].fillna(0) < 2.0)
    ].copy()
    hilly = (r["elev_m"] / r["distance_km"].replace(0, np.nan)) > 15
    r = r[~hilly.fillna(False)]
    r["ef"] = r["avg_speed"] * 60 / r["avg_hr"]
    return r[["date", "name", "ef", "avg_hr", "avg_speed", "duration_min"]]


def decoupling(acts: pd.DataFrame, details: dict[str, Any]) -> list[dict[str, Any]]:
    """Aerobe Entkopplung (Pa:HR, Friel): EF 1. Hälfte vs. 2. Hälfte.

    Unter 5 % gilt die aerobe Grundlage für diese Dauer als ausreichend.
    """
    out = []
    if acts.empty or not details:
        return out
    by_id = acts.set_index(acts["id"].astype(str))
    for aid, raw in details.items():
        if aid not in by_id.index:
            continue
        act = by_id.loc[aid]
        st = activity_stream(raw)
        if st.empty or st["hr"].notna().sum() < 50:
            continue
        use_power = act["sport"] == "ride" and st["power"].notna().mean() > 0.8
        output = st["power"] if use_power else st["speed"]
        st = st.assign(out=output).dropna(subset=["hr", "out", "t"])
        st = st[(st["hr"] > 60) & (st["out"] > 0) & (st["t"] >= 600)]  # 10 min Einlaufen weg
        if len(st) < 40 or (st["t"].iloc[-1] - st["t"].iloc[0]) < 30 * 60:
            continue
        half = st["t"].iloc[0] + (st["t"].iloc[-1] - st["t"].iloc[0]) / 2
        a, b = st[st["t"] < half], st[st["t"] >= half]
        ef1 = a["out"].mean() / a["hr"].mean()
        ef2 = b["out"].mean() / b["hr"].mean()
        out.append(
            {
                "date": act["date"].strftime("%Y-%m-%d"),
                "name": act["name"],
                "sport": act["sport"],
                "minutes": _f(act["duration_min"], 0),
                "decoupling_pct": _f((ef1 - ef2) / ef1 * 100, 1),
            }
        )
    return sorted(out, key=lambda r: r["date"])


def riegel_exponent(t1: float, d1: float, t2: float, d2: float) -> float:
    """Riegel (1981): t2 = t1·(d2/d1)^k  →  k = ln(t2/t1)/ln(d2/d1); typisch ≈ 1,06."""
    if not all(map(lambda v: v and math.isfinite(v) and v > 0, (t1, t2))):
        return np.nan
    return math.log(t2 / t1) / math.log(d2 / d1)


# --------------------------------------------------------------------------- #
# Zusammenhänge


CORRELATIONS = [
    # (x, y, Verschiebung von y in Tagen, Beschreibung)
    ("load", "hrv", 1, "Trainingslast am Tag → HRV in der Nacht danach"),
    ("load", "rhr", 1, "Trainingslast am Tag → Ruhepuls am nächsten Morgen"),
    ("load", "sleep_score", 1, "Trainingslast am Tag → Schlafscore der folgenden Nacht"),
    ("sleep_h", "hrv", 0, "Schlafdauer → HRV derselben Nacht"),
    ("sleep_h", "stress_avg", 0, "Schlafdauer → Stress am folgenden Tag"),
    ("stress_avg", "sleep_score", 1, "Tagesstress → Schlafscore der folgenden Nacht"),
    ("stress_avg", "hrv", 1, "Tagesstress → HRV der folgenden Nacht"),
    ("steps", "sleep_score", 1, "Schritte am Tag → Schlafscore der folgenden Nacht"),
    ("sleep_start", "sleep_score", 0, "Späteres Einschlafen → Schlafscore"),
    ("sleep_start", "hrv", 0, "Späteres Einschlafen → HRV"),
    ("late_training", "hrv", 1, "Training nach 19 Uhr → HRV in der Nacht danach"),
    ("late_training", "sleep_score", 1, "Training nach 19 Uhr → Schlafscore"),
    ("water_l", "hrv", 1, "Trinkmenge → HRV der folgenden Nacht"),
]

LABELS = {
    "load": "Trainingslast",
    "hrv": "HRV",
    "rhr": "Ruhepuls",
    "sleep_score": "Schlafscore",
    "sleep_h": "Schlafdauer",
    "stress_avg": "Stress",
    "steps": "Schritte",
    "sleep_start": "Einschlafzeit",
    "late_training": "Spätes Training",
    "water_l": "Trinkmenge",
    "vo2max": "VO2max",
}


def correlations(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Explorative, zeitversetzte Rangkorrelationen. Korrelation ≠ Kausalität."""
    n_tests = len(CORRELATIONS)
    res = []
    for x, y, lag, text in CORRELATIONS:
        if x not in frame or y not in frame:
            continue
        r, p, n = spearman(frame[x], frame[y].shift(-lag))
        if not math.isfinite(r):
            continue
        res.append(
            {
                "x": LABELS.get(x, x),
                "y": LABELS.get(y, y),
                "text": text,
                "r": _f(r, 2),
                "p": _f(p, 4),
                "n": n,
                # Bonferroni-Korrektur über alle geprüften Paare
                "significant": bool(p * n_tests < 0.05 and abs(r) >= 0.2),
            }
        )
    return sorted(res, key=lambda d: -abs(d["r"]))


def dose_response(load: pd.Series, hrv_z: pd.Series, rhr_delta: pd.Series) -> list[dict[str, Any]]:
    """Wie reagiert der Körper in der Nacht nach Ruhetag / leichtem / hartem Tag?"""
    nxt = pd.DataFrame({"load": load, "hrv_z": hrv_z.shift(-1), "rhr": rhr_delta.shift(-1)}).dropna(subset=["hrv_z"])
    if len(nxt) < 21:
        return []
    train = nxt[nxt["load"] > 0]
    if len(train) < 9:
        return []
    q1, q2 = train["load"].quantile([1 / 3, 2 / 3])
    groups = [
        ("Ruhetag", nxt[nxt["load"] == 0]),
        (f"Leicht (< {q1:.0f})", train[train["load"] < q1]),
        (f"Mittel ({q1:.0f}–{q2:.0f})", train[(train["load"] >= q1) & (train["load"] < q2)]),
        (f"Hart (≥ {q2:.0f})", train[train["load"] >= q2]),
    ]
    return [
        {"group": g, "n": len(df), "hrv_z": _f(df["hrv_z"].mean(), 2), "rhr_delta": _f(df["rhr"].mean(), 1)}
        for g, df in groups
        if len(df) >= 3
    ]


def weekday_pattern(daily: pd.DataFrame, hrv_z: pd.Series) -> list[dict[str, Any]]:
    names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    df = pd.DataFrame(
        {
            "sleep_h": daily.get("sleep_h"),
            "stress": daily.get("stress_avg"),
            "hrv_z": hrv_z,
            "steps": daily.get("steps"),
        }
    ).iloc[-84:]
    g = df.groupby(df.index.dayofweek).mean()
    return [
        {
            "day": names[i],
            "sleep_h": _f(g.loc[i, "sleep_h"], 2) if i in g.index else None,
            "stress": _f(g.loc[i, "stress"], 0) if i in g.index else None,
            "hrv_z": _f(g.loc[i, "hrv_z"], 2) if i in g.index else None,
            "steps": _f(g.loc[i, "steps"], 0) if i in g.index else None,
        }
        for i in range(7)
    ]


# --------------------------------------------------------------------------- #
# Coach


TRAINING_STATUS = {
    "PRODUCTIVE": "Produktiv",
    "MAINTAINING": "Erhaltend",
    "RECOVERY": "Erholung",
    "UNPRODUCTIVE": "Unproduktiv",
    "DETRAINING": "Leistungsabbau",
    "OVERREACHING": "Überlastung",
    "PEAKING": "Leistungshoch",
    "STRAINED": "Überfordert",
    "NO_STATUS": "Kein Status",
}


def training_status_de(code: Any) -> str | None:
    if not isinstance(code, str):
        return None
    key = re.sub(r"_\d+$", "", code.upper())
    return TRAINING_STATUS.get(key, code.replace("_", " ").title())


def de_text(text: str) -> str:
    """Dezimalpunkt -> Komma und "-0" vermeiden, damit Texte deutsch lesbar sind."""
    text = re.sub(r"(?<![\d.])[+-]0((?:\.0+)?)(?![.\d])", r"±0\1", text)
    return re.sub(r"(\d)\.(\d)", r"\1,\2", text)


def _status(level: str) -> str:
    return {"ok": "good", "warn": "warning", "bad": "critical", "info": "info"}[level]


def coach_today(t: dict[str, Any]) -> dict[str, Any]:
    """Regelbasierte Tagesempfehlung aus Erholungs- und Belastungsmarkern."""
    flags: list[tuple[str, str, str]] = []  # (Stufe, Marker, Erklärung)

    hz, hrv7, lo, hi = t["hrv_z"], t["hrv7"], t["hrv_lo"], t["hrv_hi"]
    if math.isfinite(hrv7) and math.isfinite(lo):
        if hrv7 < lo:
            flags.append(("warn", "HRV-Trend", "7-Tage-HRV liegt unter deinem Normalbereich – das autonome Nervensystem ist noch nicht erholt."))
        elif hrv7 > hi:
            flags.append(("info", "HRV-Trend", "7-Tage-HRV über dem Normalbereich – meist gute Erholung; bei gleichzeitig hoher Last und Müdigkeit kann es aber auch ein Überlastungssignal sein."))
        else:
            flags.append(("ok", "HRV-Trend", "7-Tage-HRV im persönlichen Normalbereich."))
    if math.isfinite(hz) and hz < -1.5:
        flags.append(("warn", "HRV letzte Nacht", f"Letzte Nacht deutlich unter Baseline (z = {hz:.1f})."))

    rd = t["rhr_delta"]
    if math.isfinite(rd):
        if rd >= 7:
            flags.append(("bad", "Ruhepuls", f"Ruhepuls {rd:+.0f} Schläge über deinem 30-Tage-Schnitt – möglicher Infekt, Hitze, Alkohol oder Übermüdung."))
        elif rd >= 4:
            flags.append(("warn", "Ruhepuls", f"Ruhepuls leicht erhöht ({rd:+.0f} Schläge)."))
        else:
            flags.append(("ok", "Ruhepuls", f"Ruhepuls normal ({rd:+.0f} Schläge zum Schnitt)."))

    sh = t["sleep_h"]
    if math.isfinite(sh):
        if sh < 6:
            flags.append(("warn", "Schlaf", f"Nur {sh:.1f} h geschlafen. Unter 6 h sinken Leistungsfähigkeit und Verletzungsresistenz messbar (Fullagar et al. 2015)."))
        else:
            flags.append(("ok", "Schlaf", f"{sh:.1f} h Schlaf."))
    debt = t["sleep_debt"]
    if math.isfinite(debt) and debt > 5:
        flags.append(("warn", "Schlafschuld", f"{debt:.1f} h Schlafdefizit in 7 Tagen."))

    tsb = t["tsb"]
    if math.isfinite(tsb):
        if tsb < -30:
            flags.append(("bad", "Form (TSB)", f"TSB {tsb:.0f}: sehr hohe aufgelaufene Ermüdung."))
        elif tsb < -15:
            flags.append(("warn", "Form (TSB)", f"TSB {tsb:.0f}: produktiver, aber ermüdender Trainingsblock."))
        elif tsb > 20:
            flags.append(("info", "Form (TSB)", f"TSB {tsb:+.0f}: sehr frisch – ideal für Wettkampf, auf Dauer aber Fitnessverlust."))
        else:
            flags.append(("ok", "Form (TSB)", f"TSB {tsb:+.0f}: ausgewogen."))

    acwr = t["acwr"]
    if math.isfinite(acwr):
        if acwr > 1.5:
            flags.append(("bad", "ACWR", f"Akut:Chronisch {acwr:.2f} – Belastung steigt sprunghaft."))
        elif acwr > 1.3:
            flags.append(("warn", "ACWR", f"Akut:Chronisch {acwr:.2f} – oberhalb von 0,8–1,3."))
        elif acwr < 0.8:
            flags.append(("info", "ACWR", f"Akut:Chronisch {acwr:.2f} – aktuell weniger Last als gewohnt."))
        else:
            flags.append(("ok", "ACWR", f"Akut:Chronisch {acwr:.2f} im Bereich 0,8–1,3."))

    rdy = t["readiness"]
    if math.isfinite(rdy):
        lvl = "ok" if rdy >= 60 else ("warn" if rdy >= 33 else "bad")
        flags.append((lvl, "Garmin Training Readiness", f"{rdy:.0f}/100"))

    n_bad = sum(f[0] == "bad" for f in flags)
    n_warn = sum(f[0] == "warn" for f in flags)
    if n_bad >= 1 or n_warn >= 3:
        level = "critical"
        title = "Erholung priorisieren"
        actions = [
            "Ruhetag oder max. 30–40 min sehr locker (Zone 1, Gesprächstempo).",
            "Keine Intervalle, kein schweres Krafttraining.",
            "Früher ins Bett, Alkohol meiden, ausreichend trinken.",
            "Bei Krankheitsgefühl: kein Training (\"Neck-Check\": Symptome unterhalb des Halses → Pause).",
        ]
    elif n_warn >= 1:
        level = "warning"
        title = "Locker trainieren"
        actions = [
            "Grundlage: 45–75 min in Zone 1–2, Nasenatmung als Kontrolle.",
            "Technik, Mobilität oder leichtes Krafttraining sind gut möglich.",
            "Harte Einheit auf einen Tag mit normaler HRV verschieben.",
        ]
    else:
        level = "good"
        title = "Bereit für Qualität"
        actions = [
            "Guter Tag für eine Schlüsseleinheit: Intervalle (z. B. 4–6 × 4 min Zone 4–5) oder Schwelle.",
            "Alternativ langer, lockerer Dauerlauf / lange Ausfahrt.",
            "Danach Kohlenhydrate + 20–30 g Protein zur Regeneration.",
        ]
    if level == "good" and math.isfinite(tsb) and tsb > 20 and math.isfinite(acwr) and acwr < 0.8:
        actions.insert(0, "Du bist sehr frisch: jetzt bewusst einen Trainingsreiz setzen, sonst sinkt die Fitness (CTL).")
    return {
        "level": level,
        "title": title,
        "actions": actions,
        "flags": [{"status": _status(s), "label": l, "text": de_text(x)} for s, l, x in flags],
    }


def insights(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Trainer-Hinweise mit Begründung und Literatur."""
    out = []

    def add(area: str, level: str, title: str, text: str, ref: str = "") -> None:
        out.append({"area": area, "status": _status(level), "title": title, "text": de_text(text), "ref": ref})

    p = ctx["pmc"]
    ramp = _last(p["ramp"])
    if math.isfinite(ramp):
        if ramp > 8:
            add("Belastung", "warn", "Aggressiver Aufbau", f"Deine Fitness (CTL) steigt um {ramp:.1f} Punkte/Woche. Mehr als ~5–8 pro Woche lässt sich selten lange durchhalten – plane eine Entlastungswoche ein (ca. −30–40 % Umfang).", "Coggan/Allen, Training and Racing with a Power Meter")
        elif ramp < -5:
            add("Belastung", "info", "Fitness sinkt", f"CTL fällt um {abs(ramp):.1f} Punkte/Woche. Gewollt (Tapering, Pause)? Sonst Umfang wieder schrittweise steigern.", "")
        else:
            add("Belastung", "ok", "Kontrollierte Progression", f"CTL-Veränderung {ramp:+.1f}/Woche – nachhaltig.", "")
    mono = _last(p["monotony"])
    if math.isfinite(mono) and mono > 2.0:
        add("Belastung", "warn", "Zu monotone Woche", f"Trainingsmonotonie {mono:.1f} (> 2,0). Fehlende harte/leichte Wechsel erhöhen das Risiko für Infekte und Übertraining. Mehr Kontrast: harte Tage härter, leichte Tage leichter.", "Foster 1998, Med Sci Sports Exerc")

    dist = ctx["distribution"]
    if dist:
        kind, low = dist["kind"], dist["low_pct"] or 0
        if kind == "schwellenbetont" or (low < 70 and kind != "polarisiert"):
            add("Intensität", "warn", "Zu viel \"mittelhart\"", f"Nur {low:.0f} % deiner Zeit liegt in der lockeren Zone (letzte 4 Wochen), Verteilung: {kind}. Ausdauersportler auf Top-Niveau trainieren ~75–80 % locker. Lockere Einheiten bewusst langsamer, harte gezielt härter.", "Seiler 2010, IJSPP; Stöggl & Sperlich 2014")
        else:
            add("Intensität", "ok", f"Verteilung {kind}", f"{low:.0f} % locker / {dist['mid_pct']:.0f} % Schwelle / {dist['high_pct']:.0f} % hoch (letzte 4 Wochen). Polarisierungsindex: {dist['pi'] if dist['pi'] is not None else '–'}.", "Treff et al. 2019, Front Physiol")

    h = ctx["hrv"]
    if h["cv7"].notna().sum() > 14:
        cv_tr = _slope_per_day(h["cv7"].iloc[-21:])
        mean_tr = _slope_per_day(h["ln7"].iloc[-21:])
        if math.isfinite(cv_tr) and math.isfinite(mean_tr):
            if cv_tr > 0 and mean_tr < 0:
                add("Erholung", "warn", "HRV: fallend & unruhig", "In den letzten 3 Wochen sinkt dein HRV-Mittel, während die Tag-zu-Tag-Schwankung (CV) steigt. Dieses Muster tritt bei unzureichender Erholung auf.", "Plews et al. 2012, Eur J Appl Physiol")
            elif cv_tr < 0 and mean_tr > 0:
                add("Erholung", "ok", "HRV: positive Anpassung", "Steigendes HRV-Mittel bei sinkender Schwankung – typisch für gute Anpassung an das Training.", "Plews et al. 2013, Sports Med")

    sl = ctx["sleep"]
    if sl:
        if (sl.get("avg7_h") or 9) < 7:
            add("Schlaf", "warn", "Zu wenig Schlaf", f"Ø {sl['avg7_h']:.1f} h in den letzten 7 Nächten. Sportler profitieren von 8–10 h Bettzeit; Schlafverlängerung verbesserte in Studien Sprintzeit und Treffsicherheit.", "Mah et al. 2011, Sleep; Fullagar et al. 2015")
        if (sl.get("regularity_min") or 0) > 60:
            add("Schlaf", "warn", "Unregelmäßiger Schlaf", f"Deine Schlafmitte schwankt um ±{sl['regularity_min']:.0f} min. Regelmäßige Schlafzeiten hängen stärker mit Gesundheit zusammen als die reine Dauer.", "Windred et al. 2024, Sleep")
        sj = sl.get("social_jetlag_h")
        if sj is not None and abs(sj) >= 1:
            add("Schlaf", "info", "Sozialer Jetlag", f"Am Wochenende liegt deine Schlafmitte {sj:+.1f} h verschoben – wie eine kleine Zeitzonenreise jede Woche.", "Wittmann et al. 2006, Chronobiol Int")

    ef = ctx["ef"]
    if len(ef) >= 6:
        recent = ef[ef["date"] >= ef["date"].max() - pd.Timedelta(days=90)].set_index("date")["ef"]
        slope = _slope_per_day(recent)
        if math.isfinite(slope) and recent.mean() > 0:
            pct = slope * 30 / recent.mean() * 100
            lvl = "ok" if pct > 0.5 else ("warn" if pct < -1 else "info")
            add("Leistung", lvl, "Aerobe Effizienz", f"Dein Tempo pro Herzschlag bei lockeren Läufen ändert sich um {pct:+.1f} % pro Monat (90 Tage).", "Allen & Coggan; Friel")

    dec = ctx["decoupling"]
    if dec:
        vals = [d["decoupling_pct"] for d in dec[-6:] if d["decoupling_pct"] is not None]
        if vals:
            m = float(np.median(vals))
            lvl = "ok" if m < 5 else "warn"
            add("Leistung", lvl, "Aerobe Entkopplung", f"Median {m:.1f} % in den letzten langen Einheiten. < 5 % = solide Grundlagenausdauer für diese Dauer; darüber lohnt mehr lockerer Umfang.", "Friel, The Triathlete's Training Bible")

    rk = ctx["riegel"]
    if math.isfinite(rk):
        if rk > 1.08:
            add("Leistung", "info", "Profil: Tempo > Ausdauer", f"Riegel-Exponent {rk:.3f} (10 km → Halbmarathon). Du verlierst auf langen Distanzen überdurchschnittlich – mehr lange Läufe und Tempodauerläufe.", "Riegel 1981, American Scientist")
        elif rk < 1.05:
            add("Leistung", "info", "Profil: Ausdauer > Tempo", f"Riegel-Exponent {rk:.3f}. Deine Ausdauer ist stark, Potenzial liegt im Tempo – kurze Intervalle (VO2max) und Steigerungen.", "Riegel 1981")

    vo2 = ctx["vo2_change"]
    if math.isfinite(vo2):
        add("Leistung", "ok" if vo2 >= 0 else "info", "VO2max-Trend", f"{vo2:+.1f} ml/kg/min in 8 Wochen.", "")

    wt = ctx["weight_trend"]
    if math.isfinite(wt):
        if wt < -0.75:
            add("Körper", "warn", "Schneller Gewichtsverlust", f"{wt:+.2f} kg/Woche. Mehr als ~0,5–1 % des Körpergewichts pro Woche gefährdet Muskelmasse und Leistungsfähigkeit (Energieverfügbarkeit, RED-S).", "Mountjoy et al. 2018, Br J Sports Med")
        else:
            add("Körper", "info", "Gewichtstrend", f"{wt:+.2f} kg/Woche (4-Wochen-Trend).", "")

    im = ctx["im_week"]
    if math.isfinite(im):
        lvl = "ok" if im >= 150 else "warn"
        add("Gesundheit", lvl, "Bewegungsempfehlung", f"{im:.0f} Intensitätsminuten in den letzten 7 Tagen (WHO: ≥ 150 moderat bzw. 75 intensiv).", "WHO-Leitlinie 2020")

    for c in ctx["correlations"]:
        if c["significant"]:
            direction = "höher" if c["r"] > 0 else "niedriger"
            add("Zusammenhänge", "info", c["text"], f"Bei dir gilt: je höher {c['x']}, desto {direction} {c['y']} (Spearman r = {c['r']:+.2f}, n = {c['n']}). Das ist eine Korrelation – mögliche Störfaktoren beachten.", "")
    return out


# --------------------------------------------------------------------------- #
# Gesamtanalyse


def _records(df: pd.DataFrame, cols: dict[str, int]) -> dict[str, list]:
    return {c: [_f(v, nd) for v in df[c]] if c in df else [None] * len(df) for c, nd in cols.items()}


def analyze(data: dict[str, Any], sleep_need_h: float = 8.0) -> dict[str, Any]:
    daily: pd.DataFrame = data["daily"]
    acts: pd.DataFrame = data["activities"]
    prof = data["profile"]
    if daily.empty:
        raise ValueError("Keine Tageswerte vorhanden.")

    # Zeitachse über Tageswerte und Aktivitäten
    start = min(daily.index.min(), acts["date"].min() if not acts.empty else daily.index.min())
    end = max(daily.index.max(), acts["date"].max() if not acts.empty else daily.index.max())
    idx = pd.date_range(start, end, freq="D")
    daily = daily.reindex(idx)

    act_load, load_source = activity_load(acts, daily, prof)
    if not acts.empty:
        acts = acts.assign(load=act_load.values)
        load = acts.groupby("date")["load"].sum().reindex(idx, fill_value=0.0)
        late = acts[acts["end_hour"] >= 19].groupby("date").size().reindex(idx, fill_value=0)
        late_training = (late > 0).astype(float)
    else:
        load = pd.Series(0.0, index=idx)
        late_training = pd.Series(0.0, index=idx)

    p = pmc(load)
    h = hrv_model(daily.get("hrv", pd.Series(np.nan, index=idx)))
    r = rhr_model(daily.get("rhr", pd.Series(np.nan, index=idx)))
    rec = recovery_index(h["z"], r["z"], daily.get("sleep_score", pd.Series(np.nan, index=idx)))
    sleep = sleep_model(daily, sleep_need_h)
    since28 = idx[-1] - pd.Timedelta(days=27)
    dist = intensity_distribution(acts, since28)
    ef = efficiency_factor(acts)
    dec = decoupling(acts, data.get("activity_details") or {})

    rp = data["race_predictions"]
    riegel = np.nan
    race = {}
    if not rp.empty:
        lastp = rp.iloc[-1]
        riegel = riegel_exponent(lastp["t10k"], 10, lastp["thm"], 21.0975)
        old = rp[rp.index <= rp.index[-1] - pd.Timedelta(days=56)]
        race = {
            "date": rp.index[-1].strftime("%Y-%m-%d"),
            "items": [
                {
                    "label": label,
                    "time": fmt_time(lastp[col]),
                    "pace": fmt_pace(dist_km * 1000 / lastp[col]) if lastp[col] else "–",
                    "change_s": _f(lastp[col] - old.iloc[-1][col], 0) if not old.empty else None,
                }
                for label, col, dist_km in (("5 km", "t5k", 5), ("10 km", "t10k", 10), ("Halbmarathon", "thm", 21.0975), ("Marathon", "tm", 42.195))
            ],
        }

    vo2 = daily.get("vo2max", pd.Series(np.nan, index=idx)).ffill(limit=21)
    vo2_now = _last(vo2)
    vo2_old = vo2.loc[: idx[-1] - pd.Timedelta(days=56)].dropna()
    vo2_change = vo2_now - float(vo2_old.iloc[-1]) if len(vo2_old) else np.nan

    body = data["body"]
    weight_trend = np.nan
    if not body.empty and "weight_kg" in body:
        w = body["weight_kg"].dropna()
        w = w[w.index >= w.index.max() - pd.Timedelta(days=28)]
        if len(w) >= 4:
            weight_trend = _slope_per_day(w) * 7

    im_daily = daily.get("im_moderate", pd.Series(np.nan, index=idx)).fillna(0) + 2 * daily.get("im_vigorous", pd.Series(np.nan, index=idx)).fillna(0)
    im_week = float(im_daily.iloc[-7:].sum()) if daily.get("im_moderate") is not None and daily["im_moderate"].notna().any() else np.nan

    frame = pd.DataFrame(
        {
            "load": load,
            "hrv": daily.get("hrv"),
            "rhr": daily.get("rhr"),
            "sleep_score": daily.get("sleep_score"),
            "sleep_h": daily.get("sleep_h"),
            "stress_avg": daily.get("stress_avg"),
            "steps": daily.get("steps"),
            "sleep_start": daily.get("sleep_start"),
            "late_training": late_training.where(load > 0),
            "water_l": daily.get("water_l"),
        },
        index=idx,
    )
    corr = correlations(frame)
    dose = dose_response(load, h["z"], r["delta"])

    # Werte für "heute": letzter Tag mit Morgenwerten
    today = {
        "hrv_z": _last(h["z"].iloc[-2:]),
        "hrv7": _last(h["ln7"].iloc[-3:]),
        "hrv_lo": _last(h["lo"]),
        "hrv_hi": _last(h["hi"]),
        "rhr_delta": _last(r["delta"].iloc[-2:]),
        "sleep_h": _last(daily.get("sleep_h", pd.Series(dtype=float)).iloc[-2:]),
        "sleep_debt": sleep.get("debt7_h") if sleep and sleep.get("debt7_h") is not None else np.nan,
        "tsb": float(p["ctl"].iloc[-1] - p["atl"].iloc[-1]),
        "acwr": _last(p["acwr"]),
        "readiness": _last(daily.get("readiness", pd.Series(dtype=float)).iloc[-2:]),
    }
    coach = coach_today(today)

    ctx = {
        "pmc": p,
        "hrv": h,
        "sleep": sleep,
        "distribution": dist,
        "ef": ef,
        "decoupling": dec,
        "riegel": riegel,
        "vo2_change": vo2_change,
        "weight_trend": weight_trend,
        "im_week": im_week,
        "correlations": corr,
    }

    # ---- Wochenaggregation ------------------------------------------------ #
    wk = pd.DataFrame(index=idx)
    wk["load"] = load
    wk["steps"] = daily.get("steps")
    wk["im"] = im_daily
    if not acts.empty:
        a = acts.copy()
        a["low"] = a["z1_min"].fillna(0) + a["z2_min"].fillna(0)
        a["mid"] = a["z3_min"].fillna(0)
        a["high"] = a["z4_min"].fillna(0) + a["z5_min"].fillna(0)
        per_day = a.groupby("date")[["low", "mid", "high", "duration_min", "distance_km"]].sum()
        for sport in ("run", "ride", "swim", "strength"):
            per_day[f"h_{sport}"] = a[a["sport"] == sport].groupby("date")["duration_min"].sum() / 60
        per_day["h_other"] = a[~a["sport"].isin(["run", "ride", "swim", "strength"])].groupby("date")["duration_min"].sum() / 60
        wk = wk.join(per_day.reindex(idx))
    weekly = wk.resample("W-MON", label="left", closed="left").agg(
        {c: "sum" for c in wk.columns if c != "steps"} | {"steps": "mean"}
    )
    weekly["monotony"] = p["monotony"].resample("W-MON", label="left", closed="left").last()
    weekly["strain"] = p["strain"].resample("W-MON", label="left", closed="left").last()
    for c in ("low", "mid", "high"):
        if c in weekly:
            weekly[c] = weekly[c] / 60  # Stunden

    # ---- Ausgabe ---------------------------------------------------------- #
    s = pd.DataFrame(index=idx)
    s = s.join(daily)
    s = s.join(p[["load", "ctl", "atl", "tsb", "acwr", "monotony"]])
    s["hrv7"] = h["ln7_ms"]
    s["hrv_lo"] = np.exp(h["lo"])
    s["hrv_hi"] = np.exp(h["hi"])
    s["hrv_cv7"] = h["cv7"]
    s["rhr_base"] = r["base"]
    s["recovery"] = rec

    daily_cols = {
        "load": 0, "ctl": 1, "atl": 1, "tsb": 1, "acwr": 2, "monotony": 2,
        "hrv": 0, "hrv7": 1, "hrv_lo": 1, "hrv_hi": 1, "hrv_cv7": 1, "hrv_base_low": 0, "hrv_base_high": 0,
        "rhr": 0, "rhr_base": 1, "recovery": 0, "readiness": 0,
        "sleep_h": 2, "deep_h": 2, "light_h": 2, "rem_h": 2, "awake_h": 2, "sleep_score": 0, "sleep_start": 2,
        "stress_avg": 0, "bb_high": 0, "bb_low": 0, "bb_wake": 0,
        "steps": 0, "step_goal": 0, "im_moderate": 0, "im_vigorous": 0, "kcal_total": 0, "kcal_active": 0,
        "vo2max": 1, "vo2max_cycling": 1, "resp_sleep": 1, "spo2_sleep": 0, "spo2_avg": 0, "floors": 0,
        "garmin_acwr": 2, "load_aer_low": 0, "load_aer_high": 0, "load_anaer": 0,
    }
    series = {"dates": [d.strftime("%Y-%m-%d") for d in idx], **_records(s, daily_cols)}

    weekly_out = {"weeks": [d.strftime("%Y-%m-%d") for d in weekly.index]}
    weekly_out.update(
        _records(
            weekly,
            {"load": 0, "low": 2, "mid": 2, "high": 2, "h_run": 2, "h_ride": 2, "h_swim": 2, "h_strength": 2, "h_other": 2,
             "distance_km": 1, "steps": 0, "im": 0, "monotony": 2, "strain": 0},
        )
    )

    body_out = {}
    if not body.empty:
        b = body.copy()
        b["weight_trend"] = b["weight_kg"].ewm(span=7).mean()
        body_out = {"dates": [d.strftime("%Y-%m-%d") for d in b.index], **_records(b, {"weight_kg": 1, "weight_trend": 2, "body_fat": 1, "muscle_kg": 1})}

    act_rows = []
    if not acts.empty:
        for _, a in acts.sort_values("start", ascending=False).iterrows():
            act_rows.append(
                {
                    "date": a["start"].strftime("%Y-%m-%d"),
                    "time": a["start"].strftime("%H:%M"),
                    "name": str(a["name"]),
                    "sport": a["sport"],
                    "type": a["type"],
                    "min": _f(a["duration_min"], 0),
                    "km": _f(a["distance_km"], 2),
                    "hr": _f(a["avg_hr"], 0),
                    "pace": fmt_pace(a["avg_speed"]) if a["sport"] in ("run", "walk") else None,
                    "kmh": _f(a["avg_speed"] * 3.6, 1) if a["sport"] == "ride" else None,
                    "power": _f(a["avg_power"], 0),
                    "load": _f(a["load"], 0),
                    "te_aer": _f(a["te_aer"], 1),
                    "te_anaer": _f(a["te_anaer"], 1),
                    "label": a.get("te_label"),
                }
            )

    ef_out = {"dates": [d.strftime("%Y-%m-%d") for d in ef["date"]], "ef": [_f(v, 3) for v in ef["ef"]]} if len(ef) else {}

    last = lambda col, nd=1: _f(_last(s[col]) if col in s else np.nan, nd)  # noqa: E731
    kpi = {
        "recovery": last("recovery", 0),
        "readiness": last("readiness", 0),
        "hrv": last("hrv", 0),
        "hrv7": last("hrv7", 0),
        "hrv_lo": last("hrv_lo", 0),
        "hrv_hi": last("hrv_hi", 0),
        "rhr": last("rhr", 0),
        "rhr_base": last("rhr_base", 1),
        "sleep_h": last("sleep_h", 1),
        "sleep_score": last("sleep_score", 0),
        "bb_wake": last("bb_wake", 0),
        "ctl": _f(p["ctl"].iloc[-1], 0),
        "atl": _f(p["atl"].iloc[-1], 0),
        "tsb": _f(today["tsb"], 0),
        "acwr": _f(today["acwr"], 2),
        "vo2max": _f(vo2_now, 1),
        "vo2_change": _f(vo2_change, 1),
        "steps7": _f(daily.get("steps", pd.Series(dtype=float)).iloc[-7:].mean(), 0),
        "im_week": _f(im_week, 0),
        "stress7": _f(daily.get("stress_avg", pd.Series(dtype=float)).iloc[-7:].mean(), 0),
        "training_status": training_status_de(next((x for x in reversed(daily.get("training_status", pd.Series(dtype=object)).tolist()) if isinstance(x, str)), None)),
    }

    coverage = {
        LABELS.get(c, c): _f(daily[c].notna().mean() * 100, 0)
        for c in ("hrv", "rhr", "sleep_score", "stress_avg", "steps", "vo2max")
        if c in daily
    }

    meta = data.get("meta") or {}
    return {
        "meta": {
            "name": prof.get("name"),
            "source": meta.get("source", "garmin"),
            "fetched_at": meta.get("fetched_at"),
            "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
            "start": idx[0].strftime("%Y-%m-%d"),
            "end": idx[-1].strftime("%Y-%m-%d"),
            "load_source": load_source,
            "n_activities": int(len(acts)),
            "coverage": coverage,
            "errors": len(meta.get("errors") or []),
        },
        "profile": {k: (_f(v) if isinstance(v, (int, float)) else v) for k, v in prof.items() if k != "hr_zones"},
        "records": data.get("personal_records") or [],
        "coach": coach,
        "kpi": kpi,
        "insights": insights(ctx),
        "sleep": sleep,
        "distribution": dist,
        "race": race,
        "riegel": _f(riegel, 3),
        "series": series,
        "weekly": weekly_out,
        "body": body_out,
        "ef": ef_out,
        "decoupling": dec,
        "correlations": corr,
        "dose_response": dose,
        "weekday": weekday_pattern(daily, h["z"]),
        "activities": act_rows,
        "endurance_score": {"dates": [d.strftime("%Y-%m-%d") for d in data["endurance_score"].index], "value": [_f(v, 0) for v in data["endurance_score"]["value"]]} if not data["endurance_score"].empty else {},
        "hill_score": {"dates": [d.strftime("%Y-%m-%d") for d in data["hill_score"].index], "value": [_f(v, 0) for v in data["hill_score"]["value"]]} if not data["hill_score"].empty else {},
    }
