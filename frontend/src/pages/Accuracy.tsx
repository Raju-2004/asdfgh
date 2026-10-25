import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { fetchAccuracy, fetchHorizon, type AccuracyResponse, type HorizonResponse, type Meta } from '../api'
import { C } from '../colors'
import { Card, ErrorBox, Kpi, Legend, Loading } from '../components/ui'
import * as f from '../format'
import { useApi } from '../hooks/useApi'

interface Props {
  meta: Meta
  cutoff: string
}

const axis = { stroke: C.axis, fontSize: 12 }
/** Y axis props for MW values shown as GW with round-number ticks. */
const yGw = (values: number[]) => {
  const ticks = f.gwTicks(values)
  return { ...axis, tickFormatter: f.gwAxis, width: 60, ticks, domain: [ticks[0], ticks[ticks.length - 1]] }
}
const mwTooltip = (v: number) => f.mw(v)

export default function Accuracy({ meta, cutoff }: Props) {
  const from = meta.deliveryDays[0]
  const to = meta.deliveryDays[meta.deliveryDays.length - 1]
  const key = from && to ? `${from}|${to}|${cutoff}` : null

  const acc = useApi(key && `acc|${key}`, (signal) => fetchAccuracy(from, to, cutoff, signal))
  const hz = useApi(key && `hz|${key}`, (signal) => fetchHorizon(from, to, cutoff, signal))

  return (
    <div className="page">
      <p className="caption">
        How accurate was the forecast you'd have used at <strong>{cutoff}</strong> on D-1? Delivery days{' '}
        <strong>{f.shortDate(from)}</strong> – <strong>{f.shortDate(to)}</strong>, hourly, against actual wind output.
      </p>

      {acc.error && <ErrorBox message={acc.error} />}
      {!acc.data && acc.loading && <Loading label="Calculating accuracy…" />}
      {acc.data && (
        <div className={`stack${acc.loading ? ' is-refreshing' : ''}`}>
          <AccuracyKpis data={acc.data} />
          <div className="grid">
            <ByHourChart data={acc.data} />
            <ByDayChart data={acc.data} />
            <HistogramChart data={acc.data} />
            <ByLevelChart data={acc.data} />
          </div>
        </div>
      )}

      <Card
        title="Lead time: deciding earlier costs accuracy"
        subtitle="Average error of every forecast run by how far ahead it was published. The highlighted buckets are the lead times your cutoff actually uses."
        wide
      >
        {hz.error && <ErrorBox message={hz.error} />}
        {!hz.data && hz.loading && <Loading />}
        {hz.data && <HorizonChart data={hz.data} />}
      </Card>

      <aside className="callout">
        <strong>Why not MAPE?</strong> Wind output can be near zero, which makes percentage errors explode — we use WAPE
        instead (total absolute error ÷ total actual output).
      </aside>
    </div>
  )
}

/** "1.2 GW above" / "0.9 GW below" — how far the actual landed from the forecast. */
const offset = (actualMinusForecast: number) => `${f.gw(Math.abs(actualMinusForecast))} ${actualMinusForecast >= 0 ? 'above' : 'below'}`

function AccuracyKpis({ data }: { data: AccuracyResponse }) {
  const m = data.metrics
  const bias = m.bias
  return (
    <div className="kpis">
      <Kpi label="MAE" value={f.mw(m.mae)} hint="Typical miss in any hour, in either direction." />
      <Kpi
        label="Bias"
        value={
          bias == null ? f.DASH : (
            <>
              {f.signedMw(bias)} <small>{bias >= 0 ? 'over-forecast' : 'under-forecast'}</small>
            </>
          )
        }
        hint={
          bias == null
            ? 'Average signed error.'
            : `On average the forecast was ${f.mw(Math.abs(bias))} ${bias >= 0 ? 'too high' : 'too low'}.`
        }
      />
      <Kpi
        label="P10 / P90 error"
        value={`${f.signedMw(m.p10)} / ${f.signedMw(m.p90)}`}
        hint={
          m.p10 == null || m.p90 == null
            ? '80% of hourly errors fall in this range.'
            : `In 80% of hours the outcome was between ${offset(-m.p90)} and ${offset(-m.p10)} the forecast — a guide for buffer size.`
        }
      />
      <Kpi label="WAPE" value={f.pct(m.wape)} hint="Total absolute error as a share of total wind output." />
      <Kpi label="RMSE" value={f.mw(m.rmse)} hint="Like MAE, but big misses count for more." />
      <Kpi label="Hours scored" value={f.count(m.n)} hint="Hourly slots with both a forecast and an actual." />
    </div>
  )
}

function ByHourChart({ data }: { data: AccuracyResponse }) {
  const rows = data.byHour.map((h) => ({ ...h, label: String(h.hour).padStart(2, '0') }))
  return (
    <Card title="Error by hour of day" subtitle="Average miss (bars) and bias (line) for each delivery hour, UK time.">
      <div className="chart">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="label" {...axis} interval={2} />
            <YAxis {...yGw(rows.flatMap((r) => [r.mae, r.bias]))} />
            <ReferenceLine y={0} stroke={C.axis} />
            <Tooltip formatter={mwTooltip} labelFormatter={(l) => `${l}:00`} />
            <Bar dataKey="mae" name="MAE" fill={C.mae} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            <Line dataKey="bias" name="Bias" stroke={C.bias} strokeWidth={2} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <Legend items={[{ label: 'MAE', color: C.mae, kind: 'bar' }, { label: 'Bias (+ = over-forecast)', color: C.bias }]} />
    </Card>
  )
}

function ByDayChart({ data }: { data: AccuracyResponse }) {
  return (
    <Card title="Error over the period" subtitle="Daily average miss and bias.">
      <div className="chart">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data.byDay} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="date" {...axis} tickFormatter={f.shortDate} minTickGap={24} />
            <YAxis {...yGw(data.byDay.flatMap((r) => [r.mae, r.bias]))} />
            <ReferenceLine y={0} stroke={C.axis} />
            <Tooltip formatter={mwTooltip} labelFormatter={(l) => f.longDate(String(l))} />
            <Line dataKey="mae" name="MAE" stroke={C.mae} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="bias" name="Bias" stroke={C.bias} strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <Legend items={[{ label: 'MAE', color: C.mae }, { label: 'Bias (+ = over-forecast)', color: C.bias }]} />
    </Card>
  )
}

function HistogramChart({ data }: { data: AccuracyResponse }) {
  const bins = data.histogram
  const width = bins.length > 1 ? bins[1].binStartMW - bins[0].binStartMW : 500
  const rows = bins.map((b) => ({ ...b, mid: b.binStartMW + width / 2 }))
  return (
    <Card title="Error distribution" subtitle="How often each size of miss happened. Right of zero: forecast too high; left: too low.">
      <div className="chart">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={1}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="binStartMW" {...axis} tickFormatter={f.gwAxis} minTickGap={16} />
            <YAxis {...axis} width={36} allowDecimals={false} />
            <Tooltip
              formatter={(v: number) => [`${f.count(v)} hours`, 'Count']}
              labelFormatter={(start) => `Error ${f.signedMw(Number(start))} to ${f.signedMw(Number(start) + width)}`}
            />
            <Bar dataKey="count" isAnimationActive={false}>
              {rows.map((r) => (
                <Cell key={r.binStartMW} fill={r.mid >= 0 ? C.over : C.under} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <Legend
        items={[
          { label: 'Under-forecast', color: C.under, kind: 'bar' },
          { label: 'Over-forecast', color: C.over, kind: 'bar' },
        ]}
      />
    </Card>
  )
}

function ByLevelChart({ data }: { data: AccuracyResponse }) {
  const names = { low: 'Low', medium: 'Medium', high: 'High' } as const
  const rows = data.byLevel.map((l) => ({ ...l, label: `${names[l.level]} (${f.gwTick(l.minMW)}–${f.gw(l.maxMW)})` }))
  return (
    <Card title="Error by wind level" subtitle="Hours split into thirds by actual output: does the forecast struggle more on calm or windy hours?">
      <div className="chart">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="label" {...axis} interval={0} fontSize={11} />
            <YAxis {...yGw(rows.flatMap((r) => [r.mae, r.bias]))} />
            <ReferenceLine y={0} stroke={C.axis} />
            <Tooltip formatter={mwTooltip} />
            <Bar dataKey="mae" name="MAE" fill={C.mae} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            <Bar dataKey="bias" name="Bias" fill={C.bias} radius={[3, 3, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <Legend items={[{ label: 'MAE', color: C.mae, kind: 'bar' }, { label: 'Bias (+ = over-forecast)', color: C.bias, kind: 'bar' }]} />
    </Card>
  )
}

function HorizonChart({ data }: { data: HorizonResponse }) {
  const { min, max } = data.cutoffHorizonH
  const rows = data.buckets.map((b) => ({ ...b, used: b.maxH > min && b.minH < max }))
  const used = rows.filter((r) => r.used)
  const usedMae = used.length ? used.reduce((s, r) => s + r.mae, 0) / used.length : null
  const first = rows[0]
  return (
    <>
      <div className="chart">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="bucket" {...axis} interval={0} fontSize={11} />
            <YAxis {...yGw(rows.map((r) => r.mae))} />
            <Tooltip formatter={mwTooltip} labelFormatter={(l) => `Published ${l} ahead`} />
            <Bar dataKey="mae" name="MAE" radius={[3, 3, 0, 0]} isAnimationActive={false}>
              {rows.map((r) => (
                <Cell key={r.bucket} fill={r.used ? C.mae : C.muted} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <Legend
        items={[
          { label: `Your cutoff: ${f.hours(min)} – ${f.hours(max)} ahead`, color: C.mae, kind: 'bar' },
          { label: 'Other lead times', color: C.muted, kind: 'bar' },
        ]}
      />
      <p className="note">
        Earlier decisions mean longer lead times, and longer lead times mean bigger errors.
        {first && usedMae != null && (
          <>
            {' '}
            At your cutoff the forecast misses by about <strong>{f.mw(usedMae)}</strong> on average, versus{' '}
            <strong>{f.mw(first.mae)}</strong> for forecasts made {first.bucket} ahead.
          </>
        )}
      </p>
    </>
  )
}
