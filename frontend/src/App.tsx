import { useState } from 'react'
import { fetchMeta } from './api'
import { ErrorBox, Loading } from './components/ui'
import { useApi } from './hooks/useApi'
import Accuracy from './pages/Accuracy'
import DayAhead from './pages/DayAhead'

const CUTOFFS = ['06:00', '08:00', '09:00', '10:00', '12:00', '16:00']
const TABS = [
  { id: 'day', label: 'Day-ahead view' },
  { id: 'accuracy', label: 'Forecast accuracy' },
] as const
type TabId = (typeof TABS)[number]['id']

export default function App() {
  const meta = useApi('meta', fetchMeta)
  const [cutoff, setCutoff] = useState('09:00')
  const [tab, setTab] = useState<TabId>('day')

  return (
    <div className="app">
      <header className="header">
        <div className="header__inner">
          <div className="brand">
            <svg className="brand__mark" viewBox="0 0 24 24" aria-hidden>
              <path d="M3 8h11a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <div>
              <div className="brand__name">Wind Forecast Monitor</div>
              <div className="brand__tag">GB wind · day-ahead purchasing</div>
            </div>
          </div>
          <label className="field field--inline">
            <span>Decision cutoff (D-1, UK time)</span>
            <select value={cutoff} onChange={(e) => setCutoff(e.target.value)}>
              {CUTOFFS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        </div>
        <nav className="tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`tab${tab === t.id ? ' tab--active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="main">
        {meta.error && <ErrorBox message={meta.error} />}
        {!meta.data && meta.loading && <Loading label="Loading data range…" />}
        {meta.data &&
          (tab === 'day' ? <DayAhead meta={meta.data} cutoff={cutoff} /> : <Accuracy meta={meta.data} cutoff={cutoff} />)}
      </main>

      <footer className="footer">Data: Elexon BMRS (FUELHH WIND, WINDFOR). Times shown in UK time (= UTC in January).</footer>
    </div>
  )
}
