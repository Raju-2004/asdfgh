"""Unit tests for the pure cleaning / data-quality functions in fetch_elexon (no network)."""

from datetime import date

import pandas as pd

from backend.ingest.fetch_elexon import (
    build_data_quality,
    clean_actuals,
    clean_forecasts,
    expected_actual_slots,
    incomplete_runs,
    missing_actual_slots,
)


def actual_row(start: str, publish: str, sp: int, gen: int, fuel: str = "WIND") -> dict:
    return {
        "dataset": "FUELHH",
        "publishTime": publish,
        "startTime": start,
        "settlementDate": start[:10],
        "settlementPeriod": sp,
        "fuelType": fuel,
        "generation": gen,
    }


def forecast_row(publish: str, start: str, gen: int) -> dict:
    return {"dataset": "WINDFOR", "publishTime": publish, "startTime": start, "generation": gen}


def utc(text: str) -> pd.Timestamp:
    return pd.Timestamp(text).tz_convert("UTC")


# --- actuals -----------------------------------------------------------------


def test_actuals_dedupe_keeps_latest_publish_time():
    raw = pd.DataFrame([
        actual_row("2026-01-02T00:00:00Z", "2026-01-02T01:00:00Z", 1, 999),  # later revision
        actual_row("2026-01-02T00:00:00Z", "2026-01-02T00:30:00Z", 1, 100),
        actual_row("2026-01-02T00:30:00Z", "2026-01-02T01:00:00Z", 2, 200),
    ])
    clean, stats = clean_actuals(raw, date(2026, 1, 2), date(2026, 1, 2))
    assert len(clean) == 2
    assert stats["duplicatesRemoved"] == 1
    assert clean.loc[0, "generation"] == 999
    assert clean.loc[0, "publishTime"] == utc("2026-01-02T01:00:00Z")


def test_actuals_filters_fuel_trims_range_and_sorts():
    raw = pd.DataFrame([
        actual_row("2026-01-03T00:00:00Z", "2026-01-03T00:30:00Z", 1, 3),  # after range
        actual_row("2026-01-02T00:30:00Z", "2026-01-02T01:00:00Z", 2, 2),
        actual_row("2026-01-01T23:30:00Z", "2026-01-02T00:00:00Z", 48, 0),  # before range
        actual_row("2026-01-02T00:00:00Z", "2026-01-02T00:30:00Z", 1, 1),
        actual_row("2026-01-02T00:00:00Z", "2026-01-02T00:30:00Z", 1, 50, fuel="CCGT"),
    ])
    clean, _ = clean_actuals(raw, date(2026, 1, 2), date(2026, 1, 2))
    assert list(clean.columns) == ["startTime", "publishTime", "settlementDate", "settlementPeriod", "generation"]
    assert list(clean["generation"]) == [1, 2]
    assert str(clean["startTime"].dt.tz) == "UTC"


def test_missing_slot_detection():
    raw = pd.DataFrame([
        actual_row(f"2026-01-02T{h:02d}:{m:02d}:00Z", "2026-01-03T00:00:00Z", 1, 1)
        for h in range(24) for m in (0, 30)
        if (h, m) != (5, 30)  # drop one slot
    ])
    clean, _ = clean_actuals(raw, date(2026, 1, 2), date(2026, 1, 2))
    expected, missing = missing_actual_slots(clean, date(2026, 1, 2), date(2026, 1, 2))
    assert expected == 48
    assert missing == [utc("2026-01-02T05:30:00Z")]


def test_expected_slots_follow_uk_settlement_day_on_dst_change():
    # Clocks go forward on 2026-03-29: a 46-slot settlement day starting at 00:00 UTC.
    assert len(expected_actual_slots(date(2026, 3, 29), date(2026, 3, 29))) == 46
    # A summer day starts at 23:00 UTC the previous evening.
    assert expected_actual_slots(date(2026, 6, 1), date(2026, 6, 1))[0] == utc("2026-05-31T23:00:00Z")


# --- forecasts ---------------------------------------------------------------


def test_forecasts_dedupe_keeps_last_and_trims_publish_range():
    raw = pd.DataFrame([
        forecast_row("2026-01-01T07:30:00Z", "2026-01-01T08:00:00Z", 1),
        forecast_row("2026-01-01T07:30:00Z", "2026-01-01T08:00:00Z", 2),  # chunk overlap repeat
        forecast_row("2025-12-29T22:30:00Z", "2025-12-30T08:00:00Z", 9),  # before range
        forecast_row("2026-01-02T00:00:00Z", "2026-01-02T08:00:00Z", 9),  # after range
    ])
    clean, stats = clean_forecasts(raw, utc("2025-12-30T00:00Z"), utc("2026-01-01T23:59Z"))
    assert len(clean) == 1
    assert clean.loc[0, "generation"] == 2
    assert stats["duplicatesRemoved"] == 1
    assert list(clean.columns) == ["publishTime", "startTime", "generation"]


def test_forecasts_backcast_rows_are_kept_and_sorted():
    raw = pd.DataFrame([
        forecast_row("2026-01-01T07:30:00Z", "2026-01-01T09:00:00Z", 3),
        forecast_row("2026-01-01T07:30:00Z", "2025-12-31T21:00:00Z", 1),  # backcast
        forecast_row("2026-01-01T07:30:00Z", "2026-01-01T07:00:00Z", 2),  # backcast
    ])
    clean, _ = clean_forecasts(raw, utc("2025-12-30T00:00Z"), utc("2026-01-01T23:59Z"))
    assert len(clean) == 3
    assert list(clean["generation"]) == [1, 2, 3]
    assert (clean["startTime"] <= clean["publishTime"]).sum() == 2


def test_incomplete_run_detection():
    rows = []
    for publish, n in [("2026-01-01T02:30:00Z", 3), ("2026-01-01T04:30:00Z", 3), ("2026-01-01T07:30:00Z", 2)]:
        for h in range(n):
            rows.append(forecast_row(publish, f"2026-01-02T{h:02d}:00:00Z", 100))
    clean, _ = clean_forecasts(pd.DataFrame(rows), utc("2026-01-01T00:00Z"), utc("2026-01-01T23:59Z"))
    run_count, short = incomplete_runs(clean)
    assert run_count == 3
    assert short == [{"publishTime": utc("2026-01-01T07:30:00Z"), "rows": 2}]


def test_data_quality_shape():
    actuals, a_stats = clean_actuals(
        pd.DataFrame([actual_row("2026-01-02T00:00:00Z", "2026-01-02T00:30:00Z", 1, 1)]),
        date(2026, 1, 2), date(2026, 1, 2),
    )
    forecasts, f_stats = clean_forecasts(
        pd.DataFrame([forecast_row("2026-01-01T07:30:00Z", "2026-01-01T08:00:00Z", 1)]),
        utc("2026-01-01T00:00Z"), utc("2026-01-01T23:59Z"),
    )
    dq = build_data_quality(actuals, a_stats, date(2026, 1, 2), date(2026, 1, 2),
                            forecasts, f_stats, utc("2026-01-01T00:00Z"), utc("2026-01-01T23:59Z"))
    assert set(dq) == {"generatedAt", "actuals", "forecasts"}
    assert dq["actuals"]["rows"] == 1
    assert dq["actuals"]["expectedSlots"] == 48
    assert len(dq["actuals"]["missingSlots"]) == 47
    assert dq["actuals"]["missingSlots"][0] == "2026-01-02T00:30:00Z"
    assert dq["forecasts"] == {
        "from": "2026-01-01T00:00:00Z", "to": "2026-01-01T23:59:00Z", "rows": 1, "runs": 1,
        "duplicatesRemoved": 0, "incompleteRuns": [],
    }
