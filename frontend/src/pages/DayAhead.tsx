import { useMemo, useState } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { fetchForecast, type ForecastResponse, type Meta } from '../api'
import { C } from '../colors'
import { Card, ErrorBox, Kpi, Legend, Loading } from '../components/ui'
import * as f from '../format'
import { useApi } from '../hooks/useApi'

interface Props {
  meta: Meta
  cutoff: string
}

/** One row per timestamp on the shared (half-hourly) time axis. */
interface Row {
  t: number
  forecast: number | null
  band: [number, number] | null
  actual: number | null
}

const HOUR_MS = 3_600_000

function buildRows(data: ForecastResponse): Row[] {
  const rows = new Map<number, Row>()
  const row = (iso: string) => {
    const t = Date.parse(iso)
    let r = rows.get(t)
    if (!r) {
      r = { t, forecast: null, band: null, actual: null }
      rows.set(t, r)
    }
    return r
  }
  for (const p of data.points) {
    const r = row(p.startTime)
    r.forecast = p.forecastMW
    r.band = p.bandLowMW != null && p.bandHighMW != null ? [p.bandLowMW, p.bandHighMW] : null
  }
  for (const a of data.actualsHalfHourly) row(a.startTime).actual = a.actualMW
  return [...rows.values()].sort((a, b) => a.t - b.t)
}

export default function DayAhead({ meta, cutoff }: Props) {
  const days = meta.deliveryDays
  const [date, setDate] = useState(() => days[Math.min(13, days.length - 1)] ?? '')
  const [showActual, setShowActual] = useState(false)

  const { data, error, loading } = useApi(date ? `forecast|${date}|${cutoff}` : null, (signal) =>
    fetchForecast(date, cutoff, signal),
  )

  const idx = days.indexOf(date)
  const rows = useMemo(() => (data ? buildRows(data) : []), [data])

  return (
    <div className="page">
      <div className="toolbar">
        <label className="field">
          <span>Delivery day</span>
          <div className="date-nav">
            <button type="button" className="btn" onClick={() => setDate(days[idx - 1])} disabled={idx <= 0} aria-label="Previous day">
              ‹
            </button>
            <select value={date} onChange={(e) => setDate(e.target.value)}>
              {days.map((d) => (
                <option key={d} value={d}>
                  {f.longDate(d)}
                </option>
              ))}
            </select>
            <button type="button" className="btn" onClick={() => setDate(days[idx + 1])} disabled={idx >= days.length - 1} aria-label="Next day">
              ›
            </button>
          </div>
        </label>
        <label className="toggle">
          <input type="checkbox" checked={showActual} onChange={(e) => setShowActual(e.target.checked)} />
          <span className="toggle__track" aria-hidden />
          Show actual outcome
        </label>
      </div>

      {error && <ErrorBox message={error} />}
      {!data && loading && <Loading label="Loading forecast…" />}

      {data && (
        <div className={`stack${loading ? ' is-refreshing' : ''}`}>
          <p className="caption">
            What you'd have seen at <strong>{f.time(data.cutoffUtc)}</strong> on <strong>{f.dayMonth(data.cutoffUtc)}</strong>
            {showActual ? ', and how it turned out.' : ` for delivery on ${f.longDate(data.deliveryDate)}.`}
          </p>
          <DayKpis data={data} showActual={showActual} />
          <Card
            title="Wind generation forecast"
            subtitle="Great Britain, all wind. Shaded band: where the outcome landed 80% of the time, based on past errors at this cutoff."
            wide
          >
            <DayChart rows={rows} showActual={showActual} />
            <Legend
              items={[
                { label: 'Forecast (hourly)', color: C.forecast },
                { label: '80% likely range (P10–P90)', color: C.band, kind: 'band' },
                ...(showActual ? [{ label: 'Actual (half-hourly)', color: C.actual }] : []),
              ]}
            />
          </Card>
          <Card title="Hour by hour" subtitle="Error = forecast − actual. Positive means the forecast was too high." wide>
            <HourTable data={data} showActual={showActual} />
          </Card>
        </div>
      )}
    </div>
  )
}

function DayKpis({ data, showActual }: { data: ForecastResponse; showActual: boolean }) {
  const { summary, points, runsUsed, cutoffUtc } = data
  const leads = points.map((p) => p.horizonH).filter((h): h is number => h != null)
  const errors = points.map((p) => p.errorMW).filter((e): e is number => e != null)
  const dayMae = errors.length ? errors.reduce((s, e) => s + Math.abs(e), 0) / errors.length : null
  const dayBias = errors.length ? errors.reduce((s, e) => s + e, 0) / errors.length : null
  const latestRun = runsUsed.length ? runsUsed[runsUsed.length - 1] : null
  const flagged = summary.staleSlots + summary.missingSlots

  return (
    <div className="kpis">
      <Kpi label="Forecast energy" value={f.mwh(summary.energyMWh)} hint={`≈ ${f.count(summary.energyMWh / 1000)} GWh over the delivery day`} />
      <Kpi label="Min / max output" value={`${f.count(summary.minMW)} / ${f.mw(summary.maxMW)}`} hint="Lowest and highest hourly forecast" />
      <Kpi
        label="Forecast run used"
        value={latestRun ? f.timeAndDay(latestRun) : f.DASH}
        hint={
          <>
            Latest run published before the {f.timeAndDay(cutoffUtc)} cutoff
            {runsUsed.length > 1 && ` (+${runsUsed.length - 1} older run)`}
          </>
        }
      />
      <Kpi
        label="Lead time"
        value={leads.length ? `${f.hours(Math.min(...leads))} – ${f.hours(Math.max(...leads))}` : f.DASH}
        hint="Gap between the forecast being published and each delivery hour"
      />
      {flagged > 0 && (
        <Kpi
          tone="warn"
          label="Data gaps"
          value={`${summary.staleSlots} stale · ${summary.missingSlots} missing`}
          hint="Stale: only an older run covers the hour. Missing: no forecast available."
        />
      )}
      {showActual && (
        <Kpi
          label="How it turned out"
          value={dayMae == null ? f.DASH : `${f.mw(dayMae)} MAE`}
          hint={dayBias == null ? 'No actuals for this day' : `Average miss this day; bias ${f.signedMw(dayBias)} (${dayBias >= 0 ? 'over' : 'under'}-forecast)`}
        />
      )}
    </div>
  )
}

function DayChart({ rows, showActual }: { rows: Row[]; showActual: boolean }) {
  if (!rows.length) return <p className="empty">No forecast points for this day.</p>
  const start = rows[0].t
  // Round the last timestamp up to the next whole hour (works for 23/25-hour DST days too).
  const end = Math.ceil((rows[rows.length - 1].t + 1) / HOUR_MS) * HOUR_MS
  const ticks: number[] = []
  for (let t = start; t <= end; t += 3 * HOUR_MS) ticks.push(t)
  const yTicks = f.gwTicks(rows.flatMap((r) => [r.forecast, r.band?.[1], showActual ? r.actual : null]))

  return (
    <div className="chart chart--tall">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={[start, end]}
            ticks={ticks}
            tickFormatter={(t: number) => f.time(t)}
            stroke={C.axis}
            fontSize={12}
          />
          <YAxis tickFormatter={f.gwAxis} stroke={C.axis} fontSize={12} width={56} ticks={yTicks} domain={[yTicks[0], yTicks[yTicks.length - 1]]} />
          <Tooltip content={<DayTooltip showActual={showActual} />} />
          <Area dataKey="band" type="monotone" stroke="none" fill={C.band} fillOpacity={0.35} connectNulls isAnimationActive={false} />
          <Line dataKey="forecast" type="monotone" stroke={C.forecast} strokeWidth={2.5} dot={false} connectNulls isAnimationActive={false} />
          {showActual && (
            <Line dataKey="actual" type="monotone" stroke={C.actual} strokeWidth={1.75} dot={false} connectNulls={false} isAnimationActive={false} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

interface TooltipProps {
  active?: boolean
  payload?: { payload: Row }[]
  showActual: boolean
}

function DayTooltip({ active, payload, showActual }: TooltipProps) {
  const r = payload?.[0]?.payload
  if (!active || !r) return null
  return (
    <div className="tooltip">
      <div className="tooltip__title">{f.time(r.t)}</div>
      {r.forecast != null && (
        <div>
          <span style={{ color: C.forecast }}>●</span> Forecast {f.mw(r.forecast)}
        </div>
      )}
      {r.band && (
        <div className="muted">
          80% range {f.count(r.band[0])} – {f.mw(r.band[1])}
        </div>
      )}
      {showActual && r.actual != null && (
        <div>
          <span style={{ color: C.actual }}>●</span> Actual {f.mw(r.actual)}
        </div>
      )}
    </div>
  )
}

function HourTable({ data, showActual }: { data: ForecastResponse; showActual: boolean }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Time</th>
            <th className="num">Forecast</th>
            <th>Run</th>
            <th className="num">Lead</th>
            {showActual && <th className="num">Actual</th>}
            {showActual && <th className="num">Error</th>}
          </tr>
        </thead>
        <tbody>
          {data.points.map((p) => (
            <tr key={p.startTime}>
              <td>{f.time(p.startTime)}</td>
              <td className="num">{f.mw(p.forecastMW)}</td>
              <td>
                {f.timeAndDay(p.publishTime)}
                {p.status !== 'ok' && <span className={`badge badge--${p.status}`}>{p.status}</span>}
              </td>
              <td className="num">{f.hours(p.horizonH)}</td>
              {showActual && <td className="num">{f.mw(p.actualMW)}</td>}
              {showActual && (
                <td
                  className={`num ${p.errorMW == null ? '' : p.errorMW > 0 ? 'err-over' : p.errorMW < 0 ? 'err-under' : ''}`}
                  title={p.errorMW == null ? undefined : p.errorMW > 0 ? 'Over-forecast' : 'Under-forecast'}
                >
                  {f.signedMw(p.errorMW)}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
