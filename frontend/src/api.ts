// Types + fetch functions for the backend HTTP API (docs/CONTRACT.md section 3).
// All timestamps are UTC ISO-8601 strings. Error convention: error = forecast − actual (MW),
// so a positive error means the forecast was too high (over-forecast).

export type SlotStatus = 'ok' | 'stale' | 'missing'

export interface Meta {
  deliveryDays: string[] // "YYYY-MM-DD"
  defaults: { cutoffTime: string; cutoffDayOffset: number; tz: string }
  runTimesUtc: string[] // "HH:MM"
  dataQuality: { missingActualSlots: number; incompleteForecastRuns: number }
  source: string
}

export interface ForecastPoint {
  startTime: string
  forecastMW: number | null
  publishTime: string | null
  horizonH: number | null
  status: SlotStatus
  actualMW: number | null
  errorMW: number | null
  bandLowMW: number | null
  bandHighMW: number | null
}

export interface ForecastResponse {
  deliveryDate: string
  cutoffUtc: string
  runsUsed: string[]
  summary: {
    energyMWh: number
    minMW: number
    maxMW: number
    okSlots: number
    staleSlots: number
    missingSlots: number
  }
  errorBand: { p10MW: number; p90MW: number }
  points: ForecastPoint[]
  actualsHalfHourly: { startTime: string; actualMW: number }[]
}

// summary_metrics returns all None when n == 0, and wape None when Σactual == 0.
export interface Metrics {
  n: number
  mae: number | null
  rmse: number | null
  bias: number | null
  wape: number | null // fraction: 0.12 = 12%
  p5: number | null
  p10: number | null
  p50: number | null
  p90: number | null
  p95: number | null
  maxAbs: number | null
}

export interface HourStat { hour: number; n: number; mae: number; bias: number; p10: number; p90: number }
export interface DayStat { date: string; n: number; mae: number; bias: number }
export interface LevelStat {
  level: 'low' | 'medium' | 'high'
  minMW: number
  maxMW: number
  n: number
  mae: number
  bias: number
}
export interface HistogramBin { binStartMW: number; count: number }

export interface AccuracyResponse {
  from: string
  to: string
  cutoff: string
  metrics: Metrics
  byHour: HourStat[]
  byDay: DayStat[]
  byLevel: LevelStat[]
  histogram: HistogramBin[]
}

export interface HorizonBucket {
  bucket: string // "0-6h"
  minH: number
  maxH: number
  n: number
  mae: number
  rmse: number
  bias: number
}

export interface HorizonResponse {
  buckets: HorizonBucket[]
  cutoffHorizonH: { min: number; max: number }
}

type Params = Record<string, string>

async function getJson<T>(path: string, params: Params, signal?: AbortSignal): Promise<T> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    const { mockGet } = await import('./mocks')
    return (await mockGet(path, params, signal)) as T
  }
  const qs = new URLSearchParams(params).toString()
  const res = await fetch(qs ? `${path}?${qs}` : path, { signal })
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body?.detail ?? body)
    } catch {
      // non-JSON error body; fall back to the status line
    }
    throw new Error(`${res.status} ${res.statusText}${detail ? ` – ${detail}` : ''}`)
  }
  return (await res.json()) as T
}

export const fetchMeta = (signal?: AbortSignal) => getJson<Meta>('/api/meta', {}, signal)

export const fetchForecast = (date: string, cutoff: string, signal?: AbortSignal) =>
  getJson<ForecastResponse>('/api/forecast', { date, cutoff }, signal)

export const fetchAccuracy = (from: string, to: string, cutoff: string, signal?: AbortSignal) =>
  getJson<AccuracyResponse>('/api/accuracy', { from, to, cutoff }, signal)

export const fetchHorizon = (from: string, to: string, cutoff: string, signal?: AbortSignal) =>
  getJson<HorizonResponse>('/api/accuracy/horizon', { from, to, cutoff }, signal)
