/**
 * Routing and the signed-in shell.
 *
 * Everything below the shell assumes a signed-in user, because the shell does
 * not render its children otherwise. That keeps every page free of "what if
 * there is no user" branches that would each need getting right.
 */

import { Suspense, lazy } from 'react'
import { BrowserRouter, Link, NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { AuthProvider } from './auth/AuthContext'
import { useAuth } from './auth/context'
import { DashboardPage } from './pages/DashboardPage'
import { LoginPage } from './pages/LoginPage'

// Loaded on demand, because this is the only page that uses Recharts and
// Recharts is most of the bundle. Someone signing in should not download a
// charting library before they have chosen a session to look at.
const SessionPage = lazy(() =>
  import('./pages/SessionPage').then((module) => ({ default: module.SessionPage })),
)
const ReportsPage = lazy(() =>
  import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })),
)

// The manager portal is a separate bundle: a student never downloads it.
const ManagerDashboardPage = lazy(() =>
  import('./pages/manager/ManagerDashboardPage').then((m) => ({
    default: m.ManagerDashboardPage,
  })),
)
const EmployeesPage = lazy(() =>
  import('./pages/manager/EmployeesPage').then((m) => ({ default: m.EmployeesPage })),
)
const EmployeeDetailPage = lazy(() =>
  import('./pages/manager/EmployeeDetailPage').then((m) => ({ default: m.EmployeeDetailPage })),
)
const TasksPage = lazy(() =>
  import('./pages/manager/TasksPage').then((m) => ({ default: m.TasksPage })),
)
const TaskDetailPage = lazy(() =>
  import('./pages/manager/TaskDetailPage').then((m) => ({ default: m.TaskDetailPage })),
)
const TeamPage = lazy(() =>
  import('./pages/manager/TeamPage').then((m) => ({ default: m.TeamPage })),
)

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </AuthProvider>
  )
}

function Shell() {
  const { user, loading } = useAuth()

  // Nothing is rendered until we know whether the stored session is still good.
  // Flashing a sign-in form at someone who is already signed in reads as a bug.
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-[var(--color-muted)]">
        Loading…
      </div>
    )
  }

  if (user === null) {
    return <LoginPage />
  }

  // The role decides which portal is mounted. The server enforces the same
  // split on every route, so this is navigation, not security.
  const manager = user.role === 'MANAGER'

  return (
    <div className="min-h-screen">
      <TopBar />
      {/* overflow-x-clip: chart tooltips are positioned absolutely and can poke
          a pixel past the edge, which on a phone makes the whole page wobble
          sideways. Clip (unlike hidden) creates no scroll container. */}
      <main
        className={`mx-auto overflow-x-clip p-4 sm:p-6 ${manager ? 'max-w-6xl' : 'max-w-5xl'}`}
      >
        <Suspense
          fallback={<p className="p-8 text-[var(--color-muted)]">Loading…</p>}
        >
          {manager ? (
            <Routes>
              <Route path="/" element={<ManagerDashboardPage />} />
              <Route path="/employees" element={<EmployeesPage />} />
              <Route path="/employees/:id" element={<EmployeeDetailPage />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/tasks/:id" element={<TaskDetailPage />} />
              <Route path="/team" element={<TeamPage />} />
              <Route path="/sessions/:id" element={<SessionPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          ) : (
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/sessions/:id" element={<SessionPage />} />
              <Route path="/reports" element={<ReportsPage />} />
              {/* An unknown path goes home rather than showing a dead end. */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          )}
        </Suspense>
      </main>
    </div>
  )
}

const MANAGER_LINKS = [
  { to: '/', label: 'Overview' },
  { to: '/employees', label: 'Employees' },
  { to: '/tasks', label: 'Tasks' },
  { to: '/team', label: 'Teams & projects' },
]

const STUDENT_LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/reports', label: 'Reports' },
]

function TopBar() {
  const { user, signOut } = useAuth()
  const links = user?.role === 'MANAGER' ? MANAGER_LINKS : STUDENT_LINKS

  return (
    <header className="no-print border-b border-[var(--color-line)] bg-[var(--color-surface)]">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3 sm:py-4">
        <Link to="/" className="order-1 font-semibold">
          FocusGuard
        </Link>

        {/* On phones the links take a full-width row of their own and scroll
            sideways if they must, rather than wrapping into a ragged block. */}
        <nav className="order-3 -mx-1 flex w-full gap-1 overflow-x-auto sm:order-2 sm:mx-0 sm:w-auto sm:flex-1 sm:gap-4">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === '/'}
              className={({ isActive }) =>
                `shrink-0 rounded-md px-2 py-1 text-sm whitespace-nowrap transition ${
                  isActive
                    ? 'bg-[var(--color-accent)]/15 font-medium text-[var(--color-accent)]'
                    : 'text-[var(--color-muted)] hover:text-[var(--color-ink)]'
                }`
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>

        <div className="order-2 flex items-center gap-3 text-sm sm:order-3">
          <span className="hidden max-w-48 truncate text-[var(--color-muted)] md:inline">
            {user?.full_name ?? user?.email}
          </span>
          <button
            type="button"
            onClick={() => void signOut()}
            className="rounded-lg border border-[var(--color-line)] px-3 py-1.5 font-medium whitespace-nowrap hover:border-[var(--color-accent)]"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  )
}
