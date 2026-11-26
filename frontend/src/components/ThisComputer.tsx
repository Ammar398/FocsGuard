/**
 * "This computer": connect it and start or stop sessions without a terminal.
 *
 * The portal asks the FocusGuard Helper on this machine; the helper asks the
 * person at the machine in a native Windows dialog. So every button here ends
 * in "approve it on your computer", never in something happening silently.
 *
 * States, in the order a new user meets them:
 *   helper not found  -> the one-time setup (the only command anyone types)
 *   not connected     -> "Connect this computer"
 *   connected         -> start a session (students: name it; employees: pick a task)
 *   running           -> what is being monitored, and Stop
 */

import { useCallback, useEffect, useState, type FormEvent } from 'react'

import { ApiError, api } from '../api/client'
import {
  HelperError,
  localHelper,
  type AssignedTask,
  type HelperStatus,
} from '../api/helper'
import { useAuth } from '../auth/context'
import { Card } from './StatCard'

const STUDY_CATEGORIES = ['STUDY', 'RESEARCH', 'DOCUMENTATION', 'BACKEND', 'FRONTEND', 'DESIGN', 'OTHER']

type Busy = null | 'connecting' | 'starting' | 'stopping'

export function ThisComputer({ onSessionChange }: { onSessionChange?: () => void }) {
  const { user } = useAuth()
  const [status, setStatus] = useState<HelperStatus | null | undefined>(undefined)
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setStatus(await localHelper.status())
  }, [])

  useEffect(() => {
    let alive = true
    const load = () =>
      localHelper.status().then((next) => {
        if (alive) setStatus(next)
      })
    void load()
    // Cheap (a loopback GET), and it is how the panel notices a session the
    // user stopped from the agent window, or a helper they just started.
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, 5000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  async function run(kind: Busy, action: () => Promise<string | null>) {
    setBusy(kind)
    setError(null)
    setNotice(null)
    try {
      setNotice(await action())
    } catch (caught) {
      if (caught instanceof HelperError && caught.declined) {
        setNotice('Nothing was changed - it was declined on the computer.')
      } else {
        setError(
          caught instanceof HelperError || caught instanceof ApiError
            ? caught.message
            : 'Something went wrong.',
        )
      }
    } finally {
      setBusy(null)
      await refresh()
      onSessionChange?.()
    }
  }

  const connect = () =>
    run('connecting', async () => {
      const token = await api.enrolMyDevice(guessDeviceName())
      await localHelper.pair(token.enrollment_token)
      return 'This computer is connected. Sessions will now appear here.'
    })

  const connectedToMe = status?.enrolled && status.user_id === user?.id

  return (
    <Card title="This computer">
      {status === undefined && <p className="text-sm text-[var(--color-muted)]">Checking…</p>}

      {status === null && <SetupHelper onRetry={() => void refresh()} />}

      {status && !connectedToMe && (
        <div className="space-y-3">
          <p className="text-sm text-[var(--color-muted)]">
            {status.enrolled
              ? 'This computer is connected to a different FocusGuard account.'
              : 'Connect this computer to your account so its sessions appear here.'}{' '}
            You will be asked to approve it on the computer, with a full list of what is and
            is not collected.
          </p>
          <PrimaryButton onClick={() => void connect()} disabled={busy !== null}>
            {busy === 'connecting'
              ? 'Approve it in the FocusGuard window…'
              : status.enrolled
                ? 'Connect to my account instead'
                : 'Connect this computer'}
          </PrimaryButton>
        </div>
      )}

      {status && connectedToMe && status.session.running && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="flex items-center gap-2 font-medium">
              <span className="relative flex h-2.5 w-2.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--color-focus-high)] opacity-60" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-[var(--color-focus-high)]" />
              </span>
              Monitoring: {status.session.name}
            </p>
            <p className="mt-1 text-xs text-[var(--color-muted)]">
              The FocusGuard window on this computer shows exactly what is being measured.
            </p>
          </div>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() =>
              void run('stopping', async () => {
                await localHelper.stop()
                return 'Stopping… the session will upload and appear below in a moment.'
              })
            }
            className="rounded-lg border border-[var(--color-focus-low)]/40 px-4 py-2 text-sm font-medium text-[var(--color-focus-low)] hover:bg-[var(--color-focus-low)]/10 disabled:opacity-50"
          >
            {busy === 'stopping' ? 'Stopping…' : 'Stop session'}
          </button>
        </div>
      )}

      {status && connectedToMe && !status.session.running && (
        status.organization_id !== null ? (
          <AssignedTasks busy={busy} run={run} />
        ) : (
          <StartStudySession busy={busy} run={run} />
        )
      )}

      {notice && <p className="mt-3 text-sm text-[var(--color-focus-high)]">{notice}</p>}
      {error && (
        <p role="alert" className="mt-3 text-sm text-[var(--color-focus-low)]">
          {error}
        </p>
      )}
    </Card>
  )
}

type Run = (kind: Busy, action: () => Promise<string | null>) => Promise<void>

function StartStudySession({ busy, run }: { busy: Busy; run: Run }) {
  const [name, setName] = useState('')
  const [category, setCategory] = useState('STUDY')
  const [distracting, setDistracting] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    void run('starting', async () => {
      const sessionName = name.trim() || 'Study session'
      await localHelper.start({
        name: sessionName,
        task: sessionName,
        task_category: category,
        distracting: distracting
          .split(/[,\s]+/)
          .map((item) => item.trim())
          .filter(Boolean),
      })
      return 'Started. You can keep this page open or close it.'
    })
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
      <label className="block">
        <span className="text-sm font-medium">What are you working on?</span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Study Data Structures"
          maxLength={200}
          className="mt-1 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-canvas)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
        />
      </label>
      <label className="block">
        <span className="text-sm font-medium">Kind of work</span>
        <select
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          className="mt-1 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-canvas)] px-3 py-2 text-sm"
        >
          {STUDY_CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {value.charAt(0) + value.slice(1).toLowerCase()}
            </option>
          ))}
        </select>
      </label>
      <PrimaryButton type="submit" disabled={busy !== null}>
        {busy === 'starting' ? 'Approve it on your computer…' : 'Start session'}
      </PrimaryButton>
      <label className="block sm:col-span-3">
        <span className="text-sm font-medium">Sites that distract you (optional)</span>
        <input
          value={distracting}
          onChange={(event) => setDistracting(event.target.value)}
          placeholder="youtube.com, instagram.com"
          maxLength={500}
          className="mt-1 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-canvas)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
        />
        <span className="mt-1 block text-xs text-[var(--color-muted)]">
          Counted as distracting for this session only. YouTube is neutral unless you list it — it
          is also where lectures live.
        </span>
      </label>
    </form>
  )
}

function AssignedTasks({ busy, run }: { busy: Busy; run: Run }) {
  const [tasks, setTasks] = useState<AssignedTask[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    localHelper
      .tasks()
      .then(setTasks)
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : 'Could not load your tasks.'),
      )
  }, [])

  if (error) return <p className="text-sm text-[var(--color-focus-low)]">{error}</p>
  if (tasks === null) return <p className="text-sm text-[var(--color-muted)]">Loading your tasks…</p>
  if (tasks.length === 0) {
    return <p className="text-sm text-[var(--color-muted)]">You have no open assigned tasks.</p>
  }

  return (
    <ul className="divide-y divide-[var(--color-line)]">
      {tasks.map((task) => (
        <li key={task.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
          <div className="min-w-0">
            <p className="font-medium">{task.title}</p>
            <p className="text-xs text-[var(--color-muted)]">
              {task.category.toLowerCase()}
              {task.project_name ? ` · ${task.project_name}` : ''}
              {task.expected_minutes ? ` · ~${task.expected_minutes} min` : ''}
            </p>
            {(task.productive_rules.length > 0 || task.distracting_rules.length > 0) && (
              <p className="mt-1 text-xs text-[var(--color-muted)]">
                {task.productive_rules.length > 0 && <>Counts as work: {task.productive_rules.join(', ')}. </>}
                {task.distracting_rules.length > 0 && <>Counts as distracting: {task.distracting_rules.join(', ')}.</>}
              </p>
            )}
          </div>
          <PrimaryButton
            disabled={busy !== null}
            onClick={() =>
              void run('starting', async () => {
                await localHelper.start({ assigned_task_id: task.id })
                return `Started "${task.title}".`
              })
            }
          >
            {busy === 'starting' ? 'Approve on your computer…' : 'Start'}
          </PrimaryButton>
        </li>
      ))}
    </ul>
  )
}

function SetupHelper({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="space-y-3 text-sm">
      <p>
        To connect this computer with one click, the <strong>FocusGuard Helper</strong> needs to
        be running on it. A web page cannot start programs or your webcam by itself, which is
        how it should be.
      </p>
      <p className="text-[var(--color-muted)]">
        One-time setup — run this once in the FocusGuard folder. It also starts the helper
        whenever you sign in to Windows:
      </p>
      <code className="block overflow-x-auto rounded-lg bg-[var(--color-canvas)] p-3 text-xs whitespace-nowrap">
        python -m focusguard_agent.helper --install-startup
      </code>
      <button
        type="button"
        onClick={onRetry}
        className="rounded-lg border border-[var(--color-line)] px-3 py-1.5 font-medium hover:border-[var(--color-accent)]"
      >
        Check again
      </button>
    </div>
  )
}

function PrimaryButton({
  children,
  onClick,
  disabled,
  type = 'button',
}: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-medium whitespace-nowrap text-white transition hover:opacity-90 disabled:opacity-60"
    >
      {children}
    </button>
  )
}

function guessDeviceName(): string {
  const platform = navigator.userAgent.includes('Windows') ? 'Windows PC' : 'Computer'
  return `${platform} (connected ${new Date().toLocaleDateString()})`
}
