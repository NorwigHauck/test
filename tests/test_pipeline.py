import json
import math

import numpy as np
import pandas as pd
import pytest

from garmin_dashboard import analysis, demo, normalize, report


@pytest.fixture(scope="module")
def demo_data(tmp_path_factory):
    path = tmp_path_factory.mktemp("data")
    demo.generate(path, days=200)
    return normalize.load(path)


def test_normalize_demo(demo_data):
    daily = demo_data["daily"]
    assert len(daily) == 200
    for col in ("hrv", "rhr", "sleep_h", "sleep_score", "steps", "vo2max", "readiness"):
        assert daily[col].notna().mean() > 0.95, col
    acts = demo_data["activities"]
    assert len(acts) > 100
    assert set(acts["sport"]) >= {"run", "ride", "strength"}
    assert not demo_data["body"].empty
    assert demo_data["profile"]["max_hr"] == 188


def test_analyze_produces_valid_json(demo_data, tmp_path):
    result = analysis.analyze(demo_data)
    json.dumps(result, allow_nan=False)
    assert result["coach"]["level"] in ("good", "warning", "critical")
    assert len(result["series"]["dates"]) == len(result["series"]["ctl"])
    assert result["distribution"]["low_pct"] > 50
    out = tmp_path / "d.html"
    report.write(result, out)
    html = out.read_text()
    assert "/*__DATA__*/" not in html
    assert '"coach"' in html


def test_dose_response_detects_load_effect(demo_data):
    # Im Demo-Modell senkt hohe Last die HRV der Folgenacht
    result = analysis.analyze(demo_data)
    groups = {g["group"].split()[0]: g["hrv_z"] for g in result["dose_response"]}
    assert groups["Hart"] < groups["Ruhetag"]


def test_pmc_constant_load_converges():
    idx = pd.date_range("2025-01-01", periods=300)
    p = analysis.pmc(pd.Series(100.0, index=idx))
    assert p["ctl"].iloc[-1] == pytest.approx(100, abs=0.5)
    assert p["tsb"].iloc[-1] == pytest.approx(0, abs=0.5)
    assert p["acwr"].iloc[-1] == pytest.approx(1, abs=0.01)


def test_hrv_model_flags_drop():
    idx = pd.date_range("2025-01-01", periods=90)
    rng = np.random.default_rng(1)
    hrv = pd.Series(np.exp(4 + rng.normal(0, 0.05, 90)), index=idx)
    hrv.iloc[-7:] *= 0.7
    m = analysis.hrv_model(hrv)
    assert m["ln7"].iloc[-1] < m["lo"].iloc[-1]


def test_spearman():
    x = pd.Series(range(50), dtype=float)
    r, p, n = analysis.spearman(x, x * 2 + 1)
    assert r == pytest.approx(1.0) and p < 1e-6 and n == 50
    r, p, n = analysis.spearman(x, pd.Series([np.nan] * 50))
    assert math.isnan(r)


def test_pick_paths():
    obj = {"a": {"123": {"b": [{"c": 5}]}}}
    assert normalize.pick(obj, "a.*.b.0.c") == 5
    assert normalize.pick(obj, "x.y", "a.*.b.*.c") == 5
    assert normalize.pick(obj, "x", default=1) == 1


def test_riegel():
    assert analysis.riegel_exponent(2400, 10, 2400 * 2.10975**1.06, 21.0975) == pytest.approx(1.06)


def test_robust_with_missing_endpoints(tmp_path):
    """Uhren ohne HRV/Wettkampfprognose, keine Waage, keine Details."""
    import shutil

    demo.generate(tmp_path, days=60)
    raw = tmp_path / "raw"
    for folder in ("daily/hrv", "daily/readiness", "daily/training_status", "daily/max_metrics", "activity_details", "static"):
        shutil.rmtree(raw / folder)
    for f in ("body_composition", "race_predictions"):
        (raw / "range" / f"{f}.json").unlink()
    result = analysis.analyze(normalize.load(tmp_path))
    json.dumps(result, allow_nan=False)
    assert result["race"] == {}
    assert result["kpi"]["hrv"] is None


def test_activities_only(tmp_path):
    demo.generate(tmp_path, days=40)
    import shutil

    shutil.rmtree(tmp_path / "raw" / "daily" / "sleep")
    shutil.rmtree(tmp_path / "raw" / "daily" / "summary")
    result = analysis.analyze(normalize.load(tmp_path))
    json.dumps(result, allow_nan=False)


def test_fetch_uses_existing_garmin_methods(tmp_path, monkeypatch):
    """Alle Endpunkte mit einem signaturgetreuen Mock der garminconnect-Klasse aufrufen."""
    import datetime as dt
    from unittest import mock

    garminconnect = pytest.importorskip("garminconnect")
    from garmin_dashboard import fetch

    monkeypatch.setattr(fetch, "PAUSE_S", 0)
    g = mock.create_autospec(garminconnect.Garmin, instance=True)
    for name in dir(garminconnect.Garmin):
        if name.startswith("get_"):
            getattr(g, name).return_value = {"ok": True}
    f = fetch.Fetcher(g, tmp_path)
    today = dt.date.today()
    f.fetch_static()
    f.fetch_ranges(today - dt.timedelta(days=3), today)
    f.fetch_daily(today - dt.timedelta(days=1), today)
    assert f.errors == []
    assert (tmp_path / "raw" / "daily" / "sleep" / f"{today}.json").exists()


# --------------------------------------------------------------------------- #
# Garmin Personal API


class _FakeHttpError(Exception):
    def __init__(self, status):
        self.status_code = status
        self.response_text = ""


def _personal_fetcher(tmp_path, monkeypatch, responder):
    """PersonalApiFetcher ohne Netzwerk: ``responder(path, params, call_no)`` liefert Antwort oder Statuscode."""
    pytest.importorskip("garmin_personal_api")
    import garmin_personal_api
    from garmin_dashboard import fetch

    monkeypatch.setattr(fetch, "PERSONAL_PAUSE_S", 0)
    monkeypatch.setattr(garmin_personal_api, "HttpError", _FakeHttpError)
    state = {"n": 0, "refreshes": 0}

    class Auth:
        def obtain_token(self, key, force_refresh=False):
            state["refreshes"] += force_refresh
            return type("T", (), {"access_token": "tok"})()

    class Client:
        def __init__(self, token):
            pass

        def update_bearer_token(self, token):
            pass

        def get(self, path, params=None):
            state["n"] += 1
            res = responder(path, params, state["n"])
            if isinstance(res, int):
                raise _FakeHttpError(res)
            return res

    monkeypatch.setattr(garmin_personal_api, "PersonalApiAuthenticator", Auth)
    monkeypatch.setattr(garmin_personal_api, "GarminPersonalApiClient", Client)
    return fetch.PersonalApiFetcher("geheim", tmp_path), state


def test_personal_404_skips_day_and_401_refreshes_once(tmp_path, monkeypatch):
    import datetime as dt

    def responder(path, params, n):
        if n == 1:
            return 401  # Token abgelaufen -> einmal erneuern
        if "hrv" in path:
            return 404
        return {"calendarDate": "x", "sleepTimeSeconds": 1}

    f, state = _personal_fetcher(tmp_path, monkeypatch, responder)
    day = dt.date.today()
    f.fetch_daily(day, day)
    assert state["refreshes"] == 1
    assert (tmp_path / "raw" / "daily" / "sleep" / f"{day}.json").exists()
    assert not (tmp_path / "raw" / "daily" / "hrv" / f"{day}.json").exists()
    assert any("HTTP 404" in e for e in f.errors)


def test_personal_429_aborts(tmp_path, monkeypatch):
    import datetime as dt

    from garmin_dashboard import fetch

    f, _ = _personal_fetcher(tmp_path, monkeypatch, lambda p, q, n: 429)
    with pytest.raises(fetch.PersonalApiAbort):
        f.fetch_daily(dt.date.today(), dt.date.today())


def test_personal_second_401_aborts(tmp_path, monkeypatch):
    import datetime as dt

    from garmin_dashboard import fetch

    f, state = _personal_fetcher(tmp_path, monkeypatch, lambda p, q, n: 401)
    with pytest.raises(fetch.PersonalApiAbort):
        f.fetch_daily(dt.date.today(), dt.date.today())
    assert state["refreshes"] == 1


def test_personal_activity_pagination(tmp_path, monkeypatch):
    import datetime as dt

    def responder(path, params, n):
        if "activities" in path:
            return [{"activityId": params["start"] + i} for i in range(100 if params["start"] < 200 else 7)]
        return []

    f, _ = _personal_fetcher(tmp_path, monkeypatch, responder)
    f.fetch_ranges(dt.date.today() - dt.timedelta(days=40), dt.date.today())
    acts = json.loads((tmp_path / "raw" / "range" / "activities.json").read_text())[0]
    assert len(acts) == 207


def test_normalize_personal_api_formats(tmp_path):
    """Flaches Schlaf-DTO und Body-Battery-Bericht der Personal API."""
    raw = tmp_path / "raw"
    (raw / "daily" / "sleep").mkdir(parents=True)
    (raw / "range").mkdir()
    end_gmt = 1_790_000_000_000
    (raw / "daily" / "sleep" / "2026-10-01.json").write_text(json.dumps({
        "calendarDate": "2026-10-01", "sleepTimeSeconds": 27000, "deepSleepSeconds": 5000,
        "lightSleepSeconds": 15000, "remSleepSeconds": 7000, "awakeSleepSeconds": 600,
        "sleepStartTimestampLocal": 1_789_970_000_000, "sleepEndTimestampLocal": 1_789_997_600_000,
        "sleepEndTimestampGMT": end_gmt, "sleepScores": {"overall": {"value": 71}},
        "sleepNeed": {"actual": 540},
    }))
    (raw / "range" / "body_battery.json").write_text(json.dumps([[{
        "date": "2026-10-01", "charged": 50, "drained": 40,
        "bodyBatteryValueDescriptorDTOList": [
            {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
            {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
        ],
        "bodyBatteryValuesArray": [[end_gmt - 3_600_000, 60], [end_gmt + 60_000, 82], [end_gmt + 9_000_000, 25]],
    }]]))
    (raw / "static").mkdir()
    (raw / "static" / "profile.json").write_text(json.dumps({"displayName": "635a7133-ba9f-4fe2-bb43-a4ff5611755e"}))
    data = normalize.load(tmp_path)
    r = data["daily"].iloc[0]
    assert r["sleep_h"] == pytest.approx(7.5)
    assert r["sleep_score"] == 71
    assert r["sleep_need_h"] == 9
    assert (r["bb_high"], r["bb_low"], r["bb_wake"]) == (82, 25, 82)
    assert data["profile"]["name"] == "Athlet:in"
