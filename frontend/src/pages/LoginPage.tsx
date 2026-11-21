/**
 * Sign in, create a student account, or register an organisation.
 *
 * ONE FORM, TWO MODES
 * -------------------
 * Register and sign-in differ by one field. Two routes would mean two nearly
 * identical forms drifting apart over time.
 *
 * WHAT THE ERROR MESSAGES SAY
 * ---------------------------
 * Whatever the server said, verbatim. The backend is deliberately vague - a
 * wrong password and an unknown account give the same reply, so that this page
 * cannot be used to ask "does this person have an account here?". Helpfully
 * rewording that into "no account with that email" here would undo the defence
 * from the client side.
 */

import { useState, type FormEvent } from 'react'

import { ApiError } from '../api/client'
import { useAuth } from '../auth/context'

export function LoginPage() {
  const { signIn, register, registerOrganization } = useAuth()

  const [mode, setMode] = useState<'signin' | 'register' | 'organisation'>('signin')
  const [organisation, setOrganisation] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const registering = mode !== 'signin'

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)

    try {
      if (mode === 'organisation') {
        await registerOrganization(organisation, email, password, fullName)
      } else if (registering) {
        await register(email, password, fullName)
      } else {
        await signIn(email, password)
      }
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : 'Could not reach the server. Is the backend running?',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold">FocusGuard</h1>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Your attention and productivity, measured privately.
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] p-6"
        >
          {mode === 'organisation' && (
            <Field
              label="Organisation name"
              value={organisation}
              onChange={setOrganisation}
              type="text"
              autoComplete="organization"
              required
            />
          )}

          {registering && (
            <Field
              label="Your name"
              value={fullName}
              onChange={setFullName}
              type="text"
              autoComplete="name"
            />
          )}

          <Field
            label="Email"
            value={email}
            onChange={setEmail}
            type="email"
            autoComplete="username"
            required
          />

          <Field
            label="Password"
            value={password}
            onChange={setPassword}
            type="password"
            // Tells a password manager which one to offer, and which to save.
            autoComplete={registering ? 'new-password' : 'current-password'}
            required
            hint={registering ? 'At least 8 characters. Longer is better than complicated.' : undefined}
          />

          {error !== null && (
            <p
              role="alert"
              className="rounded-lg bg-[var(--color-focus-low)]/10 px-3 py-2 text-sm text-[var(--color-focus-low)]"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-[var(--color-accent)] px-4 py-2.5 font-medium text-white transition hover:opacity-90 disabled:opacity-50"
          >
            {busy
              ? 'Please wait…'
              : mode === 'organisation'
                ? 'Create organisation'
                : registering
                  ? 'Create account'
                  : 'Sign in'}
          </button>

          <p className="pt-1 text-center text-sm text-[var(--color-muted)]">
            {registering ? 'Already have an account?' : 'New here?'}{' '}
            <button
              type="button"
              onClick={() => {
                setMode(registering ? 'signin' : 'register')
                setError(null)
              }}
              className="font-medium text-[var(--color-accent)] hover:underline"
            >
              {registering ? 'Sign in' : 'Create a student account'}
            </button>
          </p>
          {mode !== 'organisation' && (
            <p className="text-center text-sm text-[var(--color-muted)]">
              Managing a team?{' '}
              <button
                type="button"
                onClick={() => {
                  setMode('organisation')
                  setError(null)
                }}
                className="font-medium text-[var(--color-accent)] hover:underline"
              >
                Register your organisation
              </button>
            </p>
          )}
        </form>

        <p className="mt-6 text-center text-xs text-[var(--color-muted)]">
          FocusGuard never uploads webcam images, screenshots, or anything you type.
        </p>
      </div>
    </main>
  )
}

interface FieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  type: string
  autoComplete?: string
  required?: boolean
  hint?: string
}

function Field({ label, value, onChange, type, autoComplete, required, hint }: FieldProps) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        required={required}
        className="mt-1 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-canvas)] px-3 py-2 outline-none focus:border-[var(--color-accent)]"
      />
      {hint !== undefined && (
        <span className="mt-1 block text-xs text-[var(--color-muted)]">{hint}</span>
      )}
    </label>
  )
}
