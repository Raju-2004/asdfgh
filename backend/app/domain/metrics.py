"""Forecast-error metrics: forecasts vs actual wind generation.

Convention everywhere: error = forecast - actual (MW). Positive error = over-forecast.

All public functions return plain Python numbers / None (never numpy scalars or NaN), so the
API layer can hand the result straight to JSON. Rows whose error is NaN (no forecast or no
actual for that hour) are dropped *explicitly* before any statistic is computed.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from backend.app.domain.selection import HOUR

PERCENTILES = {"p5": 5, "p10": 10, "p50": 50, "p90": 90, "p95": 95}
LEVEL_NAMES = ["low", "medium", "high"]


# --------------------------------------------------------------------------- small helpers


def _num(value: Any) -> float | None:
    """numpy/pandas number -> Python float, with NaN/None -> None (JSON has no NaN)."""
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


def _valid(errors: pd.DataFrame) -> pd.DataFrame:
    """Only the rows we can score: both a forecast and an actual, hence a numeric error."""
    return errors[errors["errorMW"].notna()]


def _percentile(values: np.ndarray, q: float) -> float | None:
    """Linear-interpolation percentile (numpy default); None for an empty array."""
    if len(values) == 0:
        return None
    return float(np.percentile(values, q, method="linear"))


def _mae(e: np.ndarray) -> float | None:
    return float(np.mean(np.abs(e))) if len(e) else None


def _bias(e: np.ndarray) -> float | None:
    return float(np.mean(e)) if len(e) else None


def _rmse(e: np.ndarray) -> float | None:
    return float(np.sqrt(np.mean(e**2))) if len(e) else None


# --------------------------------------------------------------------------- preparing data


def to_hourly_actuals(actuals: pd.DataFrame) -> pd.DataFrame:
    """Turn half-hourly actuals into hourly ones to match the hourly forecasts.

    The hourly value for HH:00 is the mean of the HH:00 and HH:30 half-hours (MW is a rate,
    so averaging is right; summing would double it). If either half is missing the hour is
    NaN: half an hour of data is not a fair stand-in for the full hour.
    """
    df = actuals[["startTime", "generation"]].dropna(subset=["generation"])
    df = df.drop_duplicates("startTime", keep="last").copy()
    df["hour"] = df["startTime"].dt.floor("h")

    grouped = df.groupby("hour")["generation"].agg(["count", "mean"])
    hourly = pd.DataFrame(
        {
            "startTime": grouped.index,
            "actualMW": grouped["mean"].where(grouped["count"] == 2).astype(float).values,
        }
    )
    return hourly.reset_index(drop=True)


def join_errors(selected: pd.DataFrame, hourly_actuals: pd.DataFrame) -> pd.DataFrame:
    """Attach `actualMW` and `errorMW` (= forecast - actual) to each selected forecast row.

    Left join: every forecast slot is kept, even without an actual (errorMW then NaN).
    """
    joined = selected.merge(
        hourly_actuals[["startTime", "actualMW"]], on="startTime", how="left"
    )
    joined["actualMW"] = joined["actualMW"].astype(float)
    joined["errorMW"] = joined["forecastMW"].astype(float) - joined["actualMW"]
    return joined


# --------------------------------------------------------------------------- headline numbers


def summary_metrics(errors: pd.DataFrame) -> dict:
    """Headline accuracy numbers over all scorable rows.

    - mae:  mean |error|            -> typical size of a miss, in MW
    - rmse: sqrt(mean error^2)       -> like MAE but punishes big misses more
    - bias: mean error               -> systematic over (+) / under (-) forecasting
    - wape: sum|error| / sum(actual) -> MAE relative to output; None if sum(actual) == 0
    - pN:   percentiles of the *signed* error (used for the uncertainty band)
    - maxAbs: worst single miss
    """
    valid = _valid(errors)
    e = valid["errorMW"].to_numpy(dtype=float)
    n = len(e)
    keys = ["mae", "rmse", "bias", "wape", *PERCENTILES, "maxAbs"]
    if n == 0:
        return {"n": 0, **{k: None for k in keys}}

    abs_e = np.abs(e)
    total_actual = float(valid["actualMW"].sum())
    return {
        "n": n,
        "mae": _mae(e),
        "rmse": _rmse(e),
        "bias": _bias(e),
        "wape": float(abs_e.sum() / total_actual) if total_actual != 0 else None,
        **{name: _percentile(e, q) for name, q in PERCENTILES.items()},
        "maxAbs": float(abs_e.max()),
    }


# --------------------------------------------------------------------------- breakdowns


def by_hour(errors: pd.DataFrame, tz: str = "Europe/London") -> list[dict]:
    """Error by local hour of day (0-23). Always 24 entries; empty hours have n=0 and None stats.

    Local time (not UTC) because that is how the desk thinks about the day's shape.
    """
    valid = _valid(errors)
    local_hour = valid["startTime"].dt.tz_convert(tz).dt.hour
    result = []
    for hour in range(24):
        e = valid.loc[local_hour == hour, "errorMW"].to_numpy(dtype=float)
        result.append(
            {
                "hour": hour,
                "n": len(e),
                "mae": _mae(e),
                "bias": _bias(e),
                "p10": _percentile(e, 10),
                "p90": _percentile(e, 90),
            }
        )
    return result


def by_day(errors: pd.DataFrame) -> list[dict]:
    """Error per delivery day (needs the `deliveryDate` column from `select_range_at_cutoff`).

    Every day present in the input is listed, sorted; days with nothing scorable get n=0.
    """
    result = []
    for day, group in errors.groupby("deliveryDate", sort=True):
        e = _valid(group)["errorMW"].to_numpy(dtype=float)
        result.append(
            {
                "date": pd.Timestamp(day).strftime("%Y-%m-%d"),
                "n": len(e),
                "mae": _mae(e),
                "bias": _bias(e),
            }
        )
    return result


def by_level(errors: pd.DataFrame) -> list[dict]:
    """Error split by how windy it actually was: low / medium / high thirds of actual output.

    Rows are sorted by actualMW and cut into three groups as equal in size as possible
    (`np.array_split`). We split by row count rather than value quantiles so ties or tiny
    samples never produce empty or overlapping bins. Always returns 3 entries.
    """
    valid = _valid(errors).sort_values("actualMW")
    chunks = np.array_split(np.arange(len(valid)), 3)
    result = []
    for name, positions in zip(LEVEL_NAMES, chunks):
        group = valid.iloc[positions]
        e = group["errorMW"].to_numpy(dtype=float)
        result.append(
            {
                "level": name,
                "minMW": _num(group["actualMW"].min()) if len(group) else None,
                "maxMW": _num(group["actualMW"].max()) if len(group) else None,
                "n": len(e),
                "mae": _mae(e),
                "bias": _bias(e),
            }
        )
    return result


def histogram(errors: pd.DataFrame, bin_mw: float = 500) -> list[dict]:
    """Count of errors per fixed-width bin. Bin k covers [k*bin_mw, (k+1)*bin_mw).

    Bins are contiguous from the lowest to the highest populated bin (zero counts included)
    so the chart has no gaps.
    """
    e = _valid(errors)["errorMW"].to_numpy(dtype=float)
    if len(e) == 0:
        return []
    bin_index = np.floor(e / bin_mw).astype(int)
    counts = pd.Series(bin_index).value_counts()
    return [
        {"binStartMW": _num(k * bin_mw), "count": int(counts.get(k, 0))}
        for k in range(int(bin_index.min()), int(bin_index.max()) + 1)
    ]


def by_horizon(
    forecasts: pd.DataFrame,
    hourly_actuals: pd.DataFrame,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    bucket_h: float = 6,
    max_h: float = 48,
) -> list[dict]:
    """How accuracy decays with lead time, using *every* forecast run (not just the cutoff one).

    Each non-backcast forecast row with startTime in [start_utc, end_utc) is scored and put in
    a lead-time bucket [minH, maxH) of width `bucket_h`, up to `max_h`. Leads >= max_h are
    dropped. Always returns every bucket (empty ones have n=0 and None stats).
    """
    start_utc, end_utc = pd.Timestamp(start_utc), pd.Timestamp(end_utc)
    df = forecasts[
        (forecasts["startTime"] > forecasts["publishTime"])  # drop backcasts
        & (forecasts["startTime"] >= start_utc)
        & (forecasts["startTime"] < end_utc)
    ].copy()
    df["horizonH"] = (df["startTime"] - df["publishTime"]) / HOUR
    df = df.rename(columns={"generation": "forecastMW"})
    scored = _valid(join_errors(df, hourly_actuals))

    result = []
    n_buckets = int(math.ceil(max_h / bucket_h))
    for i in range(n_buckets):
        lo, hi = i * bucket_h, min((i + 1) * bucket_h, max_h)
        in_bucket = (scored["horizonH"] >= lo) & (scored["horizonH"] < hi)
        e = scored.loc[in_bucket, "errorMW"].to_numpy(dtype=float)
        result.append(
            {
                "bucket": f"{lo:g}-{hi:g}h",
                "minH": _num(lo),
                "maxH": _num(hi),
                "n": len(e),
                "mae": _mae(e),
                "rmse": _rmse(e),
                "bias": _bias(e),
            }
        )
    return result
