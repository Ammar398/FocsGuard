/**
 * Talks to the FocusGuard Helper running on *this* computer
 * (`desktop_agent/focusguard_agent/helper`).
 *
 * The helper only answers pages from the portal's own origin, and anything that
 * connects the computer or turns the webcam on is confirmed by the person at the
 * computer in a native Windows dialog. So every call here may take as long as
 * that person takes to click - the requests that ask have no short timeout.
 */

const HELPER_URL = 'http://127.0.0.1:48214'

export interface HelperStatus {
  service: string
  version: string
  enrolled: boolean
  user_id: number | null
  organization_id: number | null
  backend_url: string
  session: { running: boolean; name: string | null }
  /** Set only in the on-computer dialog ("Always allow on this computer"). */
  camera_always_allowed: boolean
  camera: {
    device_name: string | null
    check_running: boolean
    calibrated: boolean
    calibrated_at: string | null
  }
  /** Missing from Helpers installed before the extension existed. */
  extension?: ExtensionStatus
  /** Employees: the manager's weekly work times for their tasks. */
  schedule?: {
    next: ScheduledSession | null
    last: {
      task_id: number
      title: string
      at: string
      ok: boolean
      error?: string
      minutes?: number
    } | null
  }
}

export interface ExtensionStatus {
  /** Paired with this computer at least once. */
  installed: boolean
  /** Checked in during the last 90 seconds: the browser is open. */
  connected: boolean
  /** False when paused from the extension's own button. */
  enabled: boolean | null
  browser: 'chrome' | 'edge' | null
  version: string | null
  paired_at: string | null
  /** The unpacked extension's folder on this computer. */
  folder: string | null
}

export interface ScheduledSession {
  task_id: number
  title: string
  /** Local time on this computer, "YYYY-MM-DDTHH:MM". */
  starts_at: string
  ends_at: string
  /** True while inside the window. */
  now: boolean
}

export interface TaskScheduleValue {
  /** 0 = Monday ... 6 = Sunday */
  days: number[]
  /** "HH:MM" */
  start: string
  minutes: number
}

export interface AssignedTask {
  id: number
  title: string
  category: string
  status: string
  project_name: string | null
  expected_minutes: number | null
  deadline: string | null
  productive_rules: string[]
  neutral_rules: string[]
  distracting_rules: string[]
  schedule_days?: number[] | null
  schedule_time?: string | null
  schedule_minutes?: number | null
}

/** Whether a camera will actually send video (see capture/camera_apps.py). */
export interface CameraReadiness {
  ready: boolean
  message: string
  device: string | null
  app: string | null
  app_running: boolean | null
  app_installed: boolean | null
  opened_app: boolean
  download_url: string | null
}

export class HelperError extends Error {
  readonly status: number
  /** Present when the camera is missing: what to install, and where. */
  readonly camera: CameraReadiness | null

  constructor(status: number, message: string, camera: CameraReadiness | null = null) {
    super(message)
    this.name = 'HelperError'
    this.status = status
    this.camera = camera
  }

  /** The person at the computer clicked "No". */
  get declined(): boolean {
    return this.status === 403
  }
}

async function call<T>(path: string, init: RequestInit = {}, timeoutMs?: number): Promise<T> {
  const controller = new AbortController()
  const timer = timeoutMs ? window.setTimeout(() => controller.abort(), timeoutMs) : undefined
  let response: Response
  try {
    response = await fetch(`${HELPER_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: init.body ? { 'Content-Type': 'application/json' } : undefined,
    })
  } catch {
    throw new HelperError(0, 'The FocusGuard Helper is not running on this computer.')
  } finally {
    if (timer) window.clearTimeout(timer)
  }

  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new HelperError(
      response.status,
      body?.error ?? `Helper error (${response.status})`,
      body?.camera ?? null,
    )
  }
  return body as T
}

export const localHelper = {
  /** Null when no helper answers - it is not installed, or not running. */
  async status(): Promise<HelperStatus | null> {
    try {
      return await call<HelperStatus>('/status', {}, 1500)
    } catch {
      return null
    }
  },

  pair: (enrollmentToken: string) =>
    call<{ ok: boolean; user_id: number }>('/pair', {
      method: 'POST',
      body: JSON.stringify({ enrollment_token: enrollmentToken }),
    }),

  tasks: () => call<AssignedTask[]>('/tasks', {}, 15_000),

  start: (body: {
    name?: string
    task?: string
    task_category?: string
    distracting?: string[]
    assigned_task_id?: number
  }) => call<{ ok: boolean; name: string; camera?: CameraReadiness }>('/sessions/start', {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  cameraCheck: () =>
    call<{ ok: boolean; camera?: CameraReadiness }>('/camera/check', {
      method: 'POST',
      body: '{}',
    }),

  /** Go back to asking before every session. Turning it on is only possible on the computer. */
  askEveryTime: () =>
    call<{ ok: boolean }>('/prefs', {
      method: 'POST',
      body: JSON.stringify({ ask_every_time: true }),
    }),

  /** Open the extension folder and the browser's extensions page, path on the clipboard. */
  setupExtension: (browser: 'chrome' | 'edge') =>
    call<{ ok: boolean; folder: string; browser: string }>('/extension/setup', {
      method: 'POST',
      body: JSON.stringify({ browser }),
    }),

  stop: () => call<{ ok: boolean }>('/sessions/stop', { method: 'POST', body: '{}' }, 10_000),
}
