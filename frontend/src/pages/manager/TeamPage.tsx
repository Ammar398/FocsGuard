/**
 * Departments and projects (spec section 24).
 */

import { useState } from 'react'

import { ApiError, api } from '../../api/client'
import type { Department, Project } from '../../api/managerTypes'
import { Card } from '../../components/StatCard'
import {
  Button,
  ErrorBanner,
  TextField,
} from '../../components/manager/ui'
import { usePolling } from '../../lib/usePolling'

export function TeamPage() {
  const departments = usePolling<Department[]>(() => api.manager.departments(), null)
  const projects = usePolling<Project[]>(() => api.manager.projects(), null)
  const [department, setDepartment] = useState('')
  const [project, setProject] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function run(action: () => Promise<unknown>, after: () => void) {
    setError(null)
    try {
      await action()
      after()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Something went wrong.')
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Teams and projects</h1>
      <ErrorBanner message={error} />

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Departments">
          <ul className="divide-y divide-[var(--color-line)]">
            {(departments.data ?? []).map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span>
                  {item.name}
                  <span className="ml-2 text-xs text-[var(--color-muted)]">
                    {item.employee_count} people
                  </span>
                </span>
                <span className="flex gap-2">
                  <Button
                    onClick={() => {
                      const name = window.prompt('Rename department', item.name)
                      if (name && name.trim()) {
                        void run(() => api.manager.renameDepartment(item.id, name), departments.refresh)
                      }
                    }}
                  >
                    Rename
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() => {
                      if (window.confirm(`Delete ${item.name}? Its employees are kept.`)) {
                        void run(() => api.manager.deleteDepartment(item.id), departments.refresh)
                      }
                    }}
                  >
                    Delete
                  </Button>
                </span>
              </li>
            ))}
          </ul>
          <form
            className="mt-4 flex items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              void run(() => api.manager.createDepartment(department), () => {
                setDepartment('')
                departments.refresh()
              })
            }}
          >
            <div className="grow">
              <TextField label="New department" value={department} onChange={setDepartment} required />
            </div>
            <Button type="submit" variant="primary">
              Add
            </Button>
          </form>
        </Card>

        <Card title="Projects">
          <ul className="divide-y divide-[var(--color-line)]">
            {(projects.data ?? []).map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className={item.is_active ? '' : 'opacity-50'}>
                  <span className="font-medium">{item.name}</span>
                  <span className="block text-xs text-[var(--color-muted)]">
                    {item.open_tasks} open · {item.completed_tasks} completed
                    {item.description ? ` · ${item.description}` : ''}
                  </span>
                </span>
                <Button
                  onClick={() =>
                    void run(
                      () => api.manager.updateProject(item.id, { is_active: !item.is_active }),
                      projects.refresh,
                    )
                  }
                >
                  {item.is_active ? 'Archive' : 'Restore'}
                </Button>
              </li>
            ))}
          </ul>
          <form
            className="mt-4 space-y-2"
            onSubmit={(event) => {
              event.preventDefault()
              void run(() => api.manager.createProject(project, description), () => {
                setProject('')
                setDescription('')
                projects.refresh()
              })
            }}
          >
            <TextField label="New project" value={project} onChange={setProject} required />
            <TextField label="Description" value={description} onChange={setDescription} />
            <Button type="submit" variant="primary">
              Add project
            </Button>
          </form>
        </Card>
      </div>

      <Card title="Enrolling an employee's computer">
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          <li>Open the employee's page and create an enrolment token. It works once, for an hour.</li>
          <li>
            On their computer, run{' '}
            <code className="text-xs">python -m focusguard_agent.tools.enroll --token …</code>
          </li>
          <li>They read what is and is not collected, and agree. The consent is recorded.</li>
          <li>
            They list their tasks with{' '}
            <code className="text-xs">python -m focusguard_agent.tools.tasks</code> and start one with{' '}
            <code className="text-xs">--assigned-task</code>.
          </li>
        </ol>
        <p className="mt-3 text-xs text-[var(--color-muted)]">
          The agent always shows when monitoring is on. It never records screens, page contents,
          keystrokes or webcam images.
        </p>
      </Card>
    </div>
  )
}
