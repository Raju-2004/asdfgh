import os
from datetime import date, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
STATIC_DIR = Path(os.getenv("STATIC_DIR", REPO_ROOT / "frontend" / "dist"))

# Decisions made in January 2026 on D-1 -> delivery days 2 Jan .. 1 Feb.
FIRST_DELIVERY_DAY = date(2026, 1, 2)
LAST_DELIVERY_DAY = date(2026, 2, 1)

DEFAULT_CUTOFF = time(9, 0)
CUTOFF_DAY_OFFSET = 1
TZ = "Europe/London"
MAX_STALENESS_H = 24
WINDFOR_RUN_TIMES_UTC = ["02:30", "04:30", "07:30", "09:30", "11:30", "15:30", "18:30", "22:30"]
