/**
 * Sign in, create a student account, or register an organisation.
 *
 * ONE FORM, THREE MODES
 * ---------------------
 * They differ by a field or two. Separate routes would be near-identical forms
 * drifting apart over time.
 *
 * WHAT THE ERROR MESSAGES SAY
 * ---------------------------
 * Whatever the server said, verbatim. The backend is deliberately vague - a
 * wrong password and an unknown account give the same reply, so that this page
 * cannot be used to ask "does this person have an account here?". Helpfully
 * rewording that into "no account with that email" here would undo the defence
 * from the client side.
 */

import { useState, type FormEvent, type ReactNode } from 'react'

import { ApiError } from '../api/client'
import { useAuth } from '../auth/context'
import { IconChart, IconEye, IconLock, IconShield, Logo } from '../components/icons'

type Mode = 'signin' | 'register' | 'organisation'

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
}

export function LoginPage() {
  const { signIn, register, registerOrganization } = useAuth()

  const [mode, setMode] = useState<Mode>('signin')
  const [organisation, setOrganisation] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const registering = mode !== 'signin'
  const heading = HEADINGS[mode]

  function switchTo(next: Mode) {
    setMode(next)
    setError(null)
  }

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
    <main className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <BrandPanel />

      <div className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-sm fg-rise">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <Logo size={32} id="fg-logo-mobile" />
            <span className="text-lg font-semibold tracking-tight">FocusGuard</span>
          </div>

          <h1 className="text-2xl font-semibold tracking-tight">{heading.title}</h1>
          <p className="mt-1.5 text-sm text-[var(--color-muted)]">{heading.subtitle}</p>

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
              hint={registering ? 'At least 8 characters. Longer beats complicated.' : undefined}
            />

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
              disabled={busy}
              className="w-full rounded-xl px-4 py-2.5 font-medium text-white shadow-[var(--shadow-card)] transition hover:brightness-110 disabled:opacity-50"
              style={{ background: 'var(--brand-gradient)' }}
            >
              {busy ? 'Please wait…' : heading.submit}
            </button>
          </form>

          <div className="mt-6 space-y-2 text-center text-sm text-[var(--color-muted)]">
            {mode !== 'signin' && (
              <p>
                Already have an account? <LinkButton onClick={() => switchTo('signin')}>Sign in</LinkButton>
              </p>
            )}
            {mode !== 'register' && (
              <p>
                Studying? <LinkButton onClick={() => switchTo('register')}>Create a student account</LinkButton>
              </p>
            )}
            {mode !== 'organisation' && (
              <p>
                Managing a team?{' '}
                <LinkButton onClick={() => switchTo('organisation')}>Register your organisation</LinkButton>
              </p>
            )}
          </div>
        </div>
      </div>
    </main>
  )
}

function BrandPanel() {
  return (
    <aside
      className="relative hidden overflow-hidden p-12 text-white lg:flex lg:flex-col lg:justify-between"
      style={{ background: 'var(--brand-gradient)' }}
    >
      {/* Soft light shapes; decoration only. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-24 -right-24 h-96 w-96 rounded-full bg-white/10 blur-2xl"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-32 -left-20 h-[28rem] w-[28rem] rounded-full bg-white/10 blur-3xl"
      />

      <div className="relative flex items-center gap-3">
        <span className="rounded-xl bg-white/15 p-1.5 backdrop-blur">
          <Logo size={30} id="fg-logo-panel" />
        </span>
        <span className="text-xl font-semibold tracking-tight">FocusGuard</span>
      </div>

      <div className="relative max-w-md">
        <h2 className="text-4xl leading-tight font-semibold tracking-tight">
          Understand your focus. Keep your privacy.
        </h2>
        <p className="mt-4 text-white/80">
          AI attention analysis that runs on your own computer, paired with honest, explainable
          reports - for students improving their study habits and teams understanding their work.
        </p>

        <ul className="mt-10 space-y-5">
          <Feature icon={<IconEye />} title="On-device vision">
            Webcam analysis happens on your computer. Frames never leave it.
          </Feature>
          <Feature icon={<IconChart />} title="Explainable insights">
            Every score and suggestion shows the data behind it.
          </Feature>
          <Feature icon={<IconShield />} title="You stay in control">
            Nothing starts without your approval on your own computer.
          </Feature>
        </ul>
      </div>

      <p className="relative flex items-center gap-2 text-sm text-white/70">
        <IconLock size={16} />
        No screenshots, no keystrokes, no webcam images - ever.
      </p>
    </aside>
  )
}

function Feature({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-4">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/15 backdrop-blur">
        {icon}
      </span>
      <span>
        <span className="block font-medium">{title}</span>
        <span className="block text-sm text-white/75">{children}</span>
      </span>
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
        className="mt-1.5 w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-3.5 py-2.5 outline-none transition focus:border-[var(--color-accent)] focus:ring-4 focus:ring-[var(--color-accent)]/15"
      />
      {hint !== undefined && (
        <span className="mt-1 block text-xs text-[var(--color-muted)]">{hint}</span>
      )}
    </label>
  )
}
