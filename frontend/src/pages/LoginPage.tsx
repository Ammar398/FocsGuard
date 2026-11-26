/**
 * Sign in, sign up, verify an email address, and reset a forgotten password.
 *
 * ONE PAGE, SIX MODES
 * -------------------
 *   signin        email + password
 *   register      student sign-up            -> verify
 *   organisation  manager + organisation     -> verify
 *   verify        the six-digit emailed code -> signed in
 *   forgot        email                      -> reset
 *   reset         code + new password        -> signin
 *
 * They share the brand panel and the field styles; separate routes would be
 * near-identical pages drifting apart.
 *
 * WHAT THE MESSAGES SAY
 * ---------------------
 * Whatever the server said, verbatim. The backend is deliberately vague - a
 * wrong password and an unknown account give the same reply, and "forgot
 * password" says the same thing whether or not the address has an account - so
 * this page cannot be used to ask "does this person have an account here?".
 * Rewording those replies here would undo the defence from the client side.
 */

import { useEffect, useState, type FormEvent, type ReactNode } from 'react'

import { ApiError, api } from '../api/client'
import { useAuth } from '../auth/context'
import { Aperture } from '../components/Aperture'
import { IconChart, IconEye, IconShield, Logo } from '../components/icons'

type Mode = 'signin' | 'register' | 'organisation' | 'verify' | 'forgot' | 'reset'

const HEADINGS: Record<Mode, { title: string; subtitle: string; submit: string }> = {
  signin: {
    title: 'Welcome back',
    subtitle: 'Sign in to see your focus and your work.',
    submit: 'Sign in',
  },
  register: {
    title: 'Create a student account',
    subtitle: 'Free, private, and yours. Takes under a minute.',
    submit: 'Create account',
  },
  organisation: {
    title: 'Register your organisation',
    subtitle: 'You become its first manager. You can add your team next.',
    submit: 'Create organisation',
  },
  verify: {
    title: 'Check your email',
    subtitle: 'We sent a 6-digit code. Enter it to finish and sign in.',
    submit: 'Verify and sign in',
  },
  forgot: {
    title: 'Forgot your password?',
    subtitle: 'Enter your email and we will send you a code to reset it.',
    submit: 'Send reset code',
  },
  reset: {
    title: 'Choose a new password',
    subtitle: 'Enter the 6-digit code from your email and a new password.',
    submit: 'Change password',
  },
}

/** Seconds a person waits before "send again"; matches the server's cooldown. */
const RESEND_SECONDS = 60

export function LoginPage() {
  const { signIn, register, registerOrganization, verifyEmail } = useAuth()

  const [mode, setMode] = useState<Mode>('signin')
  const [organisation, setOrganisation] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (cooldown <= 0) return
    const timer = window.setTimeout(() => setCooldown((value) => value - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [cooldown])

  const registering = mode === 'register' || mode === 'organisation'
  const heading = HEADINGS[mode]

  function switchTo(next: Mode, message: string | null = null) {
    setMode(next)
    setError(null)
    setNotice(message)
    if (next === 'verify' || next === 'reset') {
      setCode('')
      setCooldown(RESEND_SECONDS)
    }
    if (next === 'reset') setPassword('')
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setNotice(null)
    setBusy(true)

    try {
      if (mode === 'organisation') {
        const mustVerify = await registerOrganization(organisation, email, password, fullName)
        if (mustVerify) switchTo('verify')
      } else if (mode === 'register') {
        const mustVerify = await register(email, password, fullName)
        if (mustVerify) switchTo('verify')
      } else if (mode === 'verify') {
        await verifyEmail(email, code)
      } else if (mode === 'forgot') {
        switchTo('reset', await api.forgotPassword(email))
      } else if (mode === 'reset') {
        const message = await api.resetPassword(email, code, password)
        setPassword('')
        switchTo('signin', message)
      } else {
        await signIn(email, password)
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 403 && mode === 'signin') {
        // The right password, but the email address was never confirmed. The
        // server has just sent a fresh code, so go straight to entering it.
        switchTo('verify', caught.message)
      } else {
        setError(
          caught instanceof ApiError
            ? caught.message
            : 'Could not reach the server. Is the backend running?',
        )
      }
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    setError(null)
    try {
      const message =
        mode === 'reset' ? await api.forgotPassword(email) : await api.resendVerification(email)
      setNotice(message)
      setCooldown(RESEND_SECONDS)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not send a new code.')
    }
  }

  const needsCode = mode === 'verify' || mode === 'reset'

  return (
    <main className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <BrandPanel />

      <div className="flex items-center justify-center p-6 sm:p-10">
        <div key={mode} className="w-full max-w-sm fg-rise">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <Logo size={32} id="fg-logo-mobile" />
            <span className="text-lg font-semibold tracking-tight">FocusGuard</span>
          </div>

          <h1 className="text-2xl font-semibold tracking-tight">{heading.title}</h1>
          <p className="mt-1.5 text-sm text-[var(--color-muted)]">
            {heading.subtitle}
            {needsCode && email && (
              <>
                {' '}
                Sent to <strong className="text-[var(--color-ink)]">{email}</strong>.
              </>
            )}
          </p>

          <form onSubmit={handleSubmit} className="mt-8 space-y-4">
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

            {!needsCode && (
              <Field
                label="Email"
                value={email}
                onChange={setEmail}
                type="email"
                autoComplete="username"
                required
              />
            )}

            {needsCode && <CodeInput value={code} onChange={setCode} />}

            {mode !== 'forgot' && mode !== 'verify' && (
              <Field
                label={mode === 'reset' ? 'New password' : 'Password'}
                value={password}
                onChange={setPassword}
                type="password"
                // Tells a password manager which one to offer, and which to save.
                autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                required
                hint={
                  mode === 'signin'
                    ? undefined
                    : 'At least 8 characters. Longer beats complicated.'
                }
                aside={
                  mode === 'signin' ? (
                    <button
                      type="button"
                      onClick={() => switchTo('forgot')}
                      className="text-xs font-medium text-[var(--color-accent)] hover:underline"
                    >
                      Forgot password?
                    </button>
                  ) : undefined
                }
              />
            )}

            {notice !== null && (
              <p
                role="status"
                className="rounded-xl bg-[var(--color-accent-soft)] px-3 py-2 text-sm text-[var(--color-ink)]"
              >
                {notice}
              </p>
            )}
            {error !== null && (
              <p
                role="alert"
                className="rounded-xl bg-[var(--color-focus-low)]/10 px-3 py-2 text-sm text-[var(--color-focus-low)]"
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || (needsCode && code.replace(/\D/g, '').length !== 6)}
              className="w-full rounded-xl px-4 py-2.5 font-semibold text-[var(--color-on-accent)] shadow-[var(--shadow-card)] transition hover:brightness-110 disabled:opacity-50"
              style={{ background: 'var(--brand-gradient)' }}
            >
              {busy ? 'Please wait…' : heading.submit}
            </button>

            {needsCode && (
              <p className="text-center text-sm text-[var(--color-muted)]">
                Didn't get it? Check spam, or{' '}
                {cooldown > 0 ? (
                  <span>send again in {cooldown}s</span>
                ) : (
                  <LinkButton onClick={() => void resend()}>send a new code</LinkButton>
                )}
                .
              </p>
            )}
          </form>

          <div className="mt-6 space-y-2 text-center text-sm text-[var(--color-muted)]">
            {mode !== 'signin' && (
              <p>
                {mode === 'verify' || mode === 'forgot' || mode === 'reset'
                  ? 'Back to '
                  : 'Already have an account? '}
                <LinkButton onClick={() => switchTo('signin')}>Sign in</LinkButton>
              </p>
            )}
            {(mode === 'signin' || mode === 'organisation') && (
              <p>
                Studying?{' '}
                <LinkButton onClick={() => switchTo('register')}>Create a student account</LinkButton>
              </p>
            )}
            {(mode === 'signin' || mode === 'register') && (
              <p>
                Managing a team?{' '}
                <LinkButton onClick={() => switchTo('organisation')}>
                  Register your organisation
                </LinkButton>
              </p>
            )}
          </div>
        </div>
      </div>
    </main>
  )
}

/**
 * The six-digit code: one field, large spaced digits, numeric keyboard on
 * phones, and the browser's "one-time code" autofill (iOS/Android offer the
 * code straight from the email or SMS).
 */
function CodeInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-sm font-medium">6-digit code</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value.replace(/[^\d ]/g, '').slice(0, 7))}
        inputMode="numeric"
        autoComplete="one-time-code"
        placeholder="000 000"
        autoFocus
        required
        className="mt-1.5 w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-3.5 py-3 text-center text-2xl font-semibold tracking-[0.4em] outline-none transition placeholder:text-[var(--color-line)] focus:border-[var(--color-accent)] focus:ring-4 focus:ring-[var(--color-accent)]/15"
      />
    </label>
  )
}

function BrandPanel() {
  return (
    <aside className="relative hidden overflow-hidden border-r border-[var(--color-line)] bg-[var(--color-canvas)] p-12 lg:flex lg:flex-col lg:justify-between">
      <div className="flex items-center gap-3">
        <Logo size={34} />
        <span className="text-xl font-semibold [font-stretch:112%]">FocusGuard</span>
      </div>

      <div className="flex items-center gap-12">
        <Aperture size={220} score={78} animate label="The FocusGuard aperture" />
        <div className="max-w-sm">
          <h2 className="text-4xl leading-[1.08] font-[750]">See your focus. Keep your privacy.</h2>
          <p className="mt-5 text-[var(--color-ink-soft)]">
            Attention analysis that runs on your own computer, with reports that show the data
            behind every number.
          </p>
        </div>
      </div>

      <ul className="grid grid-cols-3 gap-8 border-t border-[var(--color-line)] pt-8">
        <Feature icon={<IconEye />} title="On your computer">
          Webcam frames never leave it.
        </Feature>
        <Feature icon={<IconChart />} title="Explained">
          Every score shows its data.
        </Feature>
        <Feature icon={<IconShield />} title="Your approval">
          Nothing starts until you say yes.
        </Feature>
      </ul>
    </aside>
  )
}

function Feature({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li>
      <span className="text-[var(--color-accent)]">{icon}</span>
      <span className="mt-3 block font-semibold">{title}</span>
      <span className="mt-1 block text-sm text-[var(--color-muted)]">{children}</span>
    </li>
  )
}

function LinkButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="font-medium text-[var(--color-accent)] hover:underline"
    >
      {children}
    </button>
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
  aside?: ReactNode
}

function Field({ label, value, onChange, type, autoComplete, required, hint, aside }: FieldProps) {
  return (
    <label className="block">
      <span className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium">{label}</span>
        {aside}
      </span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        required={required}
        className="mt-1.5 w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-3.5 py-2.5 outline-none transition focus:border-[var(--color-accent)] focus:ring-4 focus:ring-[var(--color-accent)]/15"
      />
      {hint !== undefined && (
        <span className="mt-1 block text-xs text-[var(--color-muted)]">{hint}</span>
      )}
    </label>
  )
}
