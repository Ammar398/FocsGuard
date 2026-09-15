/**
 * Who is signed in, for the whole app.
 *
 * The `loading` flag matters more than it looks: on first paint we do not yet
 * know whether the stored refresh token is still good. Rendering the login page
 * during that moment would flash a sign-in form at a user who is already signed
 * in, which reads as a bug.
 *
 * The context object and the `useAuth` hook live in `./context` so this file
 * exports only a component - see the note there about Fast Refresh.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { api, setSignedOutHandler } from '../api/client'
import type { User } from '../api/types'
import { AuthContext } from './context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    // The client calls this when a refresh has failed for good, which can happen
    // mid-session - a revoked token, or a password changed elsewhere.
    setSignedOutHandler(() => {
      if (!cancelled) setUser(null)
    })

    api
      .restore()
      .then((restored) => {
        if (!cancelled) setUser(restored)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
      setSignedOutHandler(null)
    }
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    setUser(await api.login(email, password))
  }, [])

  const register = useCallback(
    async (email: string, password: string, fullName: string) => {
      await api.register(email, password, fullName)
      // Signing in straight away: making someone type the same password twice
      // in ten seconds achieves nothing.
      setUser(await api.login(email, password))
    },
    [],
  )

  const signOut = useCallback(async () => {
    await api.logout()
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, loading, signIn, register, signOut }),
    [user, loading, signIn, register, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
