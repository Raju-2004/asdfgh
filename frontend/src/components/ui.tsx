import type { ReactNode } from 'react'

export function Kpi({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: 'warn' }) {
  return (
    <div className={`kpi${tone ? ` kpi--${tone}` : ''}`}>
      <div className="kpi__label">{label}</div>
      <div className="kpi__value">{value}</div>
      {hint && <div className="kpi__hint">{hint}</div>}
    </div>
  )
}

export function Card({ title, subtitle, children, wide }: { title: string; subtitle?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <section className={`card${wide ? ' card--wide' : ''}`}>
      <header className="card__head">
        <h2>{title}</h2>
        {subtitle && <p className="card__sub">{subtitle}</p>}
      </header>
      {children}
    </section>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="status" role="status">
      <span className="spinner" aria-hidden /> {label}
    </div>
  )
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="status status--error" role="alert">
      <strong>Couldn't load data.</strong> {message}
    </div>
  )
}

/** Small coloured key used under charts. */
export function Legend({ items }: { items: { label: string; color: string; kind?: 'line' | 'band' | 'bar' }[] }) {
  return (
    <ul className="legend">
      {items.map((i) => (
        <li key={i.label}>
          <span className={`legend__swatch legend__swatch--${i.kind ?? 'line'}`} style={{ background: i.color }} />
          {i.label}
        </li>
      ))}
    </ul>
  )
}
