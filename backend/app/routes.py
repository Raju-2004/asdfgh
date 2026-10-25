"""HTTP layer: validates query params, calls the pure domain functions, shapes JSON."""
import math
from datetime import date, datetime, time
from functools import lru_cache

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.app import config
from backend.app.data.loader import load_actuals, load_data_quality, load_forecasts
from backend.app.domain import metrics, selection
from backend.app.schemas import Accuracy, DayAheadForecast, HorizonAccuracy, Meta

router = APIRouter(prefix="/api")

CutoffParam = Query("09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="Decision cutoff on D-1, HH:MM UK time")


# ---------- helpers ----------

def _iso(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(x, ndigits: int = 1) -> float | None:
    """Round for JSON; NaN/None become null."""
    if x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x):
        return None
    return round(float(x), ndigits)


def _round_all(obj, ndigits: int = 1):
    """Round every float inside nested dicts/lists returned by the metrics module."""
    if isinstance(obj, dict):
        return {k: _round_all(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_all(v, ndigits) for v in obj]
    if isinstance(obj, float):
        return _num(obj, ndigits)
    return obj


def _parse_cutoff(cutoff: str) -> time:
    return datetime.strptime(cutoff, "%H:%M").time()


def _check_day(d: date) -> None:
    if not (config.FIRST_DELIVERY_DAY <= d <= config.LAST_DELIVERY_DAY):
        raise HTTPException(
            422, f"date must be between {config.FIRST_DELIVERY_DAY} and {config.LAST_DELIVERY_DAY}"
        )


def _check_range(start: date, end: date) -> None:
    _check_day(start)
    _check_day(end)
    if start > end:
        raise HTTPException(422, "'from' must be on or before 'to'")


@lru_cache(maxsize=32)
def _hourly_actuals() -> pd.DataFrame:
    return metrics.to_hourly_actuals(load_actuals())


@lru_cache(maxsize=32)
def _period_errors(cutoff: str) -> pd.DataFrame:
    """Forecast selected at this cutoff for every delivery day, joined to hourly actuals."""
    selected = selection.select_range_at_cutoff(
        load_forecasts(),
        config.FIRST_DELIVERY_DAY,
        config.LAST_DELIVERY_DAY,
        cutoff_time=_parse_cutoff(cutoff),
        day_offset=config.CUTOFF_DAY_OFFSET,
        tz=config.TZ,
        max_staleness_h=config.MAX_STALENESS_H,
    )
    return metrics.join_errors(selected, _hourly_actuals())


def _errors_between(cutoff: str, start: date, end: date) -> pd.DataFrame:
    df = _period_errors(cutoff)
    return df[(df["deliveryDate"] >= start) & (df["deliveryDate"] <= end)]


# ---------- endpoints ----------

@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/meta", response_model=Meta)
def meta() -> dict:
    quality = load_data_quality()
    days = pd.date_range(config.FIRST_DELIVERY_DAY, config.LAST_DELIVERY_DAY, freq="D")
    return {
        "deliveryDays": [d.strftime("%Y-%m-%d") for d in days],
        "defaults": {
            "cutoffTime": config.DEFAULT_CUTOFF.strftime("%H:%M"),
            "cutoffDayOffset": config.CUTOFF_DAY_OFFSET,
            "tz": config.TZ,
        },
        "runTimesUtc": config.WINDFOR_RUN_TIMES_UTC,
        "dataQuality": {
            "missingActualSlots": len(quality["actuals"]["missingSlots"]),
            "incompleteForecastRuns": len(quality["forecasts"]["incompleteRuns"]),
        },
        "source": "Elexon BMRS: FUELHH (fuelType=WIND) and WINDFOR",
    }


@router.get("/forecast", response_model=DayAheadForecast)
def forecast(date: date = Query(..., description="Delivery day, YYYY-MM-DD"), cutoff: str = CutoffParam) -> dict:
    _check_day(date)
    cutoff_time = _parse_cutoff(cutoff)

    selected = selection.select_at_cutoff(
        load_forecasts(),
        date,
        cutoff_time=cutoff_time,
        day_offset=config.CUTOFF_DAY_OFFSET,
        tz=config.TZ,
        max_staleness_h=config.MAX_STALENESS_H,
    )
    day = metrics.join_errors(selected, _hourly_actuals())

    # Uncertainty band from the whole period's signed errors at this cutoff.
    # error = forecast - actual  =>  actual = forecast - error.
    period = metrics.summary_metrics(_period_errors(cutoff))
    p10, p90 = period["p10"], period["p90"]

    available = day[day["status"] != "missing"]
    start, end = selection.delivery_day_bounds(date, config.TZ)
    actuals = load_actuals()
    half_hourly = actuals[(actuals["startTime"] >= start) & (actuals["startTime"] < end)]

    points = []
    for row in day.itertuples(index=False):
        has_band = p10 is not None and not pd.isna(row.forecastMW)
        points.append({
            "startTime": _iso(row.startTime),
            "forecastMW": _num(row.forecastMW),
            "publishTime": _iso(row.publishTime),
            "horizonH": _num(row.horizonH),
            "status": row.status,
            "actualMW": _num(row.actualMW),
            "errorMW": _num(row.errorMW),
            "bandLowMW": _num(row.forecastMW - p90) if has_band else None,
            "bandHighMW": _num(row.forecastMW - p10) if has_band else None,
        })

    return {
        "deliveryDate": date.isoformat(),
        "cutoffUtc": _iso(selection.cutoff_utc(date, cutoff_time, config.CUTOFF_DAY_OFFSET, config.TZ)),
        "runsUsed": sorted({_iso(t) for t in available["publishTime"]}),
        "summary": {
            # Hourly MW values, so summing them gives MWh.
            "energyMWh": _num(available["forecastMW"].sum()) if len(available) else None,
            "minMW": _num(available["forecastMW"].min()),
            "maxMW": _num(available["forecastMW"].max()),
            "okSlots": int((day["status"] == "ok").sum()),
            "staleSlots": int((day["status"] == "stale").sum()),
            "missingSlots": int((day["status"] == "missing").sum()),
        },
        "errorBand": {"p10MW": _num(p10), "p90MW": _num(p90)},
        "points": points,
        "actualsHalfHourly": [
            {"startTime": _iso(t), "actualMW": _num(g)}
            for t, g in zip(half_hourly["startTime"], half_hourly["generation"])
        ],
    }


@router.get("/accuracy", response_model=Accuracy)
def accuracy(
    from_: date = Query(config.FIRST_DELIVERY_DAY, alias="from"),
    to: date = Query(config.LAST_DELIVERY_DAY),
    cutoff: str = CutoffParam,
) -> Accuracy:
    _check_range(from_, to)
    errors = _errors_between(cutoff, from_, to)
    summary = metrics.summary_metrics(errors)
    return Accuracy(
        from_=from_.isoformat(),
        to=to.isoformat(),
        cutoff=cutoff,
        # wape is a fraction (0.18 = 18%), so it keeps more decimals than the MW values.
        metrics={**_round_all(summary), "wape": _num(summary["wape"], 4)},
        byHour=_round_all(metrics.by_hour(errors, config.TZ)),
        byDay=_round_all(metrics.by_day(errors)),
        byLevel=_round_all(metrics.by_level(errors)),
        histogram=_round_all(metrics.histogram(errors)),
    )


@router.get("/accuracy/horizon", response_model=HorizonAccuracy)
def accuracy_by_horizon(
    from_: date = Query(config.FIRST_DELIVERY_DAY, alias="from"),
    to: date = Query(config.LAST_DELIVERY_DAY),
    cutoff: str = CutoffParam,
) -> dict:
    _check_range(from_, to)
    start, _ = selection.delivery_day_bounds(from_, config.TZ)
    _, end = selection.delivery_day_bounds(to, config.TZ)
    buckets = metrics.by_horizon(load_forecasts(), _hourly_actuals(), start, end)

    used = _errors_between(cutoff, from_, to)["horizonH"].dropna()
    return {
        "buckets": _round_all(buckets),
        "cutoffHorizonH": {
            "min": _num(used.min()) if len(used) else None,
            "max": _num(used.max()) if len(used) else None,
        },
    }
