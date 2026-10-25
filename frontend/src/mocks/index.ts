// Dev-only mock backend, used when VITE_USE_MOCKS=true (see api.ts). Never bundled otherwise:
// the import in api.ts sits behind a compile-time env check.
//
// Fixtures in ./fixtures were generated for the 09:00 cutoff with plausible GB wind numbers
// (3–19 GW, MAE ≈ 1.2 GW). For other cutoffs we only shift the run used / lead times so the UI
// reacts; the forecast values and accuracy numbers stay the same.
import type { ForecastResponse, HorizonResponse, Meta } from '../api'

const fixtures = import.meta.glob<unknown>('./fixtures/*.json', { import: 'default' })
const load = async <T>(name: string) => (await fixtures[`./fixtures/${name}.json`]()) as T

/** Latest forecast run (UTC "HH:MM") published at or before the cutoff on D-1; January, so UK time = UTC. */
function runForCutoff(runTimes: string[], cutoff: string): { run: string; daysBack: number } {
  const before = runTimes.filter((r) => r <= cutoff)
  return before.length ? { run: before[before.length - 1], daysBack: 1 } : { run: runTimes[runTimes.length - 1], daysBack: 2 }
}

const hoursOf = (hhmm: string) => Number(hhmm.slice(0, 2)) + Number(hhmm.slice(3)) / 60
const shiftDays = (ymd: string, n: number) => new Date(Date.parse(`${ymd}T00:00:00Z`) - n * 86_400_000).toISOString().slice(0, 10)

function delay(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const id = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(id)
      reject(new DOMException('Aborted', 'AbortError'))
    })
  })
}

export async function mockGet(path: string, params: Record<string, string>, signal?: AbortSignal): Promise<unknown> {
  await delay(250 + Math.random() * 250, signal)
  const cutoff = params.cutoff ?? '09:00'
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(cutoff)) throw new Error(`422 Unprocessable Entity – invalid cutoff "${cutoff}"`)
  const meta = await load<Meta>('meta')
  const { run, daysBack } = runForCutoff(meta.runTimesUtc, cutoff)
  // Lead time from the run to 00:00 on the delivery day.
  const leadToMidnight = 24 * daysBack - hoursOf(run)

  switch (path) {
    case '/api/meta':
      return meta
    case '/api/accuracy':
      return { ...(await load<object>('accuracy')), cutoff }
    case '/api/accuracy/horizon': {
      const hz = await load<HorizonResponse>('horizon')
      return { ...hz, cutoffHorizonH: { min: leadToMidnight, max: leadToMidnight + 23 } }
    }
    case '/api/forecast': {
      const all = await load<Record<string, ForecastResponse>>('forecast')
      const day = all[params.date]
      if (!day) throw new Error(`422 Unprocessable Entity – no data for date "${params.date}"`)
      const publishTime = `${shiftDays(day.deliveryDate, daysBack)}T${run}:00Z`
      return {
        ...day,
        cutoffUtc: `${shiftDays(day.deliveryDate, 1)}T${cutoff}:00Z`,
        runsUsed: [...day.runsUsed.slice(0, -1), publishTime],
        points: day.points.map((p, h) => (p.status === 'ok' ? { ...p, publishTime, horizonH: leadToMidnight + h } : p)),
      }
    }
    default:
      throw new Error(`404 Not Found – mock has no route for ${path}`)
  }
}
