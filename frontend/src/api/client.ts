/**
 * The API client: one place that knows how to talk to the backend.
 *
 * WHERE THE TOKENS LIVE, AND THE HONEST TRADE
 * -------------------------------------------
 * The **access token is held in memory only**. It never reaches localStorage, so
 * a script injected into this page cannot read it out of storage, and closing the
 * tab discards it.
 *
 * The **refresh token is in localStorage**, because it has to survive a page
 * reload or the user is signed out every time they press F5.
 *
 * That is a real weakness and it is written down rather than glossed over: an
 * XSS on this page can read a thirty-day refresh token. The better design is an
 * httpOnly, SameSite cookie, which script cannot read at all. It is not done here
 * because the backend returns a JSON token pair, and moving to cookies also means
 * adding CSRF protection - a change to the Phase 13 contract rather than a
 * frontend detail. It belongs in the Phase 23 hardening pass, and until then the
 * limitation is stated plainly.
 *
 * WHY REFRESHING IS SINGLE-FLIGHT
 * -------------------------------
 * This one is not a nicety, it is required by our own backend.
 *
 * Refresh tokens rotate, and a token that is used twice is treated as **stolen**:
 * the whole family is revoked (see `backend/focusguard_backend/api/v1/auth.py`).
 * If the dashboard fires three requests, all three get a 401, and all three try
 * to refresh with the same stored token, then the second and third look exactly
 * like a replay attack - and the backend would correctly sign the user out.
 *
 * So a refresh in progress is shared: the first caller performs it, and everyone
 * else waits on the same promise.
 */

import type {
  Department,
  EmployeeDetail,
  Employee,
  EnrollmentToken,
  ManagerDashboard,
  Organization,
  Project,
  StrengthReport,
  Suggestions,
  Task,
  TaskDetail,
  TaskInput,
} from './managerTypes'
import type {
  RecommendationItem,
  ReportSummary,
  SessionDetail,
  SessionSummary,
  TokenPair,
  User,
} from './types'

const REFRESH_STORAGE_KEY = 'focusguard.refresh'

/** Raised for any non-2xx response, carrying enough to render a message. */
export class ApiError extends Error {
  // Declared and assigned rather than a constructor parameter property, which
  // TypeScript 6 rejects under `erasableSyntaxOnly` - the flag that keeps this
  // code strippable to plain JavaScript with no emit step of its own.
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }

  /** True when signing in again is the fix. */
  get isAuthError(): boolean {
    return this.status === 401 || this.status === 403
  }
}

let accessToken: string | null = null

/** Set when a refresh is in flight, so concurrent callers share one attempt. */
let refreshInFlight: Promise<boolean> | null = null

/** Called when the session ends for good, so the app can show the login page. */
let onSignedOut: (() => void) | null = null

export function setSignedOutHandler(handler: (() => void) | null): void {
  onSignedOut = handler
}

export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_STORAGE_KEY)
  } catch {
    // Private browsing, or storage disabled. The app still works for this
    // session; the user simply signs in again after a reload.
    return null
  }
}

function storeTokens(tokens: TokenPair): void {
  accessToken = tokens.access_token
  try {
    localStorage.setItem(REFRESH_STORAGE_KEY, tokens.refresh_token)
  } catch {
    /* see getRefreshToken */
  }
}

export function clearTokens(): void {
  accessToken = null
  try {
    localStorage.removeItem(REFRESH_STORAGE_KEY)
  } catch {
    /* ignore */
  }
}

export function hasStoredSession(): boolean {
  return getRefreshToken() !== null
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json()
    const detail = body?.detail
    if (typeof detail === 'string') return detail
    // FastAPI validation errors arrive as a list of objects.
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
  } catch {
    /* fall through to the generic message */
  }
  return `Request failed (${response.status})`
}

/**
 * Exchange the stored refresh token for a new pair.
 *
 * Returns false when the session is over for good, in which case the tokens are
 * cleared - a refresh token the server has rejected is worse than useless, since
 * presenting it again is what reuse detection punishes.
 */
async function refreshSession(): Promise<boolean> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false

  const response = await fetch('/api/v1/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })

  if (!response.ok) {
    clearTokens()
    return false
  }

  storeTokens((await response.json()) as TokenPair)
  return true
}

/** Share one refresh between every caller that needs it. See the header. */
function refreshOnce(): Promise<boolean> {
  if (refreshInFlight === null) {
    refreshInFlight = refreshSession().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

interface RequestOptions {
  method?: string
  body?: unknown
  /** Internal: prevents a refresh loop when the retry also fails. */
  retrying?: boolean
  /** Endpoints that take no credentials, such as login. */
  anonymous?: boolean
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {}
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (!options.anonymous && accessToken) {
    headers.Authorization = `Bearer ${accessToken}`
  }

  const response = await fetch(`/api/v1${path}`, {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })

  if (response.status === 401 && !options.anonymous && !options.retrying) {
    // The access token expired, which is routine every fifteen minutes.
    if (await refreshOnce()) {
      return request<T>(path, { ...options, retrying: true })
    }
    onSignedOut?.()
    throw new ApiError(401, 'Your session has ended. Please sign in again.')
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readError(response))
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

// --- the API surface --------------------------------------------------------

export const api = {
  async register(email: string, password: string, fullName?: string): Promise<User> {
    return request<User>('/auth/register', {
      method: 'POST',
      anonymous: true,
      body: { email, password, full_name: fullName || null },
    })
  },

  async login(email: string, password: string): Promise<User> {
    const tokens = await request<TokenPair>('/auth/login', {
      method: 'POST',
      anonymous: true,
      body: { email, password },
    })
    storeTokens(tokens)
    return request<User>('/auth/me')
  },

  /**
   * Restore a session from the stored refresh token, on page load.
   * Returns null when there is nothing to restore.
   */
  async restore(): Promise<User | null> {
    if (!hasStoredSession()) return null
    if (!(await refreshOnce())) return null
    try {
      return await request<User>('/auth/me')
    } catch {
      clearTokens()
      return null
    }
  },

  async logout(): Promise<void> {
    const refreshToken = getRefreshToken()
    clearTokens()
    if (!refreshToken) return
    try {
      await request<unknown>('/auth/logout', {
        method: 'POST',
        anonymous: true,
        body: { refresh_token: refreshToken },
      })
    } catch {
      // The local tokens are already gone, so the user is signed out here
      // regardless. Failing loudly would be worse than useless.
    }
  },

  async sessions(limit = 50): Promise<SessionSummary[]> {
    return request<SessionSummary[]>(`/sessions?limit=${limit}`)
  },

  async session(id: number): Promise<SessionDetail> {
    return request<SessionDetail>(`/sessions/${id}`)
  },

  async report(days: number): Promise<ReportSummary> {
    return request<ReportSummary>(
      `/reports/summary?days=${days}&offset_minutes=${localOffsetMinutes()}`,
    )
  },

  async recommendations(days: number): Promise<RecommendationItem[]> {
    return request<RecommendationItem[]>(
      `/reports/recommendations?days=${days}&offset_minutes=${localOffsetMinutes()}`,
    )
  },

  /**
   * Fetch the CSV as a blob and hand it to the browser.
   *
   * A plain link cannot work here: the export needs an Authorization header,
   * and an <a href> sends none. Fetching it and creating an object URL is the
   * only way to download an authenticated file without putting the token in a
   * query string, where it would land in server logs and browser history.
   */
  async exportCsv(days: number): Promise<void> {
    await download(`/reports/export.csv?days=${days}`, 'focusguard')
  },

  /** An enrolment token for one of the caller's own computers. */
  async enrolMyDevice(deviceName: string): Promise<EnrollmentToken> {
    return request<EnrollmentToken>('/devices', {
      method: 'POST',
      body: { device_name: deviceName },
    })
  },

  // --- business mode (Milestone E) ----------------------------------------

  async registerOrganization(
    organizationName: string,
    email: string,
    password: string,
    fullName?: string,
  ): Promise<User> {
    return request<User>('/auth/register-organization', {
      method: 'POST',
      anonymous: true,
      body: {
        organization_name: organizationName,
        email,
        password,
        full_name: fullName || null,
      },
    })
  },

  manager: {
    organization: () => request<Organization>('/manager/organization'),

    dashboard: (days: number) =>
      request<ManagerDashboard>(
        `/manager/dashboard?days=${days}&offset_minutes=${localOffsetMinutes()}`,
      ),

    departments: () => request<Department[]>('/manager/departments'),
    createDepartment: (name: string) =>
      request<Department>('/manager/departments', { method: 'POST', body: { name } }),
    renameDepartment: (id: number, name: string) =>
      request<Department>(`/manager/departments/${id}`, { method: 'PATCH', body: { name } }),
    deleteDepartment: (id: number) =>
      request<unknown>(`/manager/departments/${id}`, { method: 'DELETE' }),

    employees: () =>
      request<Employee[]>(`/manager/employees?offset_minutes=${localOffsetMinutes()}`),
    employee: (id: number, days: number) =>
      request<EmployeeDetail>(
        `/manager/employees/${id}?days=${days}&offset_minutes=${localOffsetMinutes()}`,
      ),
    createEmployee: (body: {
      email: string
      full_name: string
      department_id?: number | null
      password?: string | null
    }) => request<Employee>('/manager/employees', { method: 'POST', body }),
    updateEmployee: (
      id: number,
      body: {
        full_name?: string
        department_id?: number | null
        clear_department?: boolean
        is_active?: boolean
        password?: string
      },
    ) => request<Employee>(`/manager/employees/${id}`, { method: 'PATCH', body }),
    strengths: (id: number) => request<StrengthReport>(`/manager/employees/${id}/strengths`),

    projects: () => request<Project[]>('/manager/projects'),
    createProject: (name: string, description?: string) =>
      request<Project>('/manager/projects', {
        method: 'POST',
        body: { name, description: description || null },
      }),
    updateProject: (id: number, body: { name?: string; is_active?: boolean }) =>
      request<Project>(`/manager/projects/${id}`, { method: 'PATCH', body }),

    tasks: (status?: string) =>
      request<Task[]>(`/manager/tasks${status ? `?status=${status}` : ''}`),
    task: (id: number) => request<TaskDetail>(`/manager/tasks/${id}`),
    createTask: (body: TaskInput) => request<Task>('/manager/tasks', { method: 'POST', body }),
    updateTask: (id: number, body: TaskInput) =>
      request<Task>(`/manager/tasks/${id}`, { method: 'PATCH', body }),
    suggestions: (category: string) =>
      request<Suggestions>(`/manager/tasks/suggestions?category=${category}`),

    enrolDevice: (userId: number, deviceName: string) =>
      request<EnrollmentToken>('/devices', {
        method: 'POST',
        body: { device_name: deviceName, user_id: userId },
      }),
    revokeDevice: (deviceId: number) =>
      request<unknown>(`/devices/${deviceId}`, { method: 'DELETE' }),

    exportEmployees: (days: number) =>
      download(
        `/manager/reports/employees.csv?days=${days}&offset_minutes=${localOffsetMinutes()}`,
        'focusguard-employees',
      ),
    exportTasks: () => download('/manager/reports/tasks.csv', 'focusguard-tasks'),
  },
}

/**
 * Fetch an authenticated file and hand it to the browser.
 *
 * A plain link cannot work here: the export needs an Authorization header,
 * and an <a href> sends none. Fetching it and creating an object URL is the
 * only way to download an authenticated file without putting the token in a
 * query string, where it would land in server logs and browser history.
 */
async function download(path: string, name: string, retrying = false): Promise<void> {
  const response = await fetch(`/api/v1${path}`, {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  })
  if (response.status === 401 && !retrying && (await refreshOnce())) {
    return download(path, name, true)
  }
  if (!response.ok) {
    throw new ApiError(response.status, await readError(response))
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${name}-${new Date().toISOString().slice(0, 10)}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Released immediately; the browser has already started the download, and
  // holding the blob would keep the whole file in memory for the session.
  URL.revokeObjectURL(url)
}

/**
 * Minutes east of UTC, from the browser.
 *
 * The server stores UTC and cannot guess where the user is. Without this,
 * "sessions after 10pm" and "today" would be computed in the wrong day for
 * anyone outside UTC - which is almost everyone.
 *
 * `getTimezoneOffset` returns minutes *behind* UTC, so the sign is flipped.
 */
function localOffsetMinutes(): number {
  return -new Date().getTimezoneOffset()
}
