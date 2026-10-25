"""Loads the pre-fetched Elexon CSVs once per process (one cold start on Vercel)."""
import json
from functools import lru_cache

import pandas as pd

from backend.app.config import DATA_DIR


def _read(name: str, time_cols: list[str]) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / name)
    for col in time_cols:
        df[col] = pd.to_datetime(df[col], utc=True)
    df["generation"] = df["generation"].astype(float)
    return df


@lru_cache(maxsize=1)
def load_actuals() -> pd.DataFrame:
    return _read("actuals_wind.csv", ["startTime", "publishTime"])


@lru_cache(maxsize=1)
def load_forecasts() -> pd.DataFrame:
    return _read("forecasts_wind.csv", ["publishTime", "startTime"])


@lru_cache(maxsize=1)
def load_data_quality() -> dict:
    return json.loads((DATA_DIR / "data_quality.json").read_text())
