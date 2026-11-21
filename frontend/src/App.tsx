/**
 * Routing and the signed-in shell.
 *
 * Everything below the shell assumes a signed-in user, because the shell does
 * not render its children otherwise. That keeps every page free of "what if
 * there is no user" branches that would each need getting right.
 */

import { Suspense, lazy } from 'react'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom'

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
      <main className={`mx-auto p-4 sm:p-6 ${manager ? 'max-w-6xl' : 'max-w-5xl'}`}>
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
  { to: '/employees', label: 'Employees' },
  { to: '/tasks', label: 'Tasks' },
  { to: '/team', label: 'Teams & projects' },
]

function TopBar() {
  const { user, signOut } = useAuth()

  return (
    <header className="no-print border-b border-[var(--color-line)] bg-[var(--color-surface)]">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 p-4">
        <nav className="flex flex-wrap items-center gap-5">
          <Link to="/" className="font-semibold">
            FocusGuard
          </Link>
          {user?.role === 'MANAGER' ? (
            MANAGER_LINKS.map((link) => (
              <Link key={link.to} to={link.to} className="text-sm hover:opacity-70">
                {link.label}
              </Link>
            ))
          ) : (
            <Link to="/reports" className="text-sm hover:opacity-70">
              Reports
            </Link>
          )}
        </nav>

        <div className="flex items-center gap-4 text-sm">
          <span className="hidden text-[var(--color-muted)] sm:inline">
            {user?.full_name ?? user?.email}
          </span>
          <button
            type="button"
            onClick={() => void signOut()}
            className="rounded-lg border border-[var(--color-line)] px-3 py-1.5 font-medium hover:border-[var(--color-accent)]"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  )
}
