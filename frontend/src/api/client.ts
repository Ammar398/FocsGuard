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

import type { SessionDetail, SessionSummary, TokenPair, User } from './types'

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
}
