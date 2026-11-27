/**
 * The manager wire contract, mirrored from
 * `backend/focusguard_backend/api/v1/business_schemas.py`.
 *
 * Note what is absent: no window title, no URL path, no frame, no typed text.
 * A manager sees durations, categories, applications, domains, scores and task
 * outcomes (spec section 62), and these types have nowhere to put anything else.
 */

export const TASK_CATEGORIES = [
  'FRONTEND',
  'BACKEND',
  'DESIGN',
  'DATABASE',
  'TESTING',
  'DOCUMENTATION',
  'RESEARCH',
  'DEVOPS',
  'SUPPORT',
  'MARKETING',
  'OTHER',
] as const

export const TASK_PRIORITIES = ['LOW', 'MEDIUM', 'HIGH', 'URGENT'] as const
export const TASK_STATUSES = ['TODO', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'] as const

export type TaskStatus = (typeof TASK_STATUSES)[number]

export interface Organization {
  id: number
  name: string
  slug: string
}

export interface Department {
  id: number
  name: string
  employee_count: number
}

export interface UsageRow {
  label: string
  seconds: number
  classification: string
}

export interface WindowStats {
  sessions: number
  monitored_seconds: number
  focused_seconds: number
  distracted_seconds: number
  productive_seconds: number
  neutral_seconds: number
  distracting_seconds: number
  average_focus_score: number | null
  visual_attention_score: number | null
  digital_productivity_score: number | null
  phone_seconds: number
  looking_away_seconds: number
  face_absent_seconds: number
  eyes_closed_seconds: number
  head_down_seconds: number
  multiple_faces_seconds: number
  drowsiness_events: number
}

export interface TaskBrief {
  id: number
  title: string
  category: string
  status: string
}

export interface Employee {
  id: number
  email: string
  full_name: string | null
  department_id: number | null
  department_name: string | null
  is_active: boolean
  can_sign_in: boolean
  online: boolean
  open_tasks: number
  current_task: TaskBrief | null
  today: WindowStats
}

export interface DeviceSummary {
  id: number
  device_name: string
  platform: string | null
  agent_version: string | null
  enrolled: boolean
  revoked: boolean
  online: boolean
  last_seen_at: string | null
}

export interface Consent {
  consent_version: string
  granted_at: string
  revoked_at: string | null
}

export interface SessionBrief {
  id: number
  name: string
  task_id: number | null
  started_at: string
  ended_at: string | null
  duration_seconds: number
  average_focus_score: number
}

export interface CategoryPerformance {
  category: string
  completed_tasks: number
  sufficient_data: boolean
  historical_score: number | null
  average_quality: number | null
  on_time_rate: number | null
  average_duration_ratio: number | null
  average_focus: number | null
  average_productivity: number | null
  average_rework: number
  components: Record<string, number>
}

export interface StrengthReport {
  employee_id: number
  statements: string[]
  categories: CategoryPerformance[]
  minimum_tasks: number
  weights: Record<string, number>
}

export interface TaskScores {
  session_count: number
  monitored_seconds: number
  focused_seconds: number
  productive_seconds: number
  neutral_seconds: number
  distracting_seconds: number
  task_focus_score: number | null
  task_productivity_score: number | null
  duration_ratio: number | null
  explanation: string[]
}

export interface Task {
  id: number
  title: string
  description: string | null
  category: string
  priority: string
  status: TaskStatus
  project_id: number | null
  project_name: string | null
  assignee_id: number | null
  assignee_name: string | null
  deadline: string | null
  expected_minutes: number | null
  /** Weekly auto-start on the assignee's computer, or null. */
  schedule: TaskSchedule | null
  productive_rules: string[]
  neutral_rules: string[]
  distracting_rules: string[]
  started_at: string | null
  completed_at: string | null
  quality_rating: number | null
  rework_count: number
  on_time: boolean | null
  created_at: string
  scores: TaskScores
}

export interface TaskSchedule {
  /** 0 = Monday ... 6 = Sunday */
  days: number[]
  /** "HH:MM" on the employee's computer clock */
  start: string
  minutes: number
}

export interface TaskDetail extends Task {
  sessions: SessionBrief[]
  event_seconds: Record<string, number>
  event_counts: Record<string, number>
  top_applications: UsageRow[]
  top_domains: UsageRow[]
}

export interface EmployeeDetail extends Employee {
  window_days: number
  window: WindowStats
  devices: DeviceSummary[]
  consents: Consent[]
  top_applications: UsageRow[]
  top_domains: UsageRow[]
  recent_sessions: SessionBrief[]
  tasks: Task[]
  strengths: StrengthReport
}

export interface Project {
  id: number
  name: string
  description: string | null
  is_active: boolean
  open_tasks: number
  completed_tasks: number
}

export interface Candidate {
  user_id: number
  name: string
  rank: number | null
  historical_score: number | null
  completed_tasks: number
  open_tasks: number
  reasons: string[]
}

export interface Suggestions {
  category: string
  notice: string
  ranked: Candidate[]
  insufficient_data: Candidate[]
  minimum_tasks: number
}

export interface DepartmentStats {
  department_id: number | null
  name: string
  employees: number
  monitored_seconds: number
  productive_seconds: number
  distracting_seconds: number
  average_focus_score: number | null
}

export interface DayPoint {
  day: string
  monitored_seconds: number
  focused_seconds: number
  average_focus_score: number
}

export interface ManagerDashboard {
  organization: Organization
  window_days: number
  since: string
  until: string
  total_employees: number
  employees_online: number
  employees_offline: number
  active_tasks: number
  open_tasks: number
  completed_tasks_in_window: number
  totals: WindowStats
  departments: DepartmentStats[]
  top_applications: UsageRow[]
  top_domains: UsageRow[]
  daily: DayPoint[]
  employees: Employee[]
}

export interface EnrollmentToken {
  device_id: number
  device_name: string
  enrollment_token: string
  expires_at: string
  instructions: string
}

export interface TaskInput {
  title?: string
  description?: string | null
  category?: string
  priority?: string
  project_id?: number | null
  assignee_id?: number | null
  deadline?: string | null
  expected_minutes?: number | null
  schedule?: TaskSchedule | null
  clear_schedule?: boolean
  productive_rules?: string[]
  neutral_rules?: string[]
  distracting_rules?: string[]
  status?: TaskStatus
  quality_rating?: number
  rework_count?: number
  clear_assignee?: boolean
  clear_project?: boolean
  clear_deadline?: boolean
}

export function categoryLabel(category: string): string {
  const lower = category.replace(/_/g, ' ').toLowerCase()
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

export function statusLabel(status: string): string {
  return categoryLabel(status)
}
