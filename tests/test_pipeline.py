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
