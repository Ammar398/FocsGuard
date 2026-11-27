"""Wire contract for business mode (spec sections 24-31).

Kept apart from `schemas.py` so the student contract and the manager contract can
be read separately. The same rules apply: requests forbid unknown fields, and no
response here carries a password hash, a token, a window title, a URL or a frame.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from focusguard_shared.constants import TaskCategory, TaskPriority, TaskStatus


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- organisation sign-up (spec sections 6, 24) -----------------------------


class OrganizationRegisterRequest(_Request):
    organization_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    full_name: str | None = Field(default=None, max_length=200)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


# --- departments ------------------------------------------------------------


class DepartmentCreate(_Request):
    name: str = Field(min_length=1, max_length=120)


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    employee_count: int = 0


# --- employees --------------------------------------------------------------


class EmployeeCreate(_Request):
    """A manager adds an employee.

    `password` is optional. Without one, the account cannot sign in to the portal
    at all - it exists so the employee's agent has someone to report for. With
    one, the employee can sign in and see **their own** data, the same views a
    student has, which is the transparency section 62 asks for.
    """

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    department_id: int | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


class EmployeeUpdate(_Request):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    department_id: int | None = None
    #: Explicit, because "department_id: null" must be able to mean "remove".
    clear_department: bool = False
    is_active: bool | None = None
    #: Sets or replaces the employee's portal password.
    password: str | None = Field(default=None, min_length=8, max_length=256)


class DeviceSummary(BaseModel):
    id: int
    device_name: str
    platform: str | None = None
    agent_version: str | None = None
    enrolled: bool
    revoked: bool
    online: bool
    last_seen_at: datetime | None = None


class UsageRow(BaseModel):
    label: str
    seconds: float
    classification: str


class WindowStats(BaseModel):
    """An employee's or a team's totals over a window (spec section 27)."""

    sessions: int = 0
    monitored_seconds: float = 0.0
    focused_seconds: float = 0.0
    distracted_seconds: float = 0.0
    productive_seconds: float = 0.0
    neutral_seconds: float = 0.0
    distracting_seconds: float = 0.0
    average_focus_score: float | None = None
    visual_attention_score: float | None = None
    digital_productivity_score: float | None = None
    phone_seconds: float = 0.0
    looking_away_seconds: float = 0.0
    face_absent_seconds: float = 0.0
    eyes_closed_seconds: float = 0.0
    head_down_seconds: float = 0.0
    multiple_faces_seconds: float = 0.0
    drowsiness_events: int = 0


class TaskBrief(BaseModel):
    id: int
    title: str
    category: str
    status: str


class EmployeeOut(BaseModel):
    id: int
    email: str
    full_name: str | None
    department_id: int | None
    department_name: str | None
    is_active: bool
    can_sign_in: bool
    online: bool
    open_tasks: int
    current_task: TaskBrief | None = None
    today: WindowStats


class ConsentOut(BaseModel):
    consent_version: str
    granted_at: datetime
    revoked_at: datetime | None = None


class SessionBrief(BaseModel):
    id: int
    name: str
    task_id: int | None
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float
    average_focus_score: float


class CategoryPerformanceOut(BaseModel):
    category: str
    completed_tasks: int
    sufficient_data: bool
    historical_score: float | None
    average_quality: float | None
    on_time_rate: float | None
    average_duration_ratio: float | None
    average_focus: float | None
    average_productivity: float | None
    average_rework: float
    components: dict[str, float]


class StrengthReport(BaseModel):
    employee_id: int
    statements: list[str]
    categories: list[CategoryPerformanceOut]
    minimum_tasks: int
    weights: dict[str, float]


class EmployeeDetail(EmployeeOut):
    window_days: int
    window: WindowStats
    devices: list[DeviceSummary]
    consents: list[ConsentOut]
    top_applications: list[UsageRow]
    top_domains: list[UsageRow]
    recent_sessions: list[SessionBrief]
    tasks: list[TaskOut]
    strengths: StrengthReport


# --- projects ---------------------------------------------------------------


class ProjectCreate(_Request):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class ProjectUpdate(_Request):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    is_active: bool | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_active: bool
    open_tasks: int = 0
    completed_tasks: int = 0


# --- tasks (spec sections 28, 29) -------------------------------------------

_RULE = Field(default_factory=list, max_length=100)


def _clean_rules(values: list[str]) -> list[str]:
    """Trim, drop blanks and duplicates, keep order. Lowercase like the agent."""
    seen: list[str] = []
    for value in values:
        item = value.strip().lower()
        if item and len(item) <= 253 and item not in seen:
            seen.append(item)
    return seen


class TaskSchedule(BaseModel):
    """When the employee's computer starts this task by itself, every week."""

    #: Weekdays, 0 = Monday ... 6 = Sunday.
    days: list[int] = Field(min_length=1, max_length=7)
    #: "HH:MM", 24-hour, on the employee's own clock.
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    minutes: int = Field(ge=5, le=12 * 60)

    @field_validator("days")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(value))

    @model_validator(mode="after")
    def _same_day(self) -> TaskSchedule:
        hour, minute = (int(part) for part in self.start.split(":"))
        if hour * 60 + minute + self.minutes > 24 * 60:
            raise ValueError("a scheduled session must end by midnight")
        return self


def schedule_of(task: object) -> TaskSchedule | None:
    days = getattr(task, "schedule_days", None)
    start = getattr(task, "schedule_time", None)
    minutes = getattr(task, "schedule_minutes", None)
    if not days or not start or not minutes:
        return None
    return TaskSchedule(days=list(days), start=start, minutes=minutes)


class TaskCreate(_Request):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: TaskCategory
    priority: TaskPriority = TaskPriority.MEDIUM
    project_id: int | None = None
    assignee_id: int | None = None
    deadline: datetime | None = None
    expected_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)
    schedule: TaskSchedule | None = None
    productive_rules: list[str] = _RULE
    neutral_rules: list[str] = _RULE
    distracting_rules: list[str] = _RULE

    @field_validator("productive_rules", "neutral_rules", "distracting_rules")
    @classmethod
    def _rules(cls, value: list[str]) -> list[str]:
        return _clean_rules(value)


class TaskUpdate(_Request):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: TaskCategory | None = None
    priority: TaskPriority | None = None
    project_id: int | None = None
    assignee_id: int | None = None
    #: Explicit flags for "set to nothing", which a None field cannot express.
    clear_project: bool = False
    clear_assignee: bool = False
    clear_deadline: bool = False
    deadline: datetime | None = None
    expected_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)
    status: TaskStatus | None = None
    schedule: TaskSchedule | None = None
    clear_schedule: bool = False
    quality_rating: float | None = Field(default=None, ge=1, le=5)
    rework_count: int | None = Field(default=None, ge=0, le=100)
    productive_rules: list[str] | None = Field(default=None, max_length=100)
    neutral_rules: list[str] | None = Field(default=None, max_length=100)
    distracting_rules: list[str] | None = Field(default=None, max_length=100)

    @field_validator("productive_rules", "neutral_rules", "distracting_rules")
    @classmethod
    def _rules(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _clean_rules(value)


class TaskScoresOut(BaseModel):
    session_count: int
    monitored_seconds: float
    focused_seconds: float
    productive_seconds: float
    neutral_seconds: float
    distracting_seconds: float
    task_focus_score: float | None
    task_productivity_score: float | None
    duration_ratio: float | None
    explanation: list[str]


class TaskOut(BaseModel):
    id: int
    title: str
    description: str | None
    category: str
    priority: str
    status: str
    project_id: int | None
    project_name: str | None
    assignee_id: int | None
    assignee_name: str | None
    deadline: datetime | None
    expected_minutes: int | None
    schedule: TaskSchedule | None = None
    productive_rules: list[str]
    neutral_rules: list[str]
    distracting_rules: list[str]
    started_at: datetime | None
    completed_at: datetime | None
    quality_rating: float | None
    rework_count: int
    on_time: bool | None
    created_at: datetime
    scores: TaskScoresOut


class TaskDetail(TaskOut):
    sessions: list[SessionBrief]
    event_seconds: dict[str, float]
    event_counts: dict[str, int]
    top_applications: list[UsageRow]
    top_domains: list[UsageRow]


# --- assignee suggestions (spec section 31) ---------------------------------


class CandidateOut(BaseModel):
    user_id: int
    name: str
    rank: int | None
    historical_score: float | None
    completed_tasks: int
    open_tasks: int
    reasons: list[str]


class SuggestionResponse(BaseModel):
    category: str
    notice: str
    ranked: list[CandidateOut]
    insufficient_data: list[CandidateOut]
    minimum_tasks: int


# --- dashboard (spec section 26) --------------------------------------------


class DepartmentStats(BaseModel):
    department_id: int | None
    name: str
    employees: int
    monitored_seconds: float
    productive_seconds: float
    distracting_seconds: float
    average_focus_score: float | None


class DayPoint(BaseModel):
    day: str
    monitored_seconds: float
    focused_seconds: float
    average_focus_score: float


class ManagerDashboard(BaseModel):
    organization: OrganizationOut
    window_days: int
    since: datetime
    until: datetime
    total_employees: int
    employees_online: int
    employees_offline: int
    active_tasks: int
    open_tasks: int
    completed_tasks_in_window: int
    totals: WindowStats
    departments: list[DepartmentStats]
    top_applications: list[UsageRow]
    top_domains: list[UsageRow]
    daily: list[DayPoint]
    employees: list[EmployeeOut]


# --- agent: the device's own assigned tasks ---------------------------------


class AgentTaskOut(BaseModel):
    """What an employee's agent needs to start a task session. Nothing else."""

    id: int
    title: str
    category: str
    status: str
    project_name: str | None
    expected_minutes: int | None
    deadline: datetime | None
    productive_rules: list[str]
    neutral_rules: list[str]
    distracting_rules: list[str]
    #: Weekly auto-start: weekdays (0 = Monday), "HH:MM" and minutes, or None.
    schedule_days: list[int] | None = None
    schedule_time: str | None = None
    schedule_minutes: int | None = None


EmployeeDetail.model_rebuild()
