/**
 * The wire contract, mirrored from `backend/focusguard_backend/api/v1/schemas.py`.
 *
 * Hand-written rather than generated. A generator would be the right answer for
 * a large API; this one has fourteen endpoints, and a generated client would add
 * a build step and a dependency to save a hundred lines that change rarely.
 *
 * Note what is absent and always will be: there is no `url`, no `frame`, no
 * `screenshot`, no `window_title` on anything the portal reads. The backend has
 * nowhere to put them (spec section 33), so neither does this.
 */

export type Role = 'STUDENT' | 'MANAGER' | 'EMPLOYEE'

export interface User {
  id: number
  email: string
  full_name: string | null
  role: Role
  organization_id: number | null
  is_active: boolean
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface SessionSummary {
  id: number
  session_uuid: string
  name: string
  task_name: string | null
  task_category: string | null
  started_at: string
  /** Null while the session is still running. */
  ended_at: string | null
  duration_seconds: number
  focused_seconds: number
  distracted_seconds: number
  average_focus_score: number
}

export interface VisionEvent {
  id: number
  event_type: string
  start_time: string
  end_time: string
  duration_seconds: number
  confidence: number
}

export interface ActivityEvent {
  id: number
  application_name: string
  /** A domain only - never a full address (section 20, 35). */
  domain: string | null
  category: string
  classification: 'PRODUCTIVE' | 'NEUTRAL' | 'DISTRACTING' | string
  rule_source: string
  start_time: string
  end_time: string
  duration_seconds: number
}

export interface FocusScore {
  recorded_at: string
  visual_attention_score: number
  digital_productivity_score: number | null
  combined_focus_score: number
  active_events: string[] | null
}

export interface SessionDetail extends SessionSummary {
  vision_events: VisionEvent[]
  activity_events: ActivityEvent[]
  focus_scores: FocusScore[]
}

/**
 * How each attention event is described to the student.
 *
 * Deliberately plain and non-clinical (spec section 8). "Looked away" is an
 * observation; "attention deficit" would be a diagnosis, and this project does
 * not diagnose anything. Nothing here names a condition or implies one.
 */
export const EVENT_LABELS: Record<string, string> = {
  FACE_ABSENT: 'Away from the desk',
  EYES_CLOSED: 'Eyes closed',
  DROWSINESS: 'Signs of tiredness',
  GAZE_AWAY: 'Looking away',
  HEAD_TURNED: 'Turned away',
  PHONE_VISIBLE: 'Phone in view',
  MULTIPLE_FACES: 'Someone else in frame',
}

export function eventLabel(eventType: string): string {
  return EVENT_LABELS[eventType] ?? eventType.replace(/_/g, ' ').toLowerCase()
}
