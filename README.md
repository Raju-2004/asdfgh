# Wind Forecast Monitor

A web app for a UK day-ahead power purchasing desk. It answers two questions for purchase decisions made in **January 2026**:

1. **What was the latest national wind forecast available when we had to decide?** (default cutoff: 09:00 UK time on the day before delivery)
2. **How accurate is that forecast?** This tells the desk how much extra power to buy as a buffer.

Data: Elexon BMRS. Actuals come from [FUELHH](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream) (`fuelType=WIND`) and forecasts from [WINDFOR](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream). National level only.

**Live app:** https://gridbricks-olive.vercel.app · **API docs:** https://gridbricks-olive.vercel.app/api/docs

## Headline findings (cutoff 09:00 D-1, delivery 2 Jan – 1 Feb 2026, 744 hours)

| Metric | Value | What it means for the desk |
|---|---|---|
| MAE | ~2.0 GW | On average the forecast was off by about 2 GW in a given hour. |
| Bias (forecast − actual) | **+1.75 GW** | The forecast was too high in most hours, so buying against it would have left the desk short. |
| P10 / P50 / P90 error | −0.55 / +1.59 / +4.10 GW | In 9 out of 10 hours, actual wind was at most 4.1 GW below the forecast. That is the size of buffer to hold. |
| WAPE | ~18% | Total absolute error as a share of total wind output. |
| MAE by lead time | 1.66 GW (0–6 h) → 2.33 GW (42–48 h) | Deciding later gives a more accurate forecast, but the bias is there at every lead time. |

The over-forecast is present even a few hours ahead, so it looks structural rather than a weather-forecasting problem. A likely cause is that FUELHH records metered output *after* curtailment (grid constraints, especially in Scotland), while WINDFOR forecasts what the wind could produce. This is a hypothesis; checking it needs data outside this project's scope.

## How the forecast is selected (no lookahead)

For delivery day **D**:

1. `cutoff = (D − 1 day) at 09:00 Europe/London`, converted to UTC. The time and the day offset are configurable.
2. A forecast row is a candidate if `publishTime ≤ cutoff`, `startTime` falls within D, and `startTime > publishTime`. The last condition matters because each WINDFOR run also contains about a day of "backcast" rows for hours already past.
3. For each hour, the candidate with the **latest `publishTime`** wins.
4. A slot is flagged **stale** if its forecast is more than 24 h older than the cutoff, and **missing** if nothing was published before the cutoff. A forecast published after the cutoff is never used.

WINDFOR publishes 8 runs a day (02:30, 04:30, 07:30, 09:30, 11:30, 15:30, 18:30, 22:30 UTC), so with the default cutoff the **07:30 UTC run on D-1** is used. Its lead time to D is 16.5–39.5 h. The code does not assume fixed run times: on 15 Jan the 11:30 run was published at 12:25 instead.

**Data range used:** actuals for settlement days 2026-01-02 → 2026-02-01; forecasts published 2025-12-30 → 2026-02-01 (extra history for the lead-time analysis).

## Accuracy metrics and why

Error = **forecast − actual** (MW). Forecasts are hourly and actuals half-hourly, so each forecast hour is compared with the mean of its two half-hours.

- **MAE** is the headline because it reads directly in MW. **Bias** shows which way to lean. **RMSE** sits next to MAE to show how much the large misses weigh.
- **Signed error percentiles (P5–P95)** are the most actionable figure: they size the buffer.
- **WAPE** (Σ|e| / Σactual) is used as the percentage measure.
- Breakdowns by **hour of day**, **day**, **wind level** and **lead time** show when the forecast is weakest, and what deciding earlier or later costs.

**Deliberately avoided:**
- **MAPE:** it blows up when wind output is near zero.
- **Mean error on its own:** over- and under-forecasts cancel out.
- **R²:** it can look good despite a large constant bias.
- **RMSE on its own:** a few storm hours dominate it.

## Architecture

```
Elexon BMRS ──(one-off ingest CLI)──> data/*.csv  (committed; Jan 2026 is historical)
                                           │ loaded once per process
React (Vite, static) ──fetch /api/*──> FastAPI ──> domain/selection.py  (forecast at cutoff)
                                                └─> domain/metrics.py    (errors and breakdowns)
```

- **Pre-fetched data rather than live API calls.** The month is historical, so this makes the app fast and reproducible, and avoids Elexon rate limits and serverless timeouts. Re-fetch with one command (see below).
- **CSV files, not a database.** About 21k rows don't need one, and Parquet would add pyarrow, which pushes the Vercel bundle size limit.
- **Computed on request.** Changing the cutoff re-runs selection and metrics in under 0.3 s.
- **FastAPI** for typed request and response models (Pydantic), automatic OpenAPI docs at `/api/docs`, and native ASGI support on Vercel. **pandas** for the group-bys and percentiles.
- **Domain code is pure functions** (DataFrame in, DataFrame or dict out) with no I/O, so the rules that matter most are unit-tested in isolation.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness |
| `GET /api/meta` | Delivery days, defaults, run times, data-quality counts |
| `GET /api/forecast?date=2026-01-15&cutoff=09:00` | Forecast selected for one delivery day, with P10–P90 band, actuals and errors |
| `GET /api/accuracy?from=&to=&cutoff=` | Summary metrics plus breakdowns by hour, day, wind level and error histogram |
| `GET /api/accuracy/horizon?from=&to=&cutoff=` | MAE, RMSE and bias by lead-time bucket, plus the cutoff's lead-time range |

Full request and response shapes are in [`docs/CONTRACT.md`](docs/CONTRACT.md) and the live OpenAPI page.

## Repository layout

```
api/index.py                  Vercel entry point (exposes the FastAPI app)
backend/app/main.py           FastAPI app; also serves the React build in Docker
backend/app/routes.py         HTTP layer: validation, calls the domain, shapes JSON
backend/app/schemas.py        Pydantic response models
backend/app/config.py         Date range, default cutoff, timezone, paths
backend/app/data/loader.py    Loads the CSVs once (cached)
backend/app/domain/selection.py   Forecast-at-cutoff and by-lead-time selection
backend/app/domain/metrics.py     Hourly actuals, errors, metrics, breakdowns
backend/ingest/fetch_elexon.py    CLI: fetch from Elexon, clean, dedupe, gap report
backend/tests/                pytest: selection, metrics, ingest cleaning, API
data/                         actuals_wind.csv, forecasts_wind.csv, data_quality.json
frontend/                     React + TypeScript (Vite, Recharts)
docs/CONTRACT.md              Data file, domain function and HTTP contracts
Dockerfile, docker-compose.yml   One container: API + built frontend on port 8000
vercel.json                   Vercel build, Python function and /api rewrite
.github/workflows/ci.yml      Tests, lint and build on every push
```

## Running it

**Docker (one command):**
```bash
docker compose up --build
```
Then open http://localhost:8000 (API docs at http://localhost:8000/api/docs).

**Local dev** (Python 3.12, Node 22):
```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn backend.app.main:app --reload --port 8000
```
```bash
cd frontend && npm ci && npm run dev
```
Vite proxies `/api` to port 8000. Run the frontend with mock data and no backend via `VITE_USE_MOCKS=true npm run dev`.

**Tests:**
```bash
.venv/bin/python -m pytest -q
```

**Re-fetch data from Elexon:**
```bash
.venv/bin/python -m backend.ingest.fetch_elexon
```

**Deploy:** `vercel deploy --prod`. Vercel builds `frontend/` as static files and `api/index.py` as a Python function (see `vercel.json`). `"framework": "vite"` is set on purpose: without it, Vercel auto-detects a FastAPI backend app, sends every route to Python and skips the Python dependency install when a custom install command is set. Docker is used for local and production-like runs; Vercel deploys from source.

## Assumptions

- "Decisions made in January 2026" is read as decisions on Jan 1–31, so **delivery days are Jan 2 – Feb 1**. The other reading (delivery during January) only shifts the range by one day.
- The decision cutoff is a parameter, not an encoded market rule. The default of 09:00 UK time sits ahead of the GB morning day-ahead auction gate closures.
- Installed capacity is out of scope ("no other data"), so errors are normalised by actual output (WAPE), not by capacity.
- January has no clock change, so UK time = UTC. The code still converts through `Europe/London`, and DST days are covered by tests.

## Use of AI tools

This project was built with **Claude Code (Anthropic)** as a pair programmer:
- planning the architecture;
- running parallel sub-agents for the ingest, domain logic and frontend work against a shared contract (`docs/CONTRACT.md`);
- writing code and tests.

All design decisions, data interpretation and results were reviewed by the author.
