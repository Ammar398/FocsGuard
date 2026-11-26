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
}

export class HelperError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'HelperError'
    this.status = status
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
    throw new HelperError(response.status, body?.error ?? `Helper error (${response.status})`)
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
  }) => call<{ ok: boolean; name: string }>('/sessions/start', {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  stop: () => call<{ ok: boolean }>('/sessions/stop', { method: 'POST', body: '{}' }, 10_000),
}
