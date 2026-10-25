// Number and time formatting. All times are displayed in UK time (Europe/London).

const TZ = 'Europe/London'

const int = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 })
const oneDp = new Intl.NumberFormat('en-GB', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

const timeFmt = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false })
const dayMonthFmt = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, day: 'numeric', month: 'short' })
const longDayFmt = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })

const isNum = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)

export const DASH = '–'

/** 12,345 MW */
export const mw = (v: number | null | undefined) => (isNum(v) ? `${int.format(v)} MW` : DASH)

/** +1,234 MW / −567 MW */
export const signedMw = (v: number | null | undefined) =>
  isNum(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${int.format(Math.abs(v))} MW` : DASH

/** 12.3 GW */
export const gw = (v: number | null | undefined) => (isNum(v) ? `${oneDp.format(v / 1000)} GW` : DASH)

/** 245,300 MWh */
export const mwh = (v: number | null | undefined) => (isNum(v) ? `${int.format(v)} MWh` : DASH)

/** 0.118 -> 11.8% */
export const pct = (fraction: number | null | undefined) => (isNum(fraction) ? `${oneDp.format(fraction * 100)}%` : DASH)

export const hours = (v: number | null | undefined) => (isNum(v) ? `${oneDp.format(v)} h` : DASH)

export const count = (v: number | null | undefined) => (isNum(v) ? int.format(v) : DASH)

/** MW value as a bare GW number: 12.3 */
export const gwTick = (v: number) => `${oneDp.format(v / 1000)}`

/** Axis tick for MW values: "12 GW", "2.5 GW", "−1 GW" */
const axisNum = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 })
export const gwAxis = (v: number) => `${axisNum.format(v / 1000).replace('-', '−')} GW`

/** Round-number MW axis ticks (multiples of 0.5/1/2/4/5/10 GW) covering `values` and zero. */
export function gwTicks(values: (number | null | undefined)[]): number[] {
  const vs = values.filter(isNum)
  vs.push(0)
  const lo = Math.min(...vs)
  const hi = Math.max(...vs)
  const step = [500, 1000, 2000, 4000, 5000].find((s) => (hi - lo) / s <= 6) ?? 10000
  const ticks: number[] = []
  for (let t = Math.floor(lo / step) * step; t < hi + step; t += step) ticks.push(t)
  return ticks
}

/** "07:30" in UK time */
export const time = (iso: string | number | null | undefined) => (iso == null ? DASH : timeFmt.format(new Date(iso)))

/** "14 Jan" in UK time */
export const dayMonth = (iso: string | number | null | undefined) => (iso == null ? DASH : dayMonthFmt.format(new Date(iso)))

/** "07:30, 14 Jan" in UK time */
export const timeAndDay = (iso: string | null | undefined) => (iso == null ? DASH : `${time(iso)}, ${dayMonth(iso)}`)

/** "Thu 15 Jan 2026" for a "YYYY-MM-DD" delivery date (midday avoids any timezone edge). */
export const longDate = (ymd: string) => longDayFmt.format(new Date(`${ymd}T12:00:00Z`))

/** "15 Jan" for a "YYYY-MM-DD" date */
export const shortDate = (ymd: string) => dayMonthFmt.format(new Date(`${ymd}T12:00:00Z`))
