# Internal contracts (data files, domain functions, HTTP API)

All timestamps are UTC ISO-8601. Inside Python they are tz-aware `pandas.Timestamp` (UTC).
Error convention everywhere: `error = forecast − actual` (MW). Positive = over-forecast.

## 1. Data files (written by `backend/ingest/fetch_elexon.py`, read by `backend/app/data/loader.py`)

`data/actuals_wind.csv` — FUELHH, fuelType=WIND, deduped (latest publishTime per startTime), sorted by startTime. Gaps are NOT filled.
```
startTime,publishTime,settlementDate,settlementPeriod,generation
2026-01-02T00:00:00Z,2026-01-02T00:30:00Z,2026-01-02,1,11309
```
`data/forecasts_wind.csv` — WINDFOR, deduped on (publishTime, startTime), sorted by (publishTime, startTime). Backcast rows (startTime <= publishTime) are KEPT; selection drops them.
```
publishTime,startTime,generation
2026-01-01T07:30:00Z,2026-01-01T08:00:00Z,12443
```
`data/data_quality.json`
```json
{
  "generatedAt": "...",
  "actuals":   {"from": "...", "to": "...", "rows": 0, "expectedSlots": 0, "duplicatesRemoved": 0, "missingSlots": ["iso", "..."]},
  "forecasts": {"from": "...", "to": "...", "rows": 0, "runs": 0, "duplicatesRemoved": 0,
                "incompleteRuns": [{"publishTime": "iso", "rows": 0}]}
}
```
Ranges: actuals settlementDate 2026-01-02 → 2026-02-01; forecasts publishTime 2025-12-30T00:00Z → 2026-02-01T23:59Z.

`loader.py` exposes `load_actuals()` and `load_forecasts()` → DataFrames with the columns above,
`startTime`/`publishTime` parsed to UTC, `generation` as float. `load_data_quality()` → dict.

## 2. Domain functions (pure, take DataFrames; no file or HTTP access)

`backend/app/domain/selection.py`
- `cutoff_utc(delivery_date: date, cutoff_time: time = time(9, 0), day_offset: int = 1, tz: str = "Europe/London") -> pd.Timestamp`
- `delivery_day_bounds(delivery_date: date, tz: str = "Europe/London") -> tuple[pd.Timestamp, pd.Timestamp]` (half-open, UTC)
- `select_at_cutoff(forecasts, delivery_date, cutoff_time=time(9,0), day_offset=1, tz="Europe/London", max_staleness_h=24) -> DataFrame`
  one row per expected hourly slot of the delivery day (24; 23/25 on DST days), columns:
  `startTime, publishTime, forecastMW, horizonH, status` with status in `ok | stale | missing`
  (missing → forecastMW NaN, publishTime NaT, horizonH NaN).
  Rules: publishTime <= cutoff, startTime > publishTime, startTime in delivery day, latest publishTime wins per startTime.
- `select_range_at_cutoff(forecasts, start_date, end_date, **same kwargs) -> DataFrame` — inclusive day range, adds `deliveryDate` (date).
- `select_by_horizon(forecasts, min_horizon_h: float, start_utc, end_utc) -> DataFrame` — per startTime in [start, end), latest forecast with horizonH >= min_horizon_h. Columns `startTime, publishTime, forecastMW, horizonH`.

`backend/app/domain/metrics.py`
- `to_hourly_actuals(actuals) -> DataFrame` columns `startTime, actualMW`: mean of HH:00 and HH:30; NaN unless both halves exist.
- `join_errors(selected, hourly_actuals) -> DataFrame` adds `actualMW, errorMW` (left join on startTime).
- `summary_metrics(errors) -> dict` keys `n, mae, rmse, bias, wape, p5, p10, p50, p90, p95, maxAbs` (rows with NaN errorMW ignored; wape = Σ|e|/Σactual, None if Σactual == 0; all None if n == 0).
- `by_hour(errors, tz="Europe/London") -> list[dict]` `{hour, n, mae, bias, p10, p90}` (hour 0–23, local).
- `by_day(errors) -> list[dict]` `{date: "YYYY-MM-DD", n, mae, bias}` (uses `deliveryDate` column).
- `by_level(errors) -> list[dict]` actual-generation terciles `{level: "low"|"medium"|"high", minMW, maxMW, n, mae, bias}`.
- `histogram(errors, bin_mw=500) -> list[dict]` `{binStartMW, count}`.
- `by_horizon(forecasts, hourly_actuals, start_utc, end_utc, bucket_h=6, max_h=48) -> list[dict]`
  all non-backcast forecasts with startTime in range; `{bucket: "0-6h", minH, maxH, n, mae, rmse, bias}`.

## 3. HTTP API (FastAPI, `backend/app/routes.py`) — the frontend codes against this

Query `cutoff` is `HH:MM` (default `09:00`, on D-1, Europe/London). Numbers in MW rounded to 1 dp. Bad input → 422.

`GET /api/health` → `{"status":"ok"}`

`GET /api/meta`
```json
{"deliveryDays": ["2026-01-02", "...", "2026-02-01"],
 "defaults": {"cutoffTime": "09:00", "cutoffDayOffset": 1, "tz": "Europe/London"},
 "runTimesUtc": ["02:30","04:30","07:30","09:30","11:30","15:30","18:30","22:30"],
 "dataQuality": {"missingActualSlots": 0, "incompleteForecastRuns": 0},
 "source": "Elexon BMRS: FUELHH (fuelType=WIND) and WINDFOR"}
```

`GET /api/forecast?date=2026-01-15&cutoff=09:00`
```json
{"deliveryDate": "2026-01-15", "cutoffUtc": "2026-01-14T09:00:00Z",
 "runsUsed": ["2026-01-14T07:30:00Z"],
 "summary": {"energyMWh": 0, "minMW": 0, "maxMW": 0, "okSlots": 24, "staleSlots": 0, "missingSlots": 0},
 "errorBand": {"p10MW": -900.0, "p90MW": 1200.0},
 "points": [{"startTime": "2026-01-15T00:00:00Z", "forecastMW": 0, "publishTime": "...", "horizonH": 16.5,
             "status": "ok", "actualMW": 0, "errorMW": 0, "bandLowMW": 0, "bandHighMW": 0}],
 "actualsHalfHourly": [{"startTime": "2026-01-15T00:00:00Z", "actualMW": 0}]}
```
`errorBand` = signed-error P10/P90 over the whole data period at this cutoff. Since actual = forecast − error:
`bandLowMW = forecastMW − p90MW`, `bandHighMW = forecastMW − p10MW` (an 80% range for the actual outcome).
Nullable: `forecastMW, publishTime, horizonH, actualMW, errorMW, bandLowMW, bandHighMW`.

`GET /api/accuracy?from=2026-01-02&to=2026-02-01&cutoff=09:00`
```json
{"from": "...", "to": "...", "cutoff": "09:00",
 "metrics": {"n": 0, "mae": 0, "rmse": 0, "bias": 0, "wape": 0.0, "p5": 0, "p10": 0, "p50": 0, "p90": 0, "p95": 0, "maxAbs": 0},
 "byHour": [{"hour": 0, "n": 0, "mae": 0, "bias": 0, "p10": 0, "p90": 0}],
 "byDay": [{"date": "2026-01-02", "n": 0, "mae": 0, "bias": 0}],
 "byLevel": [{"level": "low", "minMW": 0, "maxMW": 0, "n": 0, "mae": 0, "bias": 0}],
 "histogram": [{"binStartMW": -3000, "count": 0}]}
```
`wape` is a fraction (0.12 = 12%).

`GET /api/accuracy/horizon?from=2026-01-02&to=2026-02-01&cutoff=09:00`
```json
{"buckets": [{"bucket": "0-6h", "minH": 0, "maxH": 6, "n": 0, "mae": 0, "rmse": 0, "bias": 0}],
 "cutoffHorizonH": {"min": 16.5, "max": 39.5}}
```
`cutoffHorizonH` = lead-time range of the forecasts actually selected at this cutoff (to highlight on the chart).
