"""Tests for forecast-error metrics (backend/app/domain/metrics.py).

Expected numbers are hand-computed in comments so they can be checked on paper.
"""

import json
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.app.domain.metrics import (
    by_day,
    by_horizon,
    by_hour,
    by_level,
    histogram,
    join_errors,
    summary_metrics,
    to_hourly_actuals,
)


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def errors_frame(errors, actuals, start="2026-01-15T00:00", delivery_date=None) -> pd.DataFrame:
    """Build a joined-errors frame directly (forecast = actual + error)."""
    n = len(errors)
    e = np.array(errors, dtype=float)
    a = np.array(actuals, dtype=float)
    df = pd.DataFrame(
        {
            "startTime": pd.date_range(ts(start), periods=n, freq="h"),
            "forecastMW": a + e,
            "actualMW": a,
            "errorMW": e,
        }
    )
    if delivery_date is not None:
        df["deliveryDate"] = delivery_date
    return df


# Hand-worked sample: valid errors [100, -200, 300, -400], one NaN row ignored.
SAMPLE = errors_frame([100, -200, 300, -400, np.nan], [1000, 1000, 2000, 1000, 500])


# --------------------------------------------------------------------------- hourly actuals


def test_to_hourly_actuals_needs_both_halves():
    actuals = pd.DataFrame(
        {
            "startTime": [ts("2026-01-15T00:00"), ts("2026-01-15T00:30"),  # full hour
                          ts("2026-01-15T01:00"),                          # 01:30 missing
                          ts("2026-01-15T02:30")],                         # 02:00 missing
            "generation": [1000.0, 2000.0, 500.0, 700.0],
        }
    )
    hourly = to_hourly_actuals(actuals)
    assert list(hourly.columns) == ["startTime", "actualMW"]
    by_time = dict(zip(hourly["startTime"], hourly["actualMW"]))
    assert by_time[ts("2026-01-15T00:00")] == 1500.0  # mean, not sum
    assert math.isnan(by_time[ts("2026-01-15T01:00")])
    assert math.isnan(by_time[ts("2026-01-15T02:00")])


def test_to_hourly_actuals_nan_generation_counts_as_missing():
    actuals = pd.DataFrame(
        {"startTime": [ts("2026-01-15T00:00"), ts("2026-01-15T00:30")], "generation": [1000.0, np.nan]}
    )
    assert to_hourly_actuals(actuals)["actualMW"].isna().all()


def test_join_errors_is_forecast_minus_actual_left_join():
    selected = pd.DataFrame(
        {
            "startTime": [ts("2026-01-15T00:00"), ts("2026-01-15T01:00")],
            "forecastMW": [1200.0, 900.0],
            "status": ["ok", "ok"],
        }
    )
    hourly = pd.DataFrame({"startTime": [ts("2026-01-15T00:00")], "actualMW": [1000.0]})
    joined = join_errors(selected, hourly)
    assert len(joined) == 2
    assert joined["errorMW"].iloc[0] == 200.0  # over-forecast is positive
    assert math.isnan(joined["errorMW"].iloc[1])
    assert "status" in joined.columns


# --------------------------------------------------------------------------- summary


def test_summary_metrics_hand_computed():
    m = summary_metrics(SAMPLE)
    assert m["n"] == 4
    assert m["mae"] == pytest.approx(250.0)                 # (100+200+300+400)/4
    assert m["rmse"] == pytest.approx(math.sqrt(75000.0))   # sqrt((1+4+9+16)e4 / 4)
    assert m["bias"] == pytest.approx(-50.0)                # (100-200+300-400)/4
    assert m["wape"] == pytest.approx(0.2)                  # 1000 / 5000 (NaN row's 500 excluded)
    # sorted errors [-400, -200, 100, 300]; linear position = q * (n-1)
    assert m["p5"] == pytest.approx(-370.0)   # pos 0.15
    assert m["p10"] == pytest.approx(-340.0)  # pos 0.3
    assert m["p50"] == pytest.approx(-50.0)   # pos 1.5
    assert m["p90"] == pytest.approx(240.0)   # pos 2.7
    assert m["p95"] == pytest.approx(270.0)   # pos 2.85
    assert m["maxAbs"] == 400.0


def test_summary_metrics_returns_plain_python_types():
    m = summary_metrics(SAMPLE)
    assert type(m["n"]) is int
    assert all(type(v) is float for k, v in m.items() if k != "n")
    json.dumps(m)  # must be JSON serialisable


def test_summary_wape_none_when_actual_sum_zero():
    m = summary_metrics(errors_frame([10, -10], [0, 0]))
    assert m["wape"] is None
    assert m["mae"] == 10.0


def test_summary_all_none_when_no_valid_rows():
    m = summary_metrics(errors_frame([np.nan, np.nan], [100, 100]))
    assert m["n"] == 0
    assert all(v is None for k, v in m.items() if k != "n")


# --------------------------------------------------------------------------- breakdowns


def test_by_hour_shape_and_local_time():
    # Summer day: 2026-07-01T00:00Z is 01:00 BST, so it lands in local hour 1.
    df = errors_frame([100, -100, np.nan], [1000, 1000, 1000], start="2026-07-01T00:00")
    rows = by_hour(df)
    assert len(rows) == 24
    assert [r["hour"] for r in rows] == list(range(24))
    assert set(rows[0]) == {"hour", "n", "mae", "bias", "p10", "p90"}
    assert rows[1]["n"] == 1 and rows[1]["bias"] == 100.0
    assert rows[2]["n"] == 1 and rows[2]["bias"] == -100.0
    assert rows[3]["n"] == 0 and rows[3]["mae"] is None  # only NaN error there
    assert rows[0]["n"] == 0
    json.dumps(rows)


def test_by_day_groups_by_delivery_date():
    d1 = errors_frame([100, 300], [1000, 1000], delivery_date=date(2026, 1, 15))
    d2 = errors_frame([-50, np.nan], [1000, 1000], start="2026-01-16T00:00",
                      delivery_date=date(2026, 1, 16))
    d3 = errors_frame([np.nan], [1000], start="2026-01-17T00:00", delivery_date=date(2026, 1, 17))
    rows = by_day(pd.concat([d2, d1, d3], ignore_index=True))
    assert rows == [
        {"date": "2026-01-15", "n": 2, "mae": 200.0, "bias": 200.0},
        {"date": "2026-01-16", "n": 1, "mae": 50.0, "bias": -50.0},
        {"date": "2026-01-17", "n": 0, "mae": None, "bias": None},
    ]


def test_by_level_terciles():
    # 6 valid rows sorted by actual -> pairs (100,200) / (300,400) / (500,600)
    df = errors_frame([10, -10, 20, -20, 30, 50, 999],
                      [600, 100, 300, 200, 500, 400, 50])
    df.loc[6, "errorMW"] = np.nan  # actual 50 must not create its own bin
    rows = by_level(df)
    assert [r["level"] for r in rows] == ["low", "medium", "high"]
    low, mid, high = rows
    assert (low["minMW"], low["maxMW"], low["n"]) == (100.0, 200.0, 2)
    assert low["bias"] == pytest.approx((-10 + -20) / 2)
    assert (mid["minMW"], mid["maxMW"], mid["n"]) == (300.0, 400.0, 2)
    assert mid["mae"] == pytest.approx((20 + 50) / 2)
    assert (high["minMW"], high["maxMW"], high["n"]) == (500.0, 600.0, 2)
    json.dumps(rows)


def test_by_level_handles_tiny_samples():
    rows = by_level(errors_frame([10], [100]))
    assert [r["n"] for r in rows] == [1, 0, 0]
    assert rows[1]["minMW"] is None and rows[1]["mae"] is None


def test_histogram_bins_and_gaps():
    df = errors_frame([-600, -100, 0, 499, 1600, np.nan], [1000] * 6)
    rows = histogram(df, bin_mw=500)
    assert rows == [
        {"binStartMW": -1000.0, "count": 1},
        {"binStartMW": -500.0, "count": 1},
        {"binStartMW": 0.0, "count": 2},
        {"binStartMW": 500.0, "count": 0},
        {"binStartMW": 1000.0, "count": 0},
        {"binStartMW": 1500.0, "count": 1},
    ]
    assert histogram(errors_frame([np.nan], [1])) == []


# --------------------------------------------------------------------------- horizon


def test_by_horizon_bucket_assignment_and_backcast_exclusion():
    target = ts("2026-01-15T12:00")
    forecasts = pd.DataFrame(
        {
            "publishTime": [
                ts("2026-01-15T09:30"),  # lead 2.5  -> 0-6h
                ts("2026-01-15T04:30"),  # lead 7.5  -> 6-12h
                ts("2026-01-14T22:30"),  # lead 13.5 -> 12-18h
                ts("2026-01-14T11:30"),  # lead 24.5 -> 24-30h
                ts("2026-01-13T02:30"),  # lead 57.5 -> beyond max_h, dropped
                ts("2026-01-15T15:30"),  # backcast (published after startTime)
                ts("2026-01-15T12:00"),  # backcast (startTime == publishTime)
            ],
            "startTime": [target] * 7,
            "generation": [1100.0, 1200.0, 1300.0, 700.0, 5000.0, 9999.0, 9999.0],
        }
    )
    hourly = pd.DataFrame({"startTime": [target], "actualMW": [1000.0]})
    rows = by_horizon(forecasts, hourly, ts("2026-01-15T00:00"), ts("2026-01-16T00:00"))

    assert len(rows) == 8  # 48 / 6
    assert [r["bucket"] for r in rows][:3] == ["0-6h", "6-12h", "12-18h"]
    assert rows[-1]["bucket"] == "42-48h"
    assert set(rows[0]) == {"bucket", "minH", "maxH", "n", "mae", "rmse", "bias"}
    by_name = {r["bucket"]: r for r in rows}
    assert by_name["0-6h"]["n"] == 1 and by_name["0-6h"]["bias"] == 100.0
    assert by_name["6-12h"]["bias"] == 200.0
    assert by_name["12-18h"]["bias"] == 300.0
    assert by_name["18-24h"]["n"] == 0 and by_name["18-24h"]["mae"] is None
    assert by_name["24-30h"]["bias"] == -300.0
    assert sum(r["n"] for r in rows) == 4  # backcasts and >48h excluded
    json.dumps(rows)


def test_by_horizon_respects_time_range_and_missing_actuals():
    forecasts = pd.DataFrame(
        {
            "publishTime": [ts("2026-01-14T22:30")] * 3,
            "startTime": [ts("2026-01-15T00:00"), ts("2026-01-15T01:00"), ts("2026-01-16T00:00")],
            "generation": [1100.0, 1200.0, 1300.0],
        }
    )
    hourly = pd.DataFrame(
        {"startTime": [ts("2026-01-15T00:00"), ts("2026-01-16T00:00")], "actualMW": [1000.0, 1000.0]}
    )
    rows = by_horizon(forecasts, hourly, ts("2026-01-15T00:00"), ts("2026-01-16T00:00"))
    # 01:00 has no actual (NaN error); 2026-01-16T00:00 is outside the half-open range.
    assert sum(r["n"] for r in rows) == 1
    assert rows[0]["bias"] == 100.0
