/**
 * Manager pop-up: an employee closed their office-hours session early.
 *
 * Polls every 20 seconds while the page is open. "Restart on their computer"
 * asks the employee's FocusGuard to start the session again within a minute;
 * the employee is asked on their computer as for any start.
 */

import { useCallback, useEffect, useState } from 'react'

import { ApiError, api } from '../../api/client'
import type { FocusAlert } from '../../api/managerTypes'

function clock(value: string): string {
  return new Date(value).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export function AlertsPopup() {
  const [alerts, setAlerts] = useState<FocusAlert[]>([])
  const [busy, setBusy] = useState<number | null>(null)
  const [message, setMessage] = useState<Record<number, string>>({})

  const load = useCallback(async () => {
    try {
      setAlerts(await api.manager.alerts())
    } catch {
      // A failed poll shows nothing new; the next one tries again.
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, 20_000)
    return () => window.clearInterval(timer)
  }, [load])

  async function act(alert: FocusAlert, action: 'restart' | 'dismiss') {
    setBusy(alert.id)
    try {
      const result =
        action === 'restart'
          ? await api.manager.restartAlert(alert.id)
          : await api.manager.dismissAlert(alert.id)
      setMessage((current) => ({ ...current, [alert.id]: result.detail }))
      await load()
    } catch (caught) {
      setMessage((current) => ({
        ...current,
        [alert.id]: caught instanceof ApiError ? caught.message : 'Could not do that.',
      }))
    } finally {
      setBusy(null)
    }
  }

  if (alerts.length === 0) return null

  return (
    <div
      className="fixed right-4 bottom-4 z-40 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-3"
      aria-live="polite"
    >
      {alerts.slice(0, 3).map((alert) => (
        <div
          key={alert.id}
          role="alert"
          className="rounded-2xl border border-[var(--color-focus-low)]/50 bg-[var(--color-surface)] p-4 shadow-[var(--shadow-card)]"
        >
          <p className="text-sm font-semibold">
            {alert.employee_name} closed their focus session
          </p>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            At {clock(alert.created_at)}, during office hours
            {alert.window_end ? ` (until ${clock(alert.window_end)})` : ''}.
          </p>
          {alert.restart_requested_at ? (
            <p className="mt-2 text-xs text-[var(--color-accent)]">
              Restart sent at {clock(alert.restart_requested_at)}; waiting for their computer…
            </p>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy === alert.id}
                onClick={() => void act(alert, 'restart')}
                className="rounded-xl px-3.5 py-1.5 text-sm font-semibold text-[var(--color-on-accent)] disabled:opacity-60"
                style={{ background: 'var(--brand-gradient)' }}
              >
                Restart on their computer
              </button>
              <button
                type="button"
                disabled={busy === alert.id}
                onClick={() => void act(alert, 'dismiss')}
                className="rounded-xl border border-[var(--color-line)] px-3.5 py-1.5 text-sm"
              >
                Dismiss
              </button>
            </div>
          )}
          {message[alert.id] && (
            <p className="mt-2 text-xs text-[var(--color-muted)]">{message[alert.id]}</p>
          )}
        </div>
      ))}
    </div>
  )
}
