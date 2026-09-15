/**
 * Re-fetch on a timer, for the live parts of the dashboard.
 *
 * WHY POLLING AND NOT A WEBSOCKET
 * -------------------------------
 * The agent uploads in batches roughly every thirty seconds (see
 * `docs/synchronization.md`). A WebSocket would deliver *that* data no sooner -
 * it would just hold a connection open waiting for it. The latency floor is set
 * by the agent's upload interval, not by the transport, so a socket would add a
 * connection lifecycle, reconnection logic and a second code path on the server
 * to gain nothing a student could perceive.
 *
 * When the agent gains a live push channel, this hook is the single place the
 * dashboard subscribes to data, so replacing it is contained.
 *
 * POLLING STOPS WHEN THE TAB IS HIDDEN
 * ------------------------------------
 * A dashboard left open in a background tab for eight hours would otherwise make
 * a thousand pointless requests, on a laptop the user is trying to get work done
 * on. It refreshes immediately when the tab comes back, so returning to it never
 * shows stale numbers.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

interface PollingState<T> {
  data: T | null
  error: string | null
  loading: boolean
  refresh: () => void
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number | null,
  dependencies: unknown[] = [],
): PollingState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // Held in a ref so changing the interval does not re-run the fetch effect and
  // re-trigger a load on every render.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const [tick, setTick] = useState(0)
  const refresh = useCallback(() => setTick((value) => value + 1), [])

  useEffect(() => {
    let cancelled = false

    fetcherRef
      .current()
      .then((result) => {
        if (cancelled) return
        setData(result)
        setError(null)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        // The previous data is deliberately kept. A dashboard that blanks out
        // because one poll failed is worse than one showing slightly old
        // numbers with a warning.
        setError(caught instanceof Error ? caught.message : 'Could not load data')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...dependencies])

  useEffect(() => {
    if (intervalMs === null) return

    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') refresh()
    }, intervalMs)

    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [intervalMs, refresh])

  return { data, error, loading, refresh }
}
