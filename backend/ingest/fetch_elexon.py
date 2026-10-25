"""Fetch UK wind actuals (FUELHH) and forecasts (WINDFOR) from Elexon BMRS.

Writes three files (see docs/CONTRACT.md section 1):
    data/actuals_wind.csv
    data/forecasts_wind.csv
    data/data_quality.json

Usage (from the repo root):
    .venv/bin/python -m backend.ingest.fetch_elexon
    .venv/bin/python -m backend.ingest.fetch_elexon --out-dir data \
        --actuals-from 2026-01-02 --actuals-to 2026-02-01 \
        --forecasts-from 2025-12-30T00:00Z --forecasts-to 2026-02-01T23:59Z

Fetching (network) and cleaning (pure DataFrame -> DataFrame) are kept in
separate functions so the cleaning logic can be unit-tested without HTTP.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets"
USER_AGENT = "gridbricks-wind-monitor/0.1 (research; contact via repo owner)"
TIMEOUT_S = 30.0
MAX_RETRIES = 3
CHUNK_DAYS = 7
LOCAL_TZ = "Europe/London"  # settlement days are defined in UK local time

ACTUALS_COLUMNS = ["startTime", "publishTime", "settlementDate", "settlementPeriod", "generation"]
FORECASTS_COLUMNS = ["publishTime", "startTime", "generation"]

DEFAULT_ACTUALS_FROM = "2026-01-02"
DEFAULT_ACTUALS_TO = "2026-02-01"
DEFAULT_FORECASTS_FROM = "2025-12-30T00:00Z"
DEFAULT_FORECASTS_TO = "2026-02-01T23:59Z"


# ---------------------------------------------------------------------------
# Fetching (network)
# ---------------------------------------------------------------------------


def get_json(client: httpx.Client, url: str, params: dict[str, str]) -> list[dict[str, Any]]:
    """GET a URL and return the JSON list, retrying with exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            last_error = error
            if attempt < MAX_RETRIES:
                wait_s = 2**attempt  # 1 s, 2 s, 4 s
                print(f"  request failed ({error}); retrying in {wait_s}s")
                time.sleep(wait_s)
    raise RuntimeError(f"giving up on {url} {params}") from last_error


def make_client() -> httpx.Client:
    """An httpx client with our timeout and a polite User-Agent."""
    return httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})


def fetch_actuals_raw(date_from: date, date_to: date) -> pd.DataFrame:
    """Fetch FUELHH WIND rows for settlement dates [date_from, date_to] in 7-day chunks."""
    rows: list[dict[str, Any]] = []
    with make_client() as client:
        chunk_start = date_from
        while chunk_start <= date_to:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), date_to)
            print(f"FUELHH {chunk_start} -> {chunk_end}")
            params = {
                "settlementDateFrom": chunk_start.isoformat(),
                "settlementDateTo": chunk_end.isoformat(),
                "fuelType": "WIND",
            }
            rows.extend(get_json(client, f"{BASE_URL}/FUELHH/stream", params))
            chunk_start = chunk_end + timedelta(days=1)
    return pd.DataFrame(rows)


def fetch_forecasts_raw(publish_from: pd.Timestamp, publish_to: pd.Timestamp) -> pd.DataFrame:
    """Fetch WINDFOR rows with publishTime in [publish_from, publish_to] in 7-day chunks."""
    rows: list[dict[str, Any]] = []
    end = publish_to.to_pydatetime()
    with make_client() as client:
        chunk_start = publish_from.to_pydatetime()
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end)
            print(f"WINDFOR {iso_z(chunk_start)} -> {iso_z(chunk_end)}")
            params = {
                "publishDateTimeFrom": iso_z(chunk_start),
                "publishDateTimeTo": iso_z(chunk_end),
            }
            rows.extend(get_json(client, f"{BASE_URL}/WINDFOR/stream", params))
            # Next chunk starts at this chunk's end; any boundary overlap is removed by dedupe.
            chunk_start = chunk_end
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Cleaning (pure functions: DataFrame in -> DataFrame + stats out)
# ---------------------------------------------------------------------------


def to_utc(values: pd.Series) -> pd.Series:
    """Parse ISO strings (or timestamps) into tz-aware UTC timestamps."""
    return pd.to_datetime(values, utc=True)


def clean_actuals(raw: pd.DataFrame, date_from: date, date_to: date) -> tuple[pd.DataFrame, dict[str, int]]:
    """Filter to WIND, dedupe on startTime (latest publishTime wins), trim, sort.

    The range is by settlementDate (inclusive). Gaps are NOT filled.
    Returns (clean frame with ACTUALS_COLUMNS, stats dict).
    """
    df = raw.copy()
    if "fuelType" in df.columns:
        df = df[df["fuelType"] == "WIND"]
    df["startTime"] = to_utc(df["startTime"])
    df["publishTime"] = to_utc(df["publishTime"])
    df["settlementDate"] = pd.to_datetime(df["settlementDate"]).dt.date
    df["settlementPeriod"] = df["settlementPeriod"].astype(int)

    in_range = (df["settlementDate"] >= date_from) & (df["settlementDate"] <= date_to)
    df = df[in_range]

    rows_before = len(df)
    df = df.sort_values(["startTime", "publishTime"])
    df = df.drop_duplicates(subset=["startTime"], keep="last")
    duplicates_removed = rows_before - len(df)

    df = df.sort_values("startTime").reset_index(drop=True)
    df["generation"] = df["generation"].round().astype(int)
    return df[ACTUALS_COLUMNS], {"duplicatesRemoved": duplicates_removed}


def clean_forecasts(
    raw: pd.DataFrame, publish_from: pd.Timestamp, publish_to: pd.Timestamp
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Dedupe on (publishTime, startTime) keeping the last row, trim on publishTime, sort.

    Backcast rows (startTime <= publishTime) are kept on purpose.
    Returns (clean frame with FORECASTS_COLUMNS, stats dict).
    """
    df = raw.copy()
    df["publishTime"] = to_utc(df["publishTime"])
    df["startTime"] = to_utc(df["startTime"])

    in_range = (df["publishTime"] >= publish_from) & (df["publishTime"] <= publish_to)
    df = df[in_range]

    rows_before = len(df)
    df = df.drop_duplicates(subset=["publishTime", "startTime"], keep="last")
    duplicates_removed = rows_before - len(df)

    df = df.sort_values(["publishTime", "startTime"]).reset_index(drop=True)
    df["generation"] = df["generation"].round().astype(int)
    return df[FORECASTS_COLUMNS], {"duplicatesRemoved": duplicates_removed}


# ---------------------------------------------------------------------------
# Data quality (pure)
# ---------------------------------------------------------------------------


def expected_actual_slots(date_from: date, date_to: date) -> pd.DatetimeIndex:
    """Every 30-min startTime (UTC) of the settlement days [date_from, date_to].

    Settlement days run from UK-local midnight to midnight, so DST days have 46/50 slots.
    """
    start = pd.Timestamp(date_from).tz_localize(LOCAL_TZ).tz_convert("UTC")
    end = pd.Timestamp(date_to + timedelta(days=1)).tz_localize(LOCAL_TZ).tz_convert("UTC")
    return pd.date_range(start, end, freq="30min", inclusive="left")


def missing_actual_slots(actuals: pd.DataFrame, date_from: date, date_to: date) -> tuple[int, list[pd.Timestamp]]:
    """Return (expected slot count, sorted list of missing startTimes)."""
    expected = expected_actual_slots(date_from, date_to)
    present = set(actuals["startTime"])
    missing = [slot for slot in expected if slot not in present]
    return len(expected), missing


def incomplete_runs(forecasts: pd.DataFrame) -> tuple[int, list[dict[str, Any]]]:
    """Count forecast runs and list runs with fewer rows than the most common run size."""
    counts = forecasts.groupby("publishTime").size()
    if counts.empty:
        return 0, []
    modal_rows = int(counts.mode().max())
    short = counts[counts < modal_rows]
    runs = [{"publishTime": publish_time, "rows": int(rows)} for publish_time, rows in short.items()]
    return len(counts), runs


def build_data_quality(
    actuals: pd.DataFrame,
    actual_stats: dict[str, int],
    date_from: date,
    date_to: date,
    forecasts: pd.DataFrame,
    forecast_stats: dict[str, int],
    publish_from: pd.Timestamp,
    publish_to: pd.Timestamp,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the data_quality.json dict exactly as specified in the contract."""
    expected_count, missing = missing_actual_slots(actuals, date_from, date_to)
    run_count, short_runs = incomplete_runs(forecasts)
    generated_at = generated_at or datetime.now(timezone.utc)
    return {
        "generatedAt": iso_z(pd.Timestamp(generated_at)),
        "actuals": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "rows": len(actuals),
            "expectedSlots": expected_count,
            "duplicatesRemoved": actual_stats["duplicatesRemoved"],
            "missingSlots": [iso_z(slot) for slot in missing],
        },
        "forecasts": {
            "from": iso_z(publish_from),
            "to": iso_z(publish_to),
            "rows": len(forecasts),
            "runs": run_count,
            "duplicatesRemoved": forecast_stats["duplicatesRemoved"],
            "incompleteRuns": [
                {"publishTime": iso_z(run["publishTime"]), "rows": run["rows"]} for run in short_runs
            ],
        },
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def iso_z(ts: pd.Timestamp) -> str:
    """Format a timestamp as 'YYYY-MM-DDTHH:MM:SSZ' (UTC)."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def write_actuals_csv(actuals: pd.DataFrame, path: Path) -> None:
    """Write actuals with ISO ...Z timestamps in contract column order."""
    out = actuals[ACTUALS_COLUMNS].copy()
    out["startTime"] = out["startTime"].map(iso_z)
    out["publishTime"] = out["publishTime"].map(iso_z)
    out["settlementDate"] = out["settlementDate"].map(lambda d: d.isoformat())
    out.to_csv(path, index=False)


def write_forecasts_csv(forecasts: pd.DataFrame, path: Path) -> None:
    """Write forecasts with ISO ...Z timestamps in contract column order."""
    out = forecasts[FORECASTS_COLUMNS].copy()
    out["publishTime"] = out["publishTime"].map(iso_z)
    out["startTime"] = out["startTime"].map(iso_z)
    out.to_csv(path, index=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Elexon wind actuals and forecasts.")
    parser.add_argument("--actuals-from", default=DEFAULT_ACTUALS_FROM, help="settlementDate, YYYY-MM-DD")
    parser.add_argument("--actuals-to", default=DEFAULT_ACTUALS_TO, help="settlementDate, YYYY-MM-DD (inclusive)")
    parser.add_argument("--forecasts-from", default=DEFAULT_FORECASTS_FROM, help="publishTime, ISO UTC")
    parser.add_argument("--forecasts-to", default=DEFAULT_FORECASTS_TO, help="publishTime, ISO UTC (inclusive)")
    parser.add_argument("--out-dir", default="data", help="output directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    actuals_from = date.fromisoformat(args.actuals_from)
    actuals_to = date.fromisoformat(args.actuals_to)
    forecasts_from = pd.Timestamp(args.forecasts_from).tz_convert("UTC")
    forecasts_to = pd.Timestamp(args.forecasts_to).tz_convert("UTC")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    actuals_raw = fetch_actuals_raw(actuals_from, actuals_to)
    forecasts_raw = fetch_forecasts_raw(forecasts_from, forecasts_to)

    actuals, actual_stats = clean_actuals(actuals_raw, actuals_from, actuals_to)
    forecasts, forecast_stats = clean_forecasts(forecasts_raw, forecasts_from, forecasts_to)
    quality = build_data_quality(
        actuals, actual_stats, actuals_from, actuals_to,
        forecasts, forecast_stats, forecasts_from, forecasts_to,
    )

    write_actuals_csv(actuals, out_dir / "actuals_wind.csv")
    write_forecasts_csv(forecasts, out_dir / "forecasts_wind.csv")
    with open(out_dir / "data_quality.json", "w") as f:
        json.dump(quality, f, indent=2)
        f.write("\n")

    a, fc = quality["actuals"], quality["forecasts"]
    print(f"actuals:   {a['rows']} rows, {len(a['missingSlots'])} missing of {a['expectedSlots']}, "
          f"{a['duplicatesRemoved']} duplicates removed")
    print(f"forecasts: {fc['rows']} rows, {fc['runs']} runs, {len(fc['incompleteRuns'])} incomplete, "
          f"{fc['duplicatesRemoved']} duplicates removed")


if __name__ == "__main__":
    main()
