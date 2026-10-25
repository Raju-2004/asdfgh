"""Pick the forecast a day-ahead desk could actually have seen at decision time.

The desk buys power for delivery day D on the day before (D-1), at a cutoff such as
09:00 London time. The whole point of this module is to avoid *lookahead*: we may only
use forecast runs that were already published at the cutoff. Using a later run would
make the forecast look better than anything the desk could really have acted on.

Data reminder (see docs/CONTRACT.md section 1):
- forecasts: one row per (publishTime, startTime), hourly startTimes, ~8 runs a day.
  Each run also contains "backcast" rows (startTime <= publishTime) describing the past;
  those are not forecasts and are always dropped here.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HOUR = pd.Timedelta(hours=1)

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"

SELECTED_COLUMNS = ["startTime", "publishTime", "forecastMW", "horizonH", "status"]
HORIZON_COLUMNS = ["startTime", "publishTime", "forecastMW", "horizonH"]


# --------------------------------------------------------------------------- time helpers


def _local_to_utc(local_date: date, local_time: time, tz: str) -> pd.Timestamp:
    """Interpret a wall-clock date+time in `tz` and return it as a UTC Timestamp.

    We go through `zoneinfo` (not a fixed offset) so that GMT/BST is handled correctly:
    09:00 London is 09:00Z in winter but 08:00Z in summer.
    """
    local = datetime.combine(local_date, local_time, tzinfo=ZoneInfo(tz))
    return pd.Timestamp(local).tz_convert("UTC")


def cutoff_utc(
    delivery_date: date,
    cutoff_time: time = time(9, 0),
    day_offset: int = 1,
    tz: str = "Europe/London",
) -> pd.Timestamp:
    """The moment (UTC) the desk must decide for `delivery_date`.

    Default: 09:00 local time on the day before delivery (D-1).
    Example: delivery 2026-01-15 -> 2026-01-14T09:00Z.
    """
    decision_day = delivery_date - timedelta(days=day_offset)
    return _local_to_utc(decision_day, cutoff_time, tz)


def delivery_day_bounds(
    delivery_date: date, tz: str = "Europe/London"
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Half-open UTC interval [start, end) covering the *local* delivery day.

    Using local midnights means the day is 23 hours long when clocks go forward
    (last Sunday of March) and 25 hours when they go back (last Sunday of October).
    """
    start = _local_to_utc(delivery_date, time(0, 0), tz)
    end = _local_to_utc(delivery_date + timedelta(days=1), time(0, 0), tz)
    return start, end


def expected_slots(delivery_date: date, tz: str = "Europe/London") -> pd.DatetimeIndex:
    """Every hourly startTime (UTC) the delivery day should have: 24, or 23/25 on DST days."""
    start, end = delivery_day_bounds(delivery_date, tz)
    return pd.date_range(start, end, freq="h", inclusive="left", name="startTime")


# --------------------------------------------------------------------------- core rules


def _drop_backcasts(forecasts: pd.DataFrame) -> pd.DataFrame:
    """Keep only genuine forecasts: rows whose target time is after the run was published."""
    return forecasts[forecasts["startTime"] > forecasts["publishTime"]]


def _latest_per_start_time(candidates: pd.DataFrame) -> pd.DataFrame:
    """For each startTime keep the row from the most recent run (latest publishTime wins).

    Sorting by publishTime then keeping the last row per startTime is the pandas
    equivalent of "ORDER BY publishTime; GROUP BY startTime; pick the last one".
    (We avoid `groupby().last()` because it skips NaN cells column by column and could
    silently mix values from two different runs.)
    """
    ordered = candidates.sort_values(["startTime", "publishTime"])
    return ordered.drop_duplicates("startTime", keep="last").reset_index(drop=True)


def _horizon_hours(start_time: pd.Series, publish_time: pd.Series) -> pd.Series:
    """Lead time in hours between publishing a forecast and the hour it is about."""
    return (start_time - publish_time) / HOUR


def select_at_cutoff(
    forecasts: pd.DataFrame,
    delivery_date: date,
    cutoff_time: time = time(9, 0),
    day_offset: int = 1,
    tz: str = "Europe/London",
    max_staleness_h: float = 24,
) -> pd.DataFrame:
    """The forecast the desk would have used for each hour of `delivery_date`.

    Rules (all needed to avoid lookahead and nonsense rows):
    1. publishTime <= cutoff        -> the run existed when the decision was made.
    2. startTime > publishTime      -> drop backcast rows (they describe the past).
    3. startTime inside the local delivery day.
    4. latest publishTime wins per startTime -> the freshest information available.

    Every expected hourly slot gets exactly one row. Status:
    - "missing": no run before the cutoff covers that hour (forecastMW/publishTime/horizonH empty)
    - "stale":   the run used was published more than `max_staleness_h` hours before the cutoff
                 (e.g. several runs failed to publish, so we fell back to an old one)
    - "ok":      otherwise
    """
    cutoff = cutoff_utc(delivery_date, cutoff_time, day_offset, tz)
    day_start, day_end = delivery_day_bounds(delivery_date, tz)

    candidates = _drop_backcasts(forecasts)
    candidates = candidates[
        (candidates["publishTime"] <= cutoff)
        & (candidates["startTime"] >= day_start)
        & (candidates["startTime"] < day_end)
    ]
    chosen = _latest_per_start_time(candidates[["startTime", "publishTime", "generation"]])
    chosen = chosen.rename(columns={"generation": "forecastMW"})

    # Left-join onto the full slot grid so hours with no forecast show up as "missing".
    slots = pd.DataFrame({"startTime": expected_slots(delivery_date, tz)})
    result = slots.merge(chosen, on="startTime", how="left")

    # Make dtypes predictable even when nothing matched (empty merges lose them).
    result["publishTime"] = pd.to_datetime(result["publishTime"], utc=True)
    result["forecastMW"] = result["forecastMW"].astype(float)
    result["horizonH"] = _horizon_hours(result["startTime"], result["publishTime"]).astype(float)

    age_h = (cutoff - result["publishTime"]) / HOUR
    result["status"] = np.select(
        [result["publishTime"].isna(), age_h > max_staleness_h],
        [STATUS_MISSING, STATUS_STALE],
        default=STATUS_OK,
    )
    return result[SELECTED_COLUMNS].reset_index(drop=True)


def select_range_at_cutoff(
    forecasts: pd.DataFrame,
    start_date: date,
    end_date: date,
    cutoff_time: time = time(9, 0),
    day_offset: int = 1,
    tz: str = "Europe/London",
    max_staleness_h: float = 24,
) -> pd.DataFrame:
    """`select_at_cutoff` for every day in [start_date, end_date] (inclusive), stacked.

    Adds a `deliveryDate` column (python `date`). Each day gets its own cutoff, so this is
    deliberately a simple loop rather than one big vectorised selection.
    """
    frames = []
    day = start_date
    while day <= end_date:
        selected = select_at_cutoff(
            forecasts, day, cutoff_time, day_offset, tz, max_staleness_h
        )
        selected["deliveryDate"] = day
        frames.append(selected)
        day += timedelta(days=1)

    if not frames:
        return pd.DataFrame(columns=SELECTED_COLUMNS + ["deliveryDate"])
    return pd.concat(frames, ignore_index=True)


def select_by_horizon(
    forecasts: pd.DataFrame,
    min_horizon_h: float,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
) -> pd.DataFrame:
    """Per startTime in [start_utc, end_utc): the latest forecast made at least `min_horizon_h` ahead.

    This is the "fixed lead time" view (e.g. "what did we think 24 h before?") as opposed to
    the "fixed decision time" view of `select_at_cutoff`. Only startTimes that have a
    qualifying forecast are returned (no slot grid, no status).
    """
    start_utc, end_utc = pd.Timestamp(start_utc), pd.Timestamp(end_utc)
    candidates = _drop_backcasts(forecasts)
    candidates = candidates[
        (candidates["startTime"] >= start_utc) & (candidates["startTime"] < end_utc)
    ].copy()
    candidates["horizonH"] = _horizon_hours(candidates["startTime"], candidates["publishTime"])
    candidates = candidates[candidates["horizonH"] >= min_horizon_h]

    chosen = _latest_per_start_time(
        candidates[["startTime", "publishTime", "generation", "horizonH"]]
    )
    chosen = chosen.rename(columns={"generation": "forecastMW"})
    chosen["forecastMW"] = chosen["forecastMW"].astype(float)
    chosen["horizonH"] = chosen["horizonH"].astype(float)
    return chosen[HORIZON_COLUMNS].reset_index(drop=True)
