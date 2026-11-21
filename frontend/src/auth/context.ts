/**
 * The auth context object and its hook.
 *
 * Separate from the provider component on purpose. React Fast Refresh can only
 * hot-reload a module that exports components *and nothing else*; a file that
 * exports both `AuthProvider` and `useAuth` loses that, and every edit to the
 * provider then reloads the whole app and signs you out mid-session.
 */

import { createContext, useContext } from 'react'

import type { User } from '../api/types'

export interface AuthState {
  user: User | null
  /** True until we know whether the stored session is still valid. */
  loading: boolean
  signIn: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName: string) => Promise<void>
  /** Create a new organisation with the caller as its first manager. */
  registerOrganization: (
    organizationName: string,
    email: string,
    password: string,
    fullName: string,
  ) => Promise<void>
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (context === null) {
    throw new Error('useAuth must be used inside an AuthProvider')
  }
  return context
}
