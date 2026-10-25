"""API tests run against the committed January 2026 data in data/."""
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_meta_lists_delivery_days():
    body = client.get("/api/meta").json()
    assert body["deliveryDays"][0] == "2026-01-02"
    assert body["deliveryDays"][-1] == "2026-02-01"
    assert body["defaults"]["cutoffTime"] == "09:00"


def test_forecast_uses_latest_run_before_cutoff():
    body = client.get("/api/forecast", params={"date": "2026-01-15"}).json()
    assert body["cutoffUtc"] == "2026-01-14T09:00:00Z"
    assert body["runsUsed"] == ["2026-01-14T07:30:00Z"]
    assert len(body["points"]) == 24
    first = body["points"][0]
    assert first["startTime"] == "2026-01-15T00:00:00Z"
    assert first["horizonH"] == 16.5
    assert first["bandLowMW"] <= first["forecastMW"] <= first["bandHighMW"]
    assert len(body["actualsHalfHourly"]) == 48


def test_later_cutoff_picks_later_run():
    body = client.get("/api/forecast", params={"date": "2026-01-15", "cutoff": "11:00"}).json()
    assert body["runsUsed"] == ["2026-01-14T09:30:00Z"]


def test_no_forecast_published_after_cutoff_is_used():
    body = client.get("/api/forecast", params={"date": "2026-01-20", "cutoff": "06:00"}).json()
    for p in body["points"]:
        if p["publishTime"]:
            assert p["publishTime"] <= body["cutoffUtc"]


def test_accuracy_shape():
    body = client.get("/api/accuracy").json()
    assert body["from"] == "2026-01-02"
    assert body["metrics"]["n"] > 0
    assert body["metrics"]["mae"] > 0
    assert 0.1 < body["metrics"]["wape"] < 0.3 and body["metrics"]["wape"] != 0.2  # not rounded to 1 dp
    assert len(body["byHour"]) == 24
    assert len(body["byDay"]) == 31


def test_horizon_highlights_cutoff_range():
    body = client.get("/api/accuracy/horizon").json()
    assert body["buckets"]
    assert body["cutoffHorizonH"]["min"] == 16.5


def test_bad_input_returns_422():
    assert client.get("/api/forecast", params={"date": "2025-12-01"}).status_code == 422
    assert client.get("/api/forecast", params={"date": "2026-01-15", "cutoff": "9am"}).status_code == 422
    assert client.get("/api/accuracy", params={"from": "2026-01-20", "to": "2026-01-10"}).status_code == 422
