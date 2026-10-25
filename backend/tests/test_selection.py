"""Tests for the no-lookahead forecast selection rules (backend/app/domain/selection.py)."""

from datetime import date, time

import pandas as pd
import pytest

from backend.app.domain.selection import (
    cutoff_utc,
    delivery_day_bounds,
    select_at_cutoff,
    select_by_horizon,
    select_range_at_cutoff,
)

UTC = "UTC"


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz=UTC)


def run(publish: str, first_start: str, hours: int, mw: float) -> pd.DataFrame:
    """One forecast run: `hours` hourly rows starting at `first_start`, all with value `mw`."""
    starts = pd.date_range(ts(first_start), periods=hours, freq="h")
    return pd.DataFrame({"publishTime": ts(publish), "startTime": starts, "generation": float(mw)})


def forecasts(*runs: pd.DataFrame) -> pd.DataFrame:
    return pd.concat(runs, ignore_index=True)


D = date(2026, 1, 15)  # winter day: local == UTC


# --------------------------------------------------------------------------- cutoff / bounds


def test_cutoff_winter_example():
    assert cutoff_utc(D) == ts("2026-01-14T09:00")


def test_cutoff_summer_is_one_hour_earlier_in_utc():
    assert cutoff_utc(date(2026, 7, 1)) == ts("2026-06-30T08:00")


def test_cutoff_custom_time_and_offset():
    assert cutoff_utc(D, time(16, 30), day_offset=2) == ts("2026-01-13T16:30")


def test_cutoff_on_dst_change_days():
    # Delivery Mon 30 Mar: cutoff is Sun 29 Mar 09:00 BST (clocks went forward at 01:00 UTC).
    assert cutoff_utc(date(2026, 3, 30)) == ts("2026-03-29T08:00")
    # Delivery Sun 29 Mar: cutoff is Sat 28 Mar 09:00 GMT.
    assert cutoff_utc(date(2026, 3, 29)) == ts("2026-03-28T09:00")
    # Delivery Mon 26 Oct: cutoff is Sun 25 Oct 09:00 GMT (clocks went back at 01:00 UTC).
    assert cutoff_utc(date(2026, 10, 26)) == ts("2026-10-25T09:00")
    # Delivery Sun 25 Oct: cutoff is Sat 24 Oct 09:00 BST.
    assert cutoff_utc(date(2026, 10, 25)) == ts("2026-10-24T08:00")


@pytest.mark.parametrize(
    "day, start, end, hours",
    [
        (date(2026, 1, 15), "2026-01-15T00:00", "2026-01-16T00:00", 24),
        (date(2026, 3, 29), "2026-03-29T00:00", "2026-03-29T23:00", 23),
        (date(2026, 10, 25), "2026-10-24T23:00", "2026-10-26T00:00", 25),
        (date(2026, 7, 1), "2026-06-30T23:00", "2026-07-01T23:00", 24),
    ],
)
def test_delivery_day_bounds_and_slot_counts(day, start, end, hours):
    assert delivery_day_bounds(day) == (ts(start), ts(end))
    # Plenty of coverage, published well before the cutoff.
    fc = run("2026-01-01T00:00", "2026-01-01T01:00", 24 * 400, 100)
    fc = fc[fc["startTime"] < ts("2026-12-31")]
    fc["publishTime"] = fc["startTime"] - pd.Timedelta(hours=40)
    result = select_at_cutoff(fc, day, max_staleness_h=1e9)
    assert len(result) == hours
    assert result["startTime"].iloc[0] == ts(start)
    assert result["startTime"].iloc[-1] == ts(end) - pd.Timedelta(hours=1)


# --------------------------------------------------------------------------- selection rules


def test_example_from_spec_uses_0730_run_and_horizon_16_5():
    fc = forecasts(
        run("2026-01-14T04:30", "2026-01-14T05:00", 48, 100),
        run("2026-01-14T07:30", "2026-01-14T08:00", 48, 200),
        run("2026-01-14T09:30", "2026-01-14T10:00", 48, 999),  # after cutoff
    )
    result = select_at_cutoff(fc, D)
    assert list(result.columns) == ["startTime", "publishTime", "forecastMW", "horizonH", "status"]
    assert len(result) == 24
    assert (result["publishTime"] == ts("2026-01-14T07:30")).all()
    assert (result["forecastMW"] == 200).all()
    assert result["horizonH"].iloc[0] == 16.5
    assert result["horizonH"].iloc[-1] == 39.5
    assert (result["status"] == "ok").all()


def test_run_published_after_cutoff_is_excluded():
    fc = forecasts(
        run("2026-01-14T07:30", "2026-01-14T08:00", 48, 200),
        run("2026-01-14T09:00:01", "2026-01-14T10:00", 48, 999),
    )
    assert (select_at_cutoff(fc, D)["forecastMW"] == 200).all()


def test_run_published_exactly_at_cutoff_is_allowed():
    fc = forecasts(
        run("2026-01-14T07:30", "2026-01-14T08:00", 48, 200),
        run("2026-01-14T09:00", "2026-01-14T10:00", 48, 300),
    )
    assert (select_at_cutoff(fc, D)["forecastMW"] == 300).all()


def test_latest_run_before_cutoff_wins_per_start_time():
    # Latest run only covers the first 10 hours of D; the rest comes from the older run.
    fc = forecasts(
        run("2026-01-14T07:30", "2026-01-15T00:00", 10, 200),
        run("2026-01-14T02:30", "2026-01-14T03:00", 48, 100),
    )
    result = select_at_cutoff(fc, D)
    assert (result["forecastMW"].iloc[:10] == 200).all()
    assert (result["forecastMW"].iloc[10:] == 100).all()
    assert (result["publishTime"].iloc[10:] == ts("2026-01-14T02:30")).all()


def test_backcast_rows_are_ignored():
    # A run published *inside* the delivery day: its rows for earlier hours are backcasts.
    # With a cutoff on D itself (day_offset=0, 12:00) the 11:30 run is allowed, but only
    # for startTime > 11:30; hours 00:00..11:00 must come from the earlier run.
    fc = forecasts(
        run("2026-01-14T22:30", "2026-01-14T23:00", 30, 100),
        run("2026-01-15T11:30", "2026-01-15T00:00", 24, 777),  # 00:00..11:00 are backcasts
    )
    result = select_at_cutoff(fc, D, cutoff_time=time(12, 0), day_offset=0)
    before = result["startTime"] < ts("2026-01-15T11:30")
    assert (result.loc[before, "forecastMW"] == 100).all()
    assert (result.loc[~before, "forecastMW"] == 777).all()
    assert (result["horizonH"] > 0).all()


def test_backcast_only_run_gives_missing():
    fc = run("2026-01-14T07:30", "2026-01-14T00:00", 7, 100)  # all startTime <= publishTime
    assert (select_at_cutoff(fc, date(2026, 1, 14), day_offset=0, cutoff_time=time(23, 0))
            ["status"] == "missing").all()


def test_missing_runs_fall_back_to_older_run_flagged_stale():
    # Runs on D-1 all failed; the last available run is D-2 07:30 -> 25.5 h old at cutoff.
    fc = run("2026-01-13T07:30", "2026-01-13T08:00", 72, 150)
    result = select_at_cutoff(fc, D)
    assert (result["forecastMW"] == 150).all()
    assert (result["status"] == "stale").all()
    # Same data with a looser threshold is ok.
    assert (select_at_cutoff(fc, D, max_staleness_h=26)["status"] == "ok").all()


def test_staleness_boundary_is_strictly_greater_than():
    fc = run("2026-01-13T09:00", "2026-01-13T10:00", 72, 150)  # exactly 24 h before cutoff
    assert (select_at_cutoff(fc, D, max_staleness_h=24)["status"] == "ok").all()


def test_no_candidates_gives_missing_rows():
    fc = run("2026-01-14T09:30", "2026-01-14T10:00", 48, 999)  # only after cutoff
    result = select_at_cutoff(fc, D)
    assert len(result) == 24
    assert (result["status"] == "missing").all()
    assert result["forecastMW"].isna().all()
    assert result["publishTime"].isna().all()
    assert result["horizonH"].isna().all()


def test_partial_coverage_marks_only_uncovered_hours_missing():
    fc = run("2026-01-14T07:30", "2026-01-14T08:00", 20, 200)  # ends at 2026-01-15T03:00
    result = select_at_cutoff(fc, D)
    assert list(result["status"].iloc[:4]) == ["ok"] * 4
    assert (result["status"].iloc[4:] == "missing").all()


def test_empty_forecasts_frame():
    empty = pd.DataFrame(
        {
            "publishTime": pd.Series(dtype="datetime64[ns, UTC]"),
            "startTime": pd.Series(dtype="datetime64[ns, UTC]"),
            "generation": pd.Series(dtype=float),
        }
    )
    result = select_at_cutoff(empty, D)
    assert len(result) == 24 and (result["status"] == "missing").all()


def test_dst_short_day_selection_and_horizon():
    # 2026-03-29: cutoff 2026-03-28T09:00Z (GMT). 07:30 run on the 28th covers everything.
    fc = run("2026-03-28T07:30", "2026-03-28T08:00", 48, 100)
    result = select_at_cutoff(fc, date(2026, 3, 29))
    assert len(result) == 23
    assert result["horizonH"].iloc[0] == 16.5  # 00:00 local = 00:00Z
    assert (result["status"] == "ok").all()


def test_dst_long_day_selection_and_horizon():
    # 2026-10-25: day starts 2026-10-24T23:00Z; cutoff 2026-10-24T08:00Z (09:00 BST),
    # so the 07:30 UTC run is the latest usable one.
    fc = forecasts(
        run("2026-10-24T07:30", "2026-10-24T08:00", 48, 100),
        run("2026-10-24T09:30", "2026-10-24T10:00", 48, 999),
    )
    result = select_at_cutoff(fc, date(2026, 10, 25))
    assert len(result) == 25
    assert (result["forecastMW"] == 100).all()
    assert result["horizonH"].iloc[0] == 15.5


# --------------------------------------------------------------------------- range / horizon


def test_select_range_at_cutoff_adds_delivery_date():
    fc = run("2026-01-13T07:30", "2026-01-13T08:00", 24 * 5, 100)
    fc["publishTime"] = fc["startTime"] - pd.Timedelta(hours=40)
    result = select_range_at_cutoff(fc, date(2026, 1, 15), date(2026, 1, 17))
    assert len(result) == 72
    assert sorted(set(result["deliveryDate"])) == [date(2026, 1, 15), date(2026, 1, 16), date(2026, 1, 17)]
    assert "status" in result.columns


def test_select_range_empty_when_end_before_start():
    fc = run("2026-01-13T07:30", "2026-01-13T08:00", 24, 100)
    assert select_range_at_cutoff(fc, date(2026, 1, 17), date(2026, 1, 15)).empty


def test_select_by_horizon_picks_latest_with_enough_lead():
    fc = forecasts(
        run("2026-01-14T02:30", "2026-01-15T00:00", 2, 100),  # leads 21.5, 22.5
        run("2026-01-14T07:30", "2026-01-15T00:00", 2, 200),  # leads 16.5, 17.5
        run("2026-01-14T22:30", "2026-01-14T20:00", 6, 300),  # backcasts + leads 1.5..2.5
    )
    result = select_by_horizon(fc, 12, ts("2026-01-15T00:00"), ts("2026-01-15T02:00"))
    assert list(result.columns) == ["startTime", "publishTime", "forecastMW", "horizonH"]
    assert list(result["forecastMW"]) == [200, 200]
    result = select_by_horizon(fc, 20, ts("2026-01-15T00:00"), ts("2026-01-15T02:00"))
    assert list(result["forecastMW"]) == [100, 100]
    assert list(result["horizonH"]) == [21.5, 22.5]
    result = select_by_horizon(fc, 0, ts("2026-01-15T00:00"), ts("2026-01-15T02:00"))
    assert list(result["forecastMW"]) == [300, 300]


def test_select_by_horizon_end_is_exclusive():
    fc = run("2026-01-14T07:30", "2026-01-15T00:00", 5, 200)
    result = select_by_horizon(fc, 0, ts("2026-01-15T00:00"), ts("2026-01-15T03:00"))
    assert len(result) == 3
