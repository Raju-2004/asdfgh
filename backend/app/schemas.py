"""Response models. Field names are camelCase to match the JSON contract (docs/CONTRACT.md)."""
from pydantic import BaseModel, Field


class Defaults(BaseModel):
    cutoffTime: str
    cutoffDayOffset: int
    tz: str


class DataQualitySummary(BaseModel):
    missingActualSlots: int
    incompleteForecastRuns: int


class Meta(BaseModel):
    deliveryDays: list[str]
    defaults: Defaults
    runTimesUtc: list[str]
    dataQuality: DataQualitySummary
    source: str


class ForecastSummary(BaseModel):
    energyMWh: float | None
    minMW: float | None
    maxMW: float | None
    okSlots: int
    staleSlots: int
    missingSlots: int


class ErrorBand(BaseModel):
    p10MW: float | None
    p90MW: float | None


class ForecastPoint(BaseModel):
    startTime: str
    forecastMW: float | None
    publishTime: str | None
    horizonH: float | None
    status: str
    actualMW: float | None
    errorMW: float | None
    bandLowMW: float | None
    bandHighMW: float | None


class ActualPoint(BaseModel):
    startTime: str
    actualMW: float


class DayAheadForecast(BaseModel):
    deliveryDate: str
    cutoffUtc: str
    runsUsed: list[str]
    summary: ForecastSummary
    errorBand: ErrorBand
    points: list[ForecastPoint]
    actualsHalfHourly: list[ActualPoint]


class SummaryMetrics(BaseModel):
    n: int
    mae: float | None
    rmse: float | None
    bias: float | None
    wape: float | None
    p5: float | None
    p10: float | None
    p50: float | None
    p90: float | None
    p95: float | None
    maxAbs: float | None


class HourStat(BaseModel):
    hour: int
    n: int
    mae: float | None
    bias: float | None
    p10: float | None
    p90: float | None


class DayStat(BaseModel):
    date: str
    n: int
    mae: float | None
    bias: float | None


class LevelStat(BaseModel):
    level: str
    minMW: float | None
    maxMW: float | None
    n: int
    mae: float | None
    bias: float | None


class HistogramBin(BaseModel):
    binStartMW: float
    count: int


class Accuracy(BaseModel):
    from_: str = Field(serialization_alias="from")  # "from" is a Python keyword
    to: str
    cutoff: str
    metrics: SummaryMetrics
    byHour: list[HourStat]
    byDay: list[DayStat]
    byLevel: list[LevelStat]
    histogram: list[HistogramBin]


class HorizonBucket(BaseModel):
    bucket: str
    minH: float
    maxH: float
    n: int
    mae: float | None
    rmse: float | None
    bias: float | None


class HorizonRange(BaseModel):
    min: float | None
    max: float | None


class HorizonAccuracy(BaseModel):
    buckets: list[HorizonBucket]
    cutoffHorizonH: HorizonRange
