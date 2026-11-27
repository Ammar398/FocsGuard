/**
 * "This computer": a guided setup, then start and stop - no terminal.
 *
 *   1. Helper      the FocusGuard Helper answers on this computer
 *   2. Connect     this computer is linked to the signed-in account
 *   3. Camera      a camera preview that also personalises eye detection
 *   4. Browser     the FocusGuard browser extension, so websites are counted
 *                  (required for employees, recommended - and skippable - for students)
 *   5. Start       name the session (students) or pick a task (employees)
 *
 * The portal never acts on the computer by itself. It asks the Helper, and the
 * Helper asks the person at the computer in a native Windows dialog, so every
 * button here ends in "approve it on your computer".
 *
 * The camera step can be skipped - sessions then use a sensible default eye
 * threshold - but it is the step that makes eye-closure detection fit *you*,
 * so it is offered, explained, and remembered once done.
 */

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'

import { ApiError, api } from '../api/client'
import {
  HelperError,
  localHelper,
  type AssignedTask,
  type HelperStatus,
} from '../api/helper'
import { useAuth } from '../auth/context'
import { Aperture } from './Aperture'
import { formatDate } from '../lib/format'
import { endTime, formatSchedule } from '../lib/schedule'
import {
  IconCamera,
  IconCheck,
  IconDownload,
  IconGlobe,
  IconLink,
  IconPlay,
  IconShield,
  IconStop,
} from './icons'

const STUDY_CATEGORIES = ['STUDY', 'RESEARCH', 'DOCUMENTATION', 'BACKEND', 'FRONTEND', 'DESIGN', 'OTHER']
const SKIP_CAMERA_KEY = 'focusguard.skipCameraCheck'
const SKIP_EXTENSION_KEY = 'focusguard.skipExtension'

type Busy = null | 'connecting' | 'camera' | 'extension' | 'starting' | 'stopping'
type Run = (kind: Busy, action: () => Promise<string | null>) => Promise<void>

export function ThisComputer({ onSessionChange }: { onSessionChange?: () => void }) {
  const { user } = useAuth()
  const [status, setStatus] = useState<HelperStatus | null | undefined>(undefined)
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [download, setDownload] = useState<{ url: string; app: string } | null>(null)
  const [skippedCamera, setSkippedCamera] = useState(
    () => localStorage.getItem(SKIP_CAMERA_KEY) === '1',
  )
  const [skippedExtension, setSkippedExtension] = useState(
    () => localStorage.getItem(SKIP_EXTENSION_KEY) === '1',
  )

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
    // A cheap loopback GET. It is how the panel notices the camera window
    // closing, a session stopped from the agent window, or a helper just started.
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, 3000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  const run: Run = async (kind, action) => {
    setBusy(kind)
    setError(null)
    setNotice(null)
    setDownload(null)
    try {
      setNotice(await action())
    } catch (caught) {
      if (caught instanceof HelperError && caught.declined) {
        setNotice('Nothing changed - it was declined on the computer.')
      } else {
        if (caught instanceof HelperError && caught.camera?.download_url) {
          setDownload({
            url: caught.camera.download_url,
            app: caught.camera.app ?? 'the camera app',
          })
        }
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

  const connectedToMe = Boolean(status?.enrolled && status.user_id === user?.id)
  const calibrated = Boolean(status?.camera?.calibrated)
  const cameraDone = calibrated || skippedCamera
  const running = Boolean(status?.session.running)
  const employee = Boolean(status && status.organization_id !== null)
  // Employees must add it; students may skip it (and add it later).
  const extensionDone =
    Boolean(status?.extension?.installed) || (!employee && skippedExtension)

  const steps: StepState[] = [
    status ? 'done' : 'current',
    !status ? 'todo' : connectedToMe ? 'done' : 'current',
    !connectedToMe ? 'todo' : cameraDone ? 'done' : 'current',
    !connectedToMe || !cameraDone ? 'todo' : extensionDone ? 'done' : 'current',
    !connectedToMe || !cameraDone || !extensionDone ? 'todo' : running ? 'done' : 'current',
  ]

  return (
    <section className="fg-rise overflow-hidden rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] shadow-[var(--shadow-card)]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-line)] px-5 py-4 sm:px-6">
        <div className="flex items-center gap-3">
          <Aperture size={34} score={running ? 90 : undefined} />
          <div>
            <h2 className="font-semibold tracking-tight">This computer</h2>
            <p className="text-xs text-[var(--color-muted)]">
              {status === undefined
                ? 'Checking…'
                : status === null
                  ? 'FocusGuard is not installed on this computer'
                  : running
                    ? 'Monitoring now'
                    : connectedToMe
                      ? 'Connected to your account'
                      : 'Helper found'}
            </p>
          </div>
        </div>
        <Stepper steps={steps} />
      </header>

      <div className="px-5 py-5 sm:px-6">
        {status === undefined && <p className="text-sm text-[var(--color-muted)]">Checking…</p>}

        {status === null && <SetupHelper onRetry={() => void refresh()} />}

        {status && !connectedToMe && (
          <StepPanel
            icon={<IconLink />}
            title={status.enrolled ? 'Connected to a different account' : 'Connect this computer'}
            body={
              <>
                {status.enrolled
                  ? 'This computer reports to another FocusGuard account. Connect it to yours instead?'
                  : 'Link this computer to your account so its sessions appear here.'}{' '}
                Windows will show you exactly what is and is not collected before anything is
                connected.
              </>
            }
          >
            <PrimaryButton
              icon={<IconLink size={18} />}
              disabled={busy !== null}
              onClick={() =>
                void run('connecting', async () => {
                  const token = await api.enrolMyDevice(deviceName())
                  await localHelper.pair(token.enrollment_token)
                  return 'Connected. Next, check your camera.'
                })
              }
            >
              {busy === 'connecting'
                ? 'Approve it in the window on your computer…'
                : status.enrolled
                  ? 'Connect to my account'
                  : 'Connect this computer'}
            </PrimaryButton>
          </StepPanel>
        )}

        {status && connectedToMe && status.camera.check_running && (
          <StepPanel
            icon={<IconCamera />}
            title="Camera check in progress"
            body="A camera window is open on your computer. Look at the screen and blink normally for the first 12 seconds; after that it shows live analysis for as long as you keep it open. Close the window when you are done - this panel updates by itself."
          >
            <span className="inline-flex items-center gap-2 text-sm text-[var(--color-muted)]">
              <Pulse /> Waiting for the camera window…
            </span>
          </StepPanel>
        )}

        {status && connectedToMe && !status.camera.check_running && !cameraDone && !running && (
          <StepPanel
            icon={<IconCamera />}
            title="Check your camera"
            body={
              <>
                Opens a camera window on your computer showing face points, and spends 12 seconds
                learning how <em>your</em> eyes look when open - so a normal blink is never
                mistaken for closed eyes. It then stays live until you close it. Nothing is
                recorded; the result is one number kept on this computer.
                {status.camera.device_name && (
                  <span className="mt-2 block text-xs">
                    Camera: <strong>{status.camera.device_name}</strong>. If it is a phone camera
                    app (Camo, DroidCam), open that app first.
                  </span>
                )}
              </>
            }
          >
            <div className="flex flex-wrap gap-2">
              <PrimaryButton
                icon={<IconCamera size={18} />}
                disabled={busy !== null}
                onClick={() =>
                  void run('camera', async () => {
                    await localHelper.cameraCheck()
                    return null
                  })
                }
              >
                {busy === 'camera' ? 'Approve it on your computer…' : 'Open camera check'}
              </PrimaryButton>
              <SecondaryButton
                onClick={() => {
                  localStorage.setItem(SKIP_CAMERA_KEY, '1')
                  setSkippedCamera(true)
                }}
              >
                Skip for now
              </SecondaryButton>
            </div>
          </StepPanel>
        )}

        {status &&
          connectedToMe &&
          cameraDone &&
          !extensionDone &&
          !running &&
          !status.camera.check_running && (
            <ExtensionSetup
              status={status}
              employee={employee}
              busy={busy}
              run={run}
              onSkip={() => {
                localStorage.setItem(SKIP_EXTENSION_KEY, '1')
                setSkippedExtension(true)
              }}
            />
          )}

        {status && connectedToMe && running && (
          <LiveSession name={status.session.name} busy={busy} run={run} />
        )}

        {status &&
          connectedToMe &&
          cameraDone &&
          extensionDone &&
          !running &&
          !status.camera.check_running && (
          <div className="space-y-4">
            <CameraLine
              status={status}
              onRecheck={() =>
                void run('camera', async () => {
                  await localHelper.cameraCheck()
                  return null
                })
              }
              disabled={busy !== null}
            />
            <ExtensionLine
              status={status}
              employee={employee}
              onAdd={() => {
                localStorage.removeItem(SKIP_EXTENSION_KEY)
                setSkippedExtension(false)
              }}
            />
            {employee && <ScheduleLine status={status} />}
            {status.organization_id !== null ? (
              <AssignedTasks busy={busy} run={run} />
            ) : (
              <StartStudySession busy={busy} run={run} />
            )}
          </div>
        )}

        {notice && <p className="mt-4 text-sm text-[var(--color-good)]">{notice}</p>}
        {error && (
          <div
            role="alert"
            className="mt-4 rounded-xl bg-[var(--color-focus-low)]/10 px-3 py-2 text-sm text-[var(--color-focus-low)]"
          >
            {error}
            {download && (
              <a
                href={download.url}
                target="_blank"
                rel="noreferrer"
                className="mt-2 block w-fit rounded-lg bg-[var(--color-accent)] px-3 py-1.5 font-semibold text-[var(--color-on-accent)]"
              >
                Download {download.app}
              </a>
            )}
          </div>
        )}

        {status && connectedToMe && status.camera_always_allowed && (
          <p className="mt-4 flex flex-wrap items-center gap-x-2 text-xs text-[var(--color-muted)]">
            Sessions on this computer start without asking first. You chose this on the computer.
            <button
              type="button"
              onClick={() =>
                void run(null, async () => {
                  await localHelper.askEveryTime()
                  return 'This computer will ask before every session again.'
                })
              }
              className="font-medium text-[var(--color-accent)] hover:underline"
            >
              Ask every time
            </button>
          </p>
        )}
      </div>

      <footer className="flex items-center gap-2 border-t border-[var(--color-line)] bg-[var(--color-canvas)]/60 px-5 py-3 text-xs text-[var(--color-muted)] sm:px-6">
        <IconShield size={14} />
        Nothing connects this computer or turns the camera on without your approval on it.
      </footer>
    </section>
  )
}

// --- steps ------------------------------------------------------------------

type StepState = 'done' | 'current' | 'todo'
const STEP_LABELS = ['Helper', 'Connect', 'Camera', 'Browser', 'Start']

function Stepper({ steps }: { steps: StepState[] }) {
  return (
    <ol className="flex items-center gap-1.5" aria-label="Setup progress">
      {steps.map((state, index) => (
        <li key={STEP_LABELS[index]} className="flex items-center gap-1.5">
          <span
            className={`flex h-6 items-center gap-1 rounded-full px-2 text-xs font-medium ${
              state === 'done'
                ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                : state === 'current'
                  ? 'text-[var(--color-on-accent)]'
                  : 'bg-[var(--color-canvas)] text-[var(--color-muted)]'
            }`}
            style={state === 'current' ? { background: 'var(--brand-gradient)' } : undefined}
            aria-current={state === 'current' ? 'step' : undefined}
          >
            {state === 'done' ? <IconCheck size={13} /> : <span>{index + 1}</span>}
            <span className="hidden sm:inline">{STEP_LABELS[index]}</span>
          </span>
          {index < steps.length - 1 && (
            <span className="h-px w-3 bg-[var(--color-line)]" aria-hidden="true" />
          )}
        </li>
      ))}
    </ol>
  )
}

function StepPanel({
  icon,
  title,
  body,
  children,
}: {
  icon: ReactNode
  title: string
  body: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
      <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
        {icon}
      </span>
      <div className="min-w-0 flex-1 space-y-3">
        <div>
          <h3 className="font-semibold tracking-tight">{title}</h3>
          <p className="mt-1 text-sm leading-relaxed text-[var(--color-muted)]">{body}</p>
        </div>
        {children}
      </div>
    </div>
  )
}

function CameraLine({
  status,
  onRecheck,
  disabled,
}: {
  status: HelperStatus
  onRecheck: () => void
  disabled: boolean
}) {
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-muted)]">
      <span className="inline-flex items-center gap-1.5">
        <IconCamera size={14} />
        {status.camera.calibrated && status.camera.calibrated_at
          ? `Eye detection personalised ${formatDate(status.camera.calibrated_at)}`
          : 'Using the default eye threshold'}
      </span>
      <button
        type="button"
        onClick={onRecheck}
        disabled={disabled}
        className="font-medium text-[var(--color-accent)] hover:underline disabled:opacity-50"
      >
        {status.camera.calibrated ? 'Re-run camera check' : 'Run camera check'}
      </button>
    </p>
  )
}

// --- browser extension -------------------------------------------------------

function currentBrowser(): 'chrome' | 'edge' {
  return navigator.userAgent.includes('Edg/') ? 'edge' : 'chrome'
}

function ExtensionSetup({
  status,
  employee,
  busy,
  run,
  onSkip,
}: {
  status: HelperStatus
  employee: boolean
  busy: Busy
  run: Run
  onSkip: () => void
}) {
  const browser = currentBrowser()
  const browserName = browser === 'edge' ? 'Edge' : 'Chrome'
  const page = browser === 'edge' ? 'edge://extensions' : 'chrome://extensions'
  const [opened, setOpened] = useState(false)
  const [copied, setCopied] = useState(false)
  const folder = status.extension?.folder ?? null
  // A Helper installed before the extension existed does not report it.
  const outdated = status.extension === undefined || folder === null

  return (
    <StepPanel
      icon={<IconGlobe />}
      title={
        employee
          ? 'Add the browser extension (required by your organisation)'
          : 'Add the browser extension (recommended)'
      }
      body={
        employee ? (
          <>
            Your organisation counts which websites your work happens on, so sessions on this
            computer start only once the FocusGuard extension is added. Without it, FocusGuard
            sees only that {browserName} is open, not whether it is your project tool or a
            video site.
          </>
        ) : (
          <>
            Without it, FocusGuard sees only that {browserName} is open: a lecture,
            documentation and social media all look the same, so browsing time cannot be
            judged. With it, the sites you mark as distracting are really counted and your
            report shows where your study time went. You can skip this and add it later.
          </>
        )
      }
    >
      <div className="space-y-4 text-sm">
        <ExtensionPrivacy />

        {outdated ? (
          <div className="space-y-2">
            <p>
              FocusGuard on this computer is older than the extension. Download it again and run
              "Install FocusGuard" (your settings are kept), then come back here.
            </p>
            <a
              href={INSTALLER_URL}
              download
              className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold whitespace-nowrap text-[var(--color-on-accent)] shadow-[var(--shadow-card)] transition hover:brightness-110"
              style={{ background: 'var(--brand-gradient)' }}
            >
              <IconDownload size={18} />
              Download FocusGuard again
            </a>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              <PrimaryButton
                icon={<IconGlobe size={18} />}
                disabled={busy !== null}
                onClick={() =>
                  void run('extension', async () => {
                    await localHelper.setupExtension(browser)
                    setOpened(true)
                    return null
                  })
                }
              >
                {busy === 'extension' ? 'Opening…' : `Add to ${browserName}`}
              </PrimaryButton>
              {!employee && <SecondaryButton onClick={onSkip}>Skip for now</SecondaryButton>}
            </div>

            <ol className="space-y-2">
              {[
                `The button opens ${browserName}'s extensions page and the extension's folder. (If the page does not open, type ${page} in the address bar.)`,
                'Turn on Developer mode (top right of the extensions page).',
                'Click Load unpacked. The folder path is already copied: paste it (Ctrl+V) into the folder box and click Select Folder.',
                'Done. This page notices the extension by itself within 15 seconds.',
              ].map((text, index) => (
                <li key={text} className="flex gap-3">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent-soft)] text-xs font-semibold text-[var(--color-accent)]">
                    {index + 1}
                  </span>
                  <span className="pt-0.5 leading-relaxed">{text}</span>
                </li>
              ))}
            </ol>

            <p className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
              Folder:
              <code className="rounded bg-[var(--color-canvas)] px-1.5 py-0.5 break-all">
                {folder}
              </code>
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard?.writeText(folder ?? '').then(() => setCopied(true))
                }}
                className="font-medium text-[var(--color-accent)] hover:underline"
              >
                {copied ? 'Copied' : 'Copy'}
              </button>
            </p>

            {opened && (
              <p className="flex items-center gap-2 rounded-xl bg-[var(--color-accent-soft)] px-3 py-2 text-xs">
                <Pulse />
                Waiting for the extension to check in…
              </p>
            )}
          </>
        )}
      </div>
    </StepPanel>
  )
}

/** What the extension does and does not do, in plain words. */
function ExtensionPrivacy() {
  return (
    <div className="grid gap-3 rounded-xl border border-[var(--color-line)] bg-[var(--color-canvas)]/60 p-3.5 text-xs leading-relaxed sm:grid-cols-2">
      <div>
        <p className="font-semibold text-[var(--color-ink)]">What it does</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[var(--color-muted)]">
          <li>
            Tells FocusGuard <em>on this computer</em> the website of the tab you are looking at:
            the domain and one path part, e.g. <code>youtube.com/watch</code>
          </li>
          <li>Only during a focus session; FocusGuard then counts it as work, neutral or distracting</li>
          <li>A Pause button in the extension stops it at any time</li>
        </ul>
      </div>
      <div>
        <p className="font-semibold text-[var(--color-ink)]">What it never does</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[var(--color-muted)]">
          <li>Send full addresses, searches or anything after a "?"</li>
          <li>Read page content, passwords, forms or cookies</li>
          <li>Look at background tabs or your history</li>
          <li>Send anything to the internet itself</li>
        </ul>
      </div>
    </div>
  )
}

function ExtensionLine({
  status,
  employee,
  onAdd,
}: {
  status: HelperStatus
  employee: boolean
  onAdd: () => void
}) {
  const extension = status.extension
  const browserName = extension?.browser === 'edge' ? 'Edge' : 'Chrome'
  let text: string
  let warn = false
  if (!extension?.installed) {
    text = 'Browser extension not added: websites are not counted'
  } else if (extension.enabled === false) {
    text = `Browser extension paused in ${browserName}: websites are not counted`
    warn = true
  } else if (extension.connected) {
    text = `Browser extension active in ${browserName}`
  } else {
    text = `Browser extension added (${browserName} is closed right now)`
  }
  return (
    <p
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 text-xs ${
        warn ? 'text-[var(--color-focus-low)]' : 'text-[var(--color-muted)]'
      }`}
    >
      <span className="inline-flex items-center gap-1.5">
        <IconGlobe size={14} />
        {text}
        {warn && employee ? ' - resume it from the FocusGuard button in the toolbar.' : ''}
      </span>
      {!extension?.installed && (
        <button
          type="button"
          onClick={onAdd}
          className="font-medium text-[var(--color-accent)] hover:underline"
        >
          Add it
        </button>
      )}
    </p>
  )
}

function formatLocal(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Employees: when the next scheduled session starts by itself. */
function ScheduleLine({ status }: { status: HelperStatus }) {
  const next = status.schedule?.next
  const last = status.schedule?.last
  if (!next && !(last && !last.ok)) return null
  return (
    <div className="space-y-1 text-xs text-[var(--color-muted)]">
      {next && (
        <p className="flex flex-wrap items-center gap-1.5">
          <IconPlay size={14} />
          {next.now ? 'Scheduled now' : 'Next scheduled'}: <strong>{next.title}</strong>,{' '}
          {formatLocal(next.starts_at)} to {next.ends_at.slice(11, 16)}. It starts by itself on
          this computer; close its window to stop early.
        </p>
      )}
      {last && !last.ok && (
        <p className="text-[var(--color-focus-low)]">
          The scheduled start of "{last.title}" at {formatLocal(last.at)} did not happen:{' '}
          {last.error}
        </p>
      )}
    </div>
  )
}

// --- running ----------------------------------------------------------------

function LiveSession({ name, busy, run }: { name: string | null; busy: Busy; run: Run }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-[var(--color-accent)]/50 bg-[var(--color-accent-soft)] p-5">
      <div>
        <p className="flex items-center gap-2 text-sm text-[var(--color-ink-soft)]">
          <Pulse /> Monitoring now
        </p>
        <p className="mt-1 text-xl font-semibold [font-stretch:112%]">{name}</p>
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          The FocusGuard window on your computer shows exactly what is being measured.
        </p>
      </div>
      <button
        type="button"
        disabled={busy !== null}
        onClick={() =>
          void run('stopping', async () => {
            await localHelper.stop()
            return 'Stopping. The session uploads and appears below in a moment.'
          })
        }
        className="flex items-center gap-2 rounded-xl border border-[var(--color-ink)]/30 px-4 py-2.5 text-sm font-semibold transition hover:border-[var(--color-ink)] disabled:opacity-60"
      >
        <IconStop size={18} />
        {busy === 'stopping' ? 'Stopping' : 'Stop session'}
      </button>
    </div>
  )
}

// --- starting ----------------------------------------------------------------

function StartStudySession({ busy, run }: { busy: Busy; run: Run }) {
  const [name, setName] = useState('')
  const [category, setCategory] = useState('STUDY')
  const [distracting, setDistracting] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    void run('starting', async () => {
      const sessionName = name.trim() || 'Study session'
      const result = await localHelper.start({
        name: sessionName,
        task: sessionName,
        task_category: category,
        distracting: distracting
          .split(/[,\s]+/)
          .map((item) => item.trim())
          .filter(Boolean),
      })
      const opened = result.camera?.opened_app ? `${result.camera.message} ` : ''
      return `${opened}Started. A live analysis window opened on your computer; the session runs until you close it or press Stop here.`
    })
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
        <TextInput
          label="What are you working on?"
          value={name}
          onChange={setName}
          placeholder="Study Data Structures"
        />
        <label className="block">
          <span className="text-sm font-medium">Kind of work</span>
          <select
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            className="mt-1.5 w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2.5 text-sm"
          >
            {STUDY_CATEGORIES.map((value) => (
              <option key={value} value={value}>
                {value.charAt(0) + value.slice(1).toLowerCase()}
              </option>
            ))}
          </select>
        </label>
      </div>
      <TextInput
        label="Sites that distract you (optional)"
        value={distracting}
        onChange={setDistracting}
        placeholder="youtube.com, instagram.com"
        hint="Counted as distracting for this session only. YouTube stays neutral unless you list it - it is also where lectures live."
      />
      <PrimaryButton type="submit" icon={<IconPlay size={18} />} disabled={busy !== null}>
        {busy === 'starting' ? 'Approve it on your computer…' : 'Start session'}
      </PrimaryButton>
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
    <div>
      <h3 className="mb-2 text-sm font-semibold">Your assigned tasks</h3>
      <ul className="space-y-2">
        {tasks.map((task) => (
          <li
            key={task.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--color-line)] p-3.5 transition hover:border-[var(--color-accent)]/50"
          >
            <div className="min-w-0">
              <p className="font-medium">{task.title}</p>
              <p className="text-xs text-[var(--color-muted)]">
                {task.category.toLowerCase()}
                {task.project_name ? `, ${task.project_name}` : ''}
                {task.expected_minutes ? `, about ${task.expected_minutes} min` : ''}
              </p>
              {task.schedule_time && task.schedule_days && task.schedule_minutes ? (
                <p className="mt-1 text-xs text-[var(--color-accent)]">
                  Starts by itself{' '}
                  {formatSchedule({
                    days: task.schedule_days,
                    start: task.schedule_time,
                    minutes: task.schedule_minutes,
                  })}{' '}
                  (until {endTime(task.schedule_time, task.schedule_minutes)})
                </p>
              ) : null}
              {(task.productive_rules.length > 0 || task.distracting_rules.length > 0) && (
                <p className="mt-1 text-xs text-[var(--color-muted)]">
                  {task.productive_rules.length > 0 && (
                    <>Counts as work: {task.productive_rules.join(', ')}. </>
                  )}
                  {task.distracting_rules.length > 0 && (
                    <>Counts as distracting: {task.distracting_rules.join(', ')}.</>
                  )}
                </p>
              )}
            </div>
            <PrimaryButton
              icon={<IconPlay size={16} />}
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
    </div>
  )
}

// --- setup ------------------------------------------------------------------

/** Served by the FocusGuard server, with that server's address built in. */
const INSTALLER_URL = '/api/v1/downloads/FocusGuard-Setup.zip'

function SetupHelper({ onRetry }: { onRetry: () => void }) {
  const windows = navigator.userAgent.includes('Windows')
  const [downloaded, setDownloaded] = useState(false)

  return (
    <StepPanel
      icon={<IconDownload />}
      title="Install FocusGuard on this computer"
      body="FocusGuard analyses your camera on your own computer, so video never leaves it. A website cannot do that by itself, so this computer needs the FocusGuard app once. After that, everything starts from this page."
    >
      {windows ? (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-center gap-3">
            <a
              href={INSTALLER_URL}
              download
              onClick={() => setDownloaded(true)}
              className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold whitespace-nowrap text-[var(--color-on-accent)] shadow-[var(--shadow-card)] transition hover:brightness-110"
              style={{ background: 'var(--brand-gradient)' }}
            >
              <IconDownload size={18} />
              Download FocusGuard for Windows
            </a>
            <span className="text-xs text-[var(--color-muted)]">about 12 MB</span>
          </div>

          <ol className="space-y-2">
            {[
              'Open the downloaded FocusGuard-Setup.zip and choose Extract all.',
              'In the extracted folder, double-click "Install FocusGuard". The first time takes 5 to 10 minutes.',
              'When it says "FocusGuard is ready", come back here. This page notices by itself.',
            ].map((text, index) => (
              <li key={text} className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent-soft)] text-xs font-semibold text-[var(--color-accent)]">
                  {index + 1}
                </span>
                <span className="pt-0.5 leading-relaxed">{text}</span>
              </li>
            ))}
          </ol>

          {downloaded && (
            <p className="flex items-center gap-2 rounded-xl bg-[var(--color-accent-soft)] px-3 py-2 text-xs">
              <Pulse />
              Waiting for FocusGuard to start on this computer…
            </p>
          )}

          <p className="text-xs text-[var(--color-muted)]">
            Windows may warn that the file comes from the internet. Choose More info, then Run anyway.
            Nothing turns your camera on without your approval in a Windows dialog.
          </p>

          <details className="text-xs text-[var(--color-muted)]">
            <summary className="cursor-pointer">Already installed?</summary>
            <p className="mt-2">
              FocusGuard starts by itself when you sign in to Windows. If it is not running, open
              the FocusGuard folder and double-click "Install FocusGuard" again, then:
            </p>
            <div className="mt-2">
              <SecondaryButton onClick={onRetry}>Check again</SecondaryButton>
            </div>
          </details>
        </div>
      ) : (
        <p className="text-sm text-[var(--color-muted)]">
          FocusGuard monitoring runs on Windows. Open this page on the Windows computer you want
          to monitor to download it. Your reports work on any device.
        </p>
      )}
    </StepPanel>
  )
}

// --- small pieces ------------------------------------------------------------

function Pulse({ light = false }: { light?: boolean }) {
  const color = light ? '#ffffff' : 'var(--color-accent)'
  return (
    <span className="relative flex h-2.5 w-2.5" aria-hidden="true">
      <span
        className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
        style={{ backgroundColor: color }}
      />
      <span className="relative inline-flex h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
    </span>
  )
}

function TextInput({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  hint?: string
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        maxLength={500}
        className="mt-1.5 w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-3.5 py-2.5 text-sm outline-none transition focus:border-[var(--color-accent)] focus:ring-4 focus:ring-[var(--color-accent)]/15"
      />
      {hint && <span className="mt-1 block text-xs text-[var(--color-muted)]">{hint}</span>}
    </label>
  )
}

function PrimaryButton({
  children,
  onClick,
  disabled,
  icon,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  icon?: ReactNode
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold whitespace-nowrap text-[var(--color-on-accent)] shadow-[var(--shadow-card)] transition hover:brightness-110 disabled:opacity-60"
      style={{ background: 'var(--brand-gradient)' }}
    >
      {icon}
      {children}
    </button>
  )
}

function SecondaryButton({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-xl border border-[var(--color-line)] px-4 py-2.5 text-sm font-medium transition hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
    >
      {children}
    </button>
  )
}

function deviceName(): string {
  const platform = navigator.userAgent.includes('Windows') ? 'Windows PC' : 'Computer'
  return `${platform} (connected ${new Date().toLocaleDateString()})`
}
