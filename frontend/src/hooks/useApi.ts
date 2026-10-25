import { useEffect, useState } from 'react'

export interface ApiState<T> {
  data: T | null
  error: string | null
  loading: boolean
}

/**
 * Runs `load` whenever `key` changes and aborts the previous request.
 * `key` should encode every parameter `load` uses (e.g. "forecast|2026-01-15|09:00").
 * Pass `null` as the key to skip loading. Previous data is kept while the next request loads,
 * so charts don't flash empty when a parameter changes.
 */
export function useApi<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ data: null, error: null, loading: key !== null })

  useEffect(() => {
    if (key === null) return
    const ctrl = new AbortController()
    setState((s) => ({ data: s.data, error: null, loading: true }))
    load(ctrl.signal).then(
      (data) => setState({ data, error: null, loading: false }),
      (err: unknown) => {
        if (ctrl.signal.aborted) return
        setState({ data: null, error: err instanceof Error ? err.message : String(err), loading: false })
      },
    )
    return () => ctrl.abort()
    // `load` is intentionally not a dependency: `key` captures its inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return state
}
