"""Manager portal API (spec sections 24-31, 52).

    GET    /api/v1/manager/organization
    GET    /api/v1/manager/dashboard
    GET    /api/v1/manager/departments            POST, PATCH/DELETE /{id}
    GET    /api/v1/manager/employees              POST, GET/PATCH /{id}
    GET    /api/v1/manager/employees/{id}/strengths
    GET    /api/v1/manager/projects               POST, PATCH /{id}
    GET    /api/v1/manager/tasks                  POST, GET/PATCH /{id}
    GET    /api/v1/manager/tasks/suggestions?category=BACKEND
    GET    /api/v1/manager/reports/employees.csv
    GET    /api/v1/manager/reports/tasks.csv

WHO CAN CALL THIS
-----------------
Managers only, enforced once at the router so a route added later cannot forget
it. Every route then builds a `BusinessRepository` bound to the manager's own
organisation, which comes from their **signed token**, not from the request. An
id from another organisation finds nothing and gets the same 404 as an id that
does not exist (section 51).

WHAT A MANAGER SEES
-------------------
Durations, categories, applications, domains, attention-event totals, scores and
task outcomes (section 62). Never a window title, a URL path, a frame or any
typed content - none of the response models below has a field for one.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.business_schemas import (
    CandidateOut,
    CategoryPerformanceOut,
    ConsentOut,
    DayPoint,
    DepartmentCreate,
    DepartmentOut,
    DepartmentStats,
    DeviceSummary,
    EmployeeCreate,
    EmployeeDetail,
    EmployeeOut,
    EmployeeUpdate,
    ManagerDashboard,
    OrganizationOut,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    SessionBrief,
    StrengthReport,
    SuggestionResponse,
    TaskBrief,
    TaskCreate,
    TaskDetail,
    TaskOut,
    TaskSchedule,
    TaskScoresOut,
    TaskUpdate,
    UsageRow,
    WindowStats,
    schedule_of,
)
from focusguard_backend.api.v1.schemas import MessageResponse
from focusguard_backend.auth.dependencies import require_role
from focusguard_backend.auth.passwords import (
    NO_LOGIN_HASH,
    hash_password,
    validate_password_strength,
)
from focusguard_backend.database.session import get_db
from focusguard_backend.models.base import as_utc
from focusguard_backend.models.business import Project, Task
from focusguard_backend.models.identity import Department, Device, Organization, User
from focusguard_backend.models.telemetry import ActivityEvent, FocusSession, VisionEvent
from focusguard_backend.repositories.analytics import AnalyticsRepository
from focusguard_backend.repositories.business import (
    ONLINE_WINDOW,
    BusinessRepository,
    EmployeeWindowStats,
)
from focusguard_backend.repositories.telemetry import Scope
from focusguard_shared.business import (
    DEFAULT_WEIGHTS,
    TaskScores,
    employee_profile,
    rank_candidates,
    strength_statements,
)
from focusguard_shared.business.strengths import CandidateInput, CategoryPerformance
from focusguard_shared.constants import (
    MIN_TASKS_FOR_STRENGTH_CLAIM,
    OPEN_TASK_STATUSES,
    TaskCategory,
    TaskStatus,
    UserRole,
)
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.api.manager")

router = APIRouter(
    prefix="/manager",
    tags=["manager"],
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)

MIN_OFFSET = -12 * 60
MAX_OFFSET = 14 * 60


# --- dependencies -----------------------------------------------------------


def current_manager(user: User = Depends(require_role(UserRole.MANAGER))) -> User:
    if user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This manager account is not attached to an organisation.",
        )
    return user


def business_repository(
    manager: User = Depends(current_manager), db: Session = Depends(get_db)
) -> BusinessRepository:
    assert manager.organization_id is not None
    return BusinessRepository(db, manager.organization_id)


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


# --- time windows -----------------------------------------------------------


def local_window(
    days: int, offset_minutes: int, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """The last `days` local days, ending at the next local midnight, in UTC.

    "Today" for a manager in Karachi starts at midnight in Karachi, not at
    midnight UTC - five hours of the working day would otherwise land in the
    previous day's report.
    """
    moment = (now or datetime.now(UTC)) + timedelta(minutes=offset_minutes)
    local_end = datetime(moment.year, moment.month, moment.day, tzinfo=UTC) + timedelta(days=1)
    end = local_end - timedelta(minutes=offset_minutes)
    return end - timedelta(days=days), end


# --- converters -------------------------------------------------------------


def window_stats(entry: EmployeeWindowStats | None) -> WindowStats:
    if entry is None:
        return WindowStats()
    return WindowStats(
        sessions=entry.sessions,
        monitored_seconds=entry.monitored_seconds,
        focused_seconds=entry.focused_seconds,
        distracted_seconds=entry.distracted_seconds,
        productive_seconds=entry.productive_seconds,
        neutral_seconds=entry.neutral_seconds,
        distracting_seconds=entry.distracting_seconds,
        average_focus_score=entry.average_focus_score,
        visual_attention_score=entry.visual_attention_score,
        digital_productivity_score=entry.digital_productivity_score,
        phone_seconds=entry.event_seconds.get("PHONE_VISIBLE", 0.0),
        looking_away_seconds=entry.event_seconds.get("GAZE_AWAY", 0.0),
        face_absent_seconds=entry.event_seconds.get("FACE_ABSENT", 0.0),
        eyes_closed_seconds=entry.event_seconds.get("EYES_CLOSED", 0.0),
        head_down_seconds=entry.event_seconds.get("HEAD_DOWN", 0.0),
        multiple_faces_seconds=entry.event_seconds.get("MULTIPLE_FACES", 0.0),
        drowsiness_events=entry.event_counts.get("DROWSINESS", 0),
    )


def merge_stats(entries: list[EmployeeWindowStats]) -> WindowStats:
    """Team totals. Averages are weighted by monitored time."""
    merged = EmployeeWindowStats()
    visual_weight = digital_weight = 0.0
    visual_sum = digital_sum = 0.0

    for entry in entries:
        merged.sessions += entry.sessions
        merged.monitored_seconds += entry.monitored_seconds
        merged.focused_seconds += entry.focused_seconds
        merged.distracted_seconds += entry.distracted_seconds
        merged.weighted_focus += entry.weighted_focus
        merged.productive_seconds += entry.productive_seconds
        merged.neutral_seconds += entry.neutral_seconds
        merged.distracting_seconds += entry.distracting_seconds
        for key, value in entry.event_seconds.items():
            merged.event_seconds[key] = merged.event_seconds.get(key, 0.0) + value
        for key, count in entry.event_counts.items():
            merged.event_counts[key] = merged.event_counts.get(key, 0) + count

        weight = max(entry.monitored_seconds, 1.0)
        if entry.visual_attention_score is not None:
            visual_sum += entry.visual_attention_score * weight
            visual_weight += weight
        if entry.digital_productivity_score is not None:
            digital_sum += entry.digital_productivity_score * weight
            digital_weight += weight

    merged.visual_attention_score = visual_sum / visual_weight if visual_weight else None
    merged.digital_productivity_score = digital_sum / digital_weight if digital_weight else None
    return window_stats(merged)


def usage_rows(rows: list[dict[str, object]]) -> list[UsageRow]:
    return [
        UsageRow(
            label=str(row["label"]),
            seconds=float(row["seconds"]),  # type: ignore[arg-type]
            classification=str(row["classification"]),
        )
        for row in rows
    ]


def task_on_time(task: Task) -> bool | None:
    deadline = as_utc(task.deadline)
    if deadline is None:
        return None
    finished = as_utc(task.completed_at)
    if finished is not None:
        return finished <= deadline
    # Still open: only "late" is knowable so far.
    return False if datetime.now(UTC) > deadline else None


def scores_out(scores: TaskScores) -> TaskScoresOut:
    return TaskScoresOut(
        session_count=scores.session_count,
        monitored_seconds=scores.monitored_seconds,
        focused_seconds=scores.focused_seconds,
        productive_seconds=scores.productive_seconds,
        neutral_seconds=scores.neutral_seconds,
        distracting_seconds=scores.distracting_seconds,
        task_focus_score=scores.task_focus_score,
        task_productivity_score=scores.task_productivity_score,
        duration_ratio=scores.duration_ratio,
        explanation=scores.explanation,
    )


def _apply_schedule(task: Task, schedule: TaskSchedule | None) -> None:
    task.schedule_days = list(schedule.days) if schedule else None
    task.schedule_time = schedule.start if schedule else None
    task.schedule_minutes = schedule.minutes if schedule else None


def task_out(
    task: Task, scores: TaskScores, people: dict[int, str], projects: dict[int, str]
) -> TaskOut:
    return TaskOut(
        id=task.id,
        title=task.title,
        description=task.description,
        category=task.category,
        priority=task.priority,
        status=task.status,
        project_id=task.project_id,
        project_name=projects.get(task.project_id) if task.project_id else None,
        assignee_id=task.assignee_id,
        assignee_name=people.get(task.assignee_id) if task.assignee_id else None,
        deadline=as_utc(task.deadline),
        expected_minutes=task.expected_minutes,
        schedule=schedule_of(task),
        productive_rules=list(task.productive_rules or []),
        neutral_rules=list(task.neutral_rules or []),
        distracting_rules=list(task.distracting_rules or []),
        started_at=as_utc(task.started_at),
        completed_at=as_utc(task.completed_at),
        quality_rating=task.quality_rating,
        rework_count=task.rework_count,
        on_time=task_on_time(task),
        created_at=as_utc(task.created_at) or task.created_at,
        scores=scores_out(scores),
    )


def session_brief(session: FocusSession) -> SessionBrief:
    return SessionBrief(
        id=session.id,
        name=session.name,
        task_id=session.task_id,
        started_at=as_utc(session.started_at) or session.started_at,
        ended_at=as_utc(session.ended_at),
        duration_seconds=session.duration_seconds,
        average_focus_score=session.average_focus_score,
    )


def device_summary(device: Device, now: datetime) -> DeviceSummary:
    seen = as_utc(device.last_seen_at)
    return DeviceSummary(
        id=device.id,
        device_name=device.device_name,
        platform=device.platform,
        agent_version=device.agent_version,
        enrolled=device.enrolled_at is not None,
        revoked=device.revoked_at is not None,
        online=device.revoked_at is None and seen is not None and seen >= now - ONLINE_WINDOW,
        last_seen_at=seen,
    )


def display_name(user: User) -> str:
    return user.full_name or user.email


def employee_out(
    user: User,
    departments: dict[int, str],
    online: set[int],
    open_counts: dict[int, int],
    current: dict[int, Task],
    today: dict[int, EmployeeWindowStats],
) -> EmployeeOut:
    task = current.get(user.id)
    return EmployeeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        department_id=user.department_id,
        department_name=departments.get(user.department_id) if user.department_id else None,
        is_active=user.is_active,
        can_sign_in=user.password_hash != NO_LOGIN_HASH,
        online=user.id in online,
        open_tasks=open_counts.get(user.id, 0),
        current_task=(
            TaskBrief(id=task.id, title=task.title, category=task.category, status=task.status)
            if task
            else None
        ),
        today=window_stats(today.get(user.id)),
    )


def performance_out(performance: CategoryPerformance) -> CategoryPerformanceOut:
    return CategoryPerformanceOut(
        category=performance.category,
        completed_tasks=performance.completed_tasks,
        sufficient_data=performance.sufficient_data,
        historical_score=performance.historical_score,
        average_quality=performance.average_quality,
        on_time_rate=performance.on_time_rate,
        average_duration_ratio=performance.average_duration_ratio,
        average_focus=performance.average_focus,
        average_productivity=performance.average_productivity,
        average_rework=performance.average_rework,
        components=performance.components,
    )


def strength_report(repo: BusinessRepository, employee: User) -> StrengthReport:
    outcomes = repo.outcomes([employee.id]).get(employee.id, [])
    profile = employee_profile(outcomes)
    return StrengthReport(
        employee_id=employee.id,
        statements=strength_statements(display_name(employee), profile),
        categories=[performance_out(item) for item in profile],
        minimum_tasks=MIN_TASKS_FOR_STRENGTH_CLAIM,
        weights=DEFAULT_WEIGHTS.as_dict(),
    )


def _names(repo: BusinessRepository) -> dict[int, str]:
    return {user.id: display_name(user) for user in repo.employees()}


def _project_names(repo: BusinessRepository) -> dict[int, str]:
    return {project.id: project.name for project in repo.projects()}


# --- organisation and dashboard ---------------------------------------------


@router.get("/organization", response_model=OrganizationOut)
def organization(
    manager: User = Depends(current_manager), db: Session = Depends(get_db)
) -> OrganizationOut:
    record = db.get(Organization, manager.organization_id)
    if record is None:
        raise _not_found("Organisation")
    return OrganizationOut.model_validate(record)


@router.get("/dashboard", response_model=ManagerDashboard)
def dashboard(
    days: int = Query(1, ge=1, le=366),
    offset_minutes: int = Query(0, ge=MIN_OFFSET, le=MAX_OFFSET),
    manager: User = Depends(current_manager),
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> ManagerDashboard:
    """The section 26 overview."""
    since, until = local_window(days, offset_minutes)
    org = db.get(Organization, repo.organization_id)
    assert org is not None

    employees = repo.employees()
    active_employees = [user for user in employees if user.is_active]
    departments = repo.department_names()
    online = repo.online_user_ids()
    open_counts = repo.open_task_counts()
    current = repo.current_tasks()
    stats = repo.employee_window_stats(since, until)

    by_department: dict[int | None, list[User]] = {}
    for user in active_employees:
        by_department.setdefault(user.department_id, []).append(user)

    department_rows = []
    for department_id, members in sorted(
        by_department.items(), key=lambda item: departments.get(item[0] or -1, "~")
    ):
        member_stats = [stats[user.id] for user in members if user.id in stats]
        merged = merge_stats(member_stats)
        department_rows.append(
            DepartmentStats(
                department_id=department_id,
                name=departments.get(department_id, "No department")
                if department_id
                else "No department",
                employees=len(members),
                monitored_seconds=merged.monitored_seconds,
                productive_seconds=merged.productive_seconds,
                distracting_seconds=merged.distracting_seconds,
                average_focus_score=merged.average_focus_score,
            )
        )

    tasks = repo.tasks()
    completed_in_window = sum(
        1
        for task in tasks
        if task.status == TaskStatus.COMPLETED.value
        and task.completed_at is not None
        and since <= (as_utc(task.completed_at) or since) < until
    )

    daily = AnalyticsRepository(db).daily_totals(
        Scope.for_organization(repo.organization_id), since, until, offset_minutes
    )

    return ManagerDashboard(
        organization=OrganizationOut.model_validate(org),
        window_days=days,
        since=since,
        until=until,
        total_employees=len(active_employees),
        employees_online=len([user for user in active_employees if user.id in online]),
        employees_offline=len([user for user in active_employees if user.id not in online]),
        active_tasks=sum(1 for task in tasks if task.status == TaskStatus.IN_PROGRESS.value),
        open_tasks=sum(
            1 for task in tasks if task.status in {str(s) for s in OPEN_TASK_STATUSES}
        ),
        completed_tasks_in_window=completed_in_window,
        totals=merge_stats(list(stats.values())),
        departments=department_rows,
        top_applications=usage_rows(repo.top_usage(since, until, by="application")),
        top_domains=usage_rows(repo.top_usage(since, until, by="domain")),
        daily=[
            DayPoint(
                day=day.day.isoformat(),
                monitored_seconds=day.monitored_seconds,
                focused_seconds=day.focused_seconds,
                average_focus_score=day.average_focus_score,
            )
            for day in daily
        ],
        employees=[
            employee_out(user, departments, online, open_counts, current, stats)
            for user in active_employees
        ],
    )


# --- departments ------------------------------------------------------------


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(repo: BusinessRepository = Depends(business_repository)):
    counts: dict[int, int] = {}
    for user in repo.employees():
        if user.department_id is not None and user.is_active:
            counts[user.department_id] = counts.get(user.department_id, 0) + 1
    return [
        DepartmentOut(id=d.id, name=d.name, employee_count=counts.get(d.id, 0))
        for d in repo.departments()
    ]


@router.post("/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> DepartmentOut:
    name = payload.name.strip()
    if any(d.name.lower() == name.lower() for d in repo.departments()):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A department with that name exists.")
    department = Department(organization_id=repo.organization_id, name=name)
    db.add(department)
    db.commit()
    return DepartmentOut(id=department.id, name=department.name, employee_count=0)


@router.patch("/departments/{department_id}", response_model=DepartmentOut)
def rename_department(
    department_id: int,
    payload: DepartmentCreate,
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> DepartmentOut:
    department = repo.get_department(department_id)
    if department is None:
        raise _not_found("Department")
    department.name = payload.name.strip()
    db.commit()
    members = len([u for u in repo.employees(department_id) if u.is_active])
    return DepartmentOut(id=department.id, name=department.name, employee_count=members)


@router.delete("/departments/{department_id}", response_model=MessageResponse)
def delete_department(
    department_id: int,
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Delete a department. Its employees are kept, with no department."""
    department = repo.get_department(department_id)
    if department is None:
        raise _not_found("Department")
    for user in repo.employees(department_id):
        user.department_id = None
    db.delete(department)
    db.commit()
    return MessageResponse(detail="Department deleted. Its employees were kept.")


# --- employees (spec sections 24, 27) ---------------------------------------


def _department_or_404(repo: BusinessRepository, department_id: int | None) -> int | None:
    if department_id is None:
        return None
    if repo.get_department(department_id) is None:
        raise _not_found("Department")
    return department_id


@router.get("/employees", response_model=list[EmployeeOut])
def list_employees(
    department_id: int | None = Query(None),
    offset_minutes: int = Query(0, ge=MIN_OFFSET, le=MAX_OFFSET),
    repo: BusinessRepository = Depends(business_repository),
) -> list[EmployeeOut]:
    since, until = local_window(1, offset_minutes)
    employees = repo.employees(department_id)
    departments = repo.department_names()
    online = repo.online_user_ids()
    open_counts = repo.open_task_counts()
    current = repo.current_tasks()
    today = repo.employee_window_stats(since, until)
    return [
        employee_out(user, departments, online, open_counts, current, today)
        for user in employees
    ]


@router.post("/employees", response_model=EmployeeOut, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: EmployeeCreate,
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> EmployeeOut:
    department_id = _department_or_404(repo, payload.department_id)

    if payload.password is not None:
        problem = validate_password_strength(payload.password)
        if problem:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    email = payload.email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="That email address cannot be registered."
        )

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=(
            hash_password(payload.password) if payload.password is not None else NO_LOGIN_HASH
        ),
        role=UserRole.EMPLOYEE.value,
        organization_id=repo.organization_id,
        department_id=department_id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Employee %d created in organisation %d", user.id, repo.organization_id)

    return employee_out(user, repo.department_names(), set(), {}, {}, {})


@router.get("/employees/{employee_id}", response_model=EmployeeDetail)
def employee_detail(
    employee_id: int,
    days: int = Query(7, ge=1, le=366),
    offset_minutes: int = Query(0, ge=MIN_OFFSET, le=MAX_OFFSET),
    repo: BusinessRepository = Depends(business_repository),
) -> EmployeeDetail:
    """Spec section 27, for one employee of the caller's organisation."""
    employee = repo.get_employee(employee_id)
    if employee is None:
        raise _not_found("Employee")

    now = datetime.now(UTC)
    since, until = local_window(days, offset_minutes, now)
    today_since, today_until = local_window(1, offset_minutes, now)

    base = employee_out(
        employee,
        repo.department_names(),
        repo.online_user_ids(now),
        repo.open_task_counts(),
        repo.current_tasks(),
        repo.employee_window_stats(today_since, today_until, [employee.id]),
    )
    window = repo.employee_window_stats(since, until, [employee.id]).get(employee.id)

    tasks = repo.tasks(assignee_id=employee.id)
    scores = repo.task_scores(tasks)
    names = {employee.id: display_name(employee)}
    projects = _project_names(repo)

    return EmployeeDetail(
        **base.model_dump(),
        window_days=days,
        window=window_stats(window),
        devices=[device_summary(device, now) for device in repo.devices(employee.id)],
        consents=[
            ConsentOut(
                consent_version=consent.consent_version,
                granted_at=as_utc(consent.granted_at) or consent.granted_at,
                revoked_at=as_utc(consent.revoked_at),
            )
            for consent in repo.consents(employee.id)
        ],
        top_applications=usage_rows(
            repo.top_usage(since, until, by="application", user_id=employee.id)
        ),
        top_domains=usage_rows(repo.top_usage(since, until, by="domain", user_id=employee.id)),
        recent_sessions=[session_brief(s) for s in repo.recent_sessions(employee.id)],
        tasks=[task_out(task, scores[task.id], names, projects) for task in tasks],
        strengths=strength_report(repo, employee),
    )


@router.patch("/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> EmployeeOut:
    employee = repo.get_employee(employee_id)
    if employee is None:
        raise _not_found("Employee")

    if payload.full_name is not None:
        employee.full_name = payload.full_name.strip()
    if payload.clear_department:
        employee.department_id = None
    elif payload.department_id is not None:
        employee.department_id = _department_or_404(repo, payload.department_id)
    if payload.is_active is not None:
        # Deactivation ends portal access at once (get_current_user checks it).
        # Their devices keep their history but can no longer be linked to work.
        employee.is_active = payload.is_active
    if payload.password is not None:
        problem = validate_password_strength(payload.password)
        if problem:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
        employee.password_hash = hash_password(payload.password)

    db.commit()
    return employee_out(
        employee,
        repo.department_names(),
        repo.online_user_ids(),
        repo.open_task_counts(),
        repo.current_tasks(),
        {},
    )


@router.get("/employees/{employee_id}/strengths", response_model=StrengthReport)
def employee_strengths(
    employee_id: int, repo: BusinessRepository = Depends(business_repository)
) -> StrengthReport:
    """Spec section 30: per-category history, with plain-language statements."""
    employee = repo.get_employee(employee_id)
    if employee is None:
        raise _not_found("Employee")
    return strength_report(repo, employee)


# --- projects ---------------------------------------------------------------


def _project_out(project: Project, tasks: list[Task]) -> ProjectOut:
    own = [task for task in tasks if task.project_id == project.id]
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        is_active=project.is_active,
        open_tasks=sum(1 for t in own if t.status in {str(s) for s in OPEN_TASK_STATUSES}),
        completed_tasks=sum(1 for t in own if t.status == TaskStatus.COMPLETED.value),
    )


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(repo: BusinessRepository = Depends(business_repository)) -> list[ProjectOut]:
    tasks = repo.tasks()
    return [_project_out(project, tasks) for project in repo.projects()]


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> ProjectOut:
    project = Project(
        organization_id=repo.organization_id,
        name=payload.name.strip(),
        description=payload.description,
        is_active=True,
    )
    db.add(project)
    db.commit()
    return _project_out(project, [])


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> ProjectOut:
    project = repo.get_project(project_id)
    if project is None:
        raise _not_found("Project")
    if payload.name is not None:
        project.name = payload.name.strip()
    if payload.description is not None:
        project.description = payload.description
    if payload.is_active is not None:
        project.is_active = payload.is_active
    db.commit()
    return _project_out(project, repo.tasks(project_id=project.id))


# --- tasks (spec sections 28, 29) -------------------------------------------


def _assignee_or_404(repo: BusinessRepository, assignee_id: int | None) -> int | None:
    if assignee_id is None:
        return None
    employee = repo.get_employee(assignee_id)
    if employee is None or not employee.is_active:
        raise _not_found("Employee")
    return assignee_id


def _project_or_404(repo: BusinessRepository, project_id: int | None) -> int | None:
    if project_id is None:
        return None
    if repo.get_project(project_id) is None:
        raise _not_found("Project")
    return project_id


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    status_filter: TaskStatus | None = Query(None, alias="status"),
    assignee_id: int | None = Query(None),
    project_id: int | None = Query(None),
    category: TaskCategory | None = Query(None),
    repo: BusinessRepository = Depends(business_repository),
) -> list[TaskOut]:
    tasks = repo.tasks(
        status=status_filter.value if status_filter else None,
        assignee_id=assignee_id,
        project_id=project_id,
        category=category.value if category else None,
    )
    scores = repo.task_scores(tasks)
    names, projects = _names(repo), _project_names(repo)
    return [task_out(task, scores[task.id], names, projects) for task in tasks]


@router.get("/tasks/suggestions", response_model=SuggestionResponse)
def suggest_assignees(
    category: TaskCategory = Query(...),
    repo: BusinessRepository = Depends(business_repository),
) -> SuggestionResponse:
    """Spec section 31: who has the strongest completed-task record for this kind of work.

    Declared before `/tasks/{task_id}` so "suggestions" is not parsed as an id.
    """
    employees = [user for user in repo.employees() if user.is_active]
    outcomes = repo.outcomes([user.id for user in employees])
    open_counts = repo.open_task_counts()

    ranking = rank_candidates(
        category.value,
        [
            CandidateInput(
                user_id=user.id,
                name=display_name(user),
                outcomes=outcomes.get(user.id, []),
                open_tasks=open_counts.get(user.id, 0),
            )
            for user in employees
        ],
    )

    def out(candidate) -> CandidateOut:
        return CandidateOut(
            user_id=candidate.user_id,
            name=candidate.name,
            rank=candidate.rank,
            historical_score=candidate.performance.historical_score,
            completed_tasks=candidate.performance.completed_tasks,
            open_tasks=candidate.open_tasks,
            reasons=candidate.reasons,
        )

    return SuggestionResponse(
        category=ranking.category,
        notice=ranking.notice,
        ranked=[out(c) for c in ranking.ranked],
        insufficient_data=[out(c) for c in ranking.insufficient_data],
        minimum_tasks=MIN_TASKS_FOR_STRENGTH_CLAIM,
    )


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    manager: User = Depends(current_manager),
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> TaskOut:
    task = Task(
        organization_id=repo.organization_id,
        project_id=_project_or_404(repo, payload.project_id),
        title=payload.title.strip(),
        description=payload.description,
        category=payload.category.value,
        priority=payload.priority.value,
        status=TaskStatus.TODO.value,
        deadline=payload.deadline,
        expected_minutes=payload.expected_minutes,
        assignee_id=_assignee_or_404(repo, payload.assignee_id),
        created_by_id=manager.id,
        productive_rules=payload.productive_rules,
        neutral_rules=payload.neutral_rules,
        distracting_rules=payload.distracting_rules,
        rework_count=0,
    )
    _apply_schedule(task, payload.schedule)
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("Task %d created in organisation %d", task.id, repo.organization_id)
    return task_out(task, repo.task_scores([task])[task.id], _names(repo), _project_names(repo))


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def task_detail(
    task_id: int,
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> TaskDetail:
    """Spec section 29: what monitoring recorded while this task was being worked on."""
    task = repo.get_task(task_id)
    if task is None:
        raise _not_found("Task")

    sessions = repo.task_sessions(task.id)
    base = task_out(task, repo.task_scores([task])[task.id], _names(repo), _project_names(repo))

    event_seconds: dict[str, float] = {}
    event_counts: dict[str, int] = {}
    if sessions:
        for event in db.scalars(
            select(VisionEvent).where(
                VisionEvent.organization_id == repo.organization_id,
                VisionEvent.session_id.in_([s.id for s in sessions]),
            )
        ):
            event_seconds[event.event_type] = (
                event_seconds.get(event.event_type, 0.0) + event.duration_seconds
            )
            event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1

    applications = _task_usage(repo, db, [s.id for s in sessions], "application")
    domains = _task_usage(repo, db, [s.id for s in sessions], "domain")

    return TaskDetail(
        **base.model_dump(),
        sessions=[session_brief(s) for s in sessions],
        event_seconds=event_seconds,
        event_counts=event_counts,
        top_applications=applications,
        top_domains=domains,
    )


def _task_usage(
    repo: BusinessRepository, db: Session, session_ids: list[int], by: str
) -> list[UsageRow]:
    """Application or domain totals for a task's own sessions."""
    if not session_ids:
        return []

    column = ActivityEvent.domain if by == "domain" else ActivityEvent.application_name
    statement = (
        select(column, ActivityEvent.classification, func.sum(ActivityEvent.duration_seconds))
        .where(
            ActivityEvent.organization_id == repo.organization_id,
            ActivityEvent.session_id.in_(session_ids),
        )
        .group_by(column, ActivityEvent.classification)
    )
    if by == "domain":
        statement = statement.where(ActivityEvent.domain.is_not(None))

    totals: dict[str, dict[str, float]] = {}
    for label, classification, seconds in db.execute(statement):
        totals.setdefault(label, {})[classification] = float(seconds or 0.0)

    rows = [
        UsageRow(
            label=label,
            seconds=sum(split.values()),
            classification=max(split.items(), key=lambda item: item[1])[0],
        )
        for label, split in totals.items()
    ]
    return sorted(rows, key=lambda row: -row.seconds)[:8]


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    repo: BusinessRepository = Depends(business_repository),
    db: Session = Depends(get_db),
) -> TaskOut:
    task = repo.get_task(task_id)
    if task is None:
        raise _not_found("Task")

    if payload.title is not None:
        task.title = payload.title.strip()
    if payload.description is not None:
        task.description = payload.description
    if payload.category is not None:
        task.category = payload.category.value
    if payload.priority is not None:
        task.priority = payload.priority.value
    if payload.clear_project:
        task.project_id = None
    elif payload.project_id is not None:
        task.project_id = _project_or_404(repo, payload.project_id)
    if payload.clear_assignee:
        task.assignee_id = None
    elif payload.assignee_id is not None:
        task.assignee_id = _assignee_or_404(repo, payload.assignee_id)
    if payload.clear_deadline:
        task.deadline = None
    elif payload.deadline is not None:
        task.deadline = payload.deadline
    if payload.expected_minutes is not None:
        task.expected_minutes = payload.expected_minutes
    if payload.clear_schedule:
        _apply_schedule(task, None)
    elif payload.schedule is not None:
        _apply_schedule(task, payload.schedule)
    if payload.rework_count is not None:
        task.rework_count = payload.rework_count
    for field_name in ("productive_rules", "neutral_rules", "distracting_rules"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(task, field_name, value)

    now = datetime.now(UTC)
    if payload.status is not None and payload.status.value != task.status:
        task.status = payload.status.value
        if payload.status is TaskStatus.IN_PROGRESS and task.started_at is None:
            task.started_at = now
        if payload.status is TaskStatus.COMPLETED:
            task.completed_at = now
            task.started_at = task.started_at or now
        elif task.completed_at is not None:
            # Reopened: it is no longer a completed outcome, so it must stop
            # counting toward strength analytics.
            task.completed_at = None

    if payload.quality_rating is not None:
        if task.status != TaskStatus.COMPLETED.value:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A quality rating can only be given to a completed task.",
            )
        task.quality_rating = payload.quality_rating

    db.commit()
    db.refresh(task)
    return task_out(task, repo.task_scores([task])[task.id], _names(repo), _project_names(repo))


# --- exports (spec section 52) ----------------------------------------------


def _csv_response(columns: list[str], rows: list[dict[str, object]], name: str):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return StreamingResponse(
        # BOM so Excel on Windows reads UTF-8; see reports.py.
        iter([buffer.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="focusguard-{name}-{stamp}.csv"'},
    )


def _minutes(seconds: float) -> float:
    return round(seconds / 60.0, 1)


def _round(value: float | None) -> object:
    return "" if value is None else round(value, 1)


@router.get("/reports/employees.csv")
def export_employees(
    days: int = Query(7, ge=1, le=366),
    offset_minutes: int = Query(0, ge=MIN_OFFSET, le=MAX_OFFSET),
    repo: BusinessRepository = Depends(business_repository),
):
    """One row per employee: the Employee Productivity / Team Weekly report."""
    since, until = local_window(days, offset_minutes)
    stats = repo.employee_window_stats(since, until)
    departments = repo.department_names()
    open_counts = repo.open_task_counts()
    completed = [
        task
        for task in repo.tasks(status=TaskStatus.COMPLETED.value)
        if task.completed_at is not None and since <= (as_utc(task.completed_at) or since) < until
    ]

    rows = []
    for user in repo.employees():
        entry = window_stats(stats.get(user.id))
        rows.append(
            {
                "employee_id": user.id,
                "name": display_name(user),
                "department": departments.get(user.department_id, "")
                if user.department_id
                else "",
                "sessions": entry.sessions,
                "monitored_minutes": _minutes(entry.monitored_seconds),
                "focused_minutes": _minutes(entry.focused_seconds),
                "productive_minutes": _minutes(entry.productive_seconds),
                "neutral_minutes": _minutes(entry.neutral_seconds),
                "distracting_minutes": _minutes(entry.distracting_seconds),
                "average_focus_score": _round(entry.average_focus_score),
                "visual_attention_score": _round(entry.visual_attention_score),
                "digital_productivity_score": _round(entry.digital_productivity_score),
                "phone_minutes": _minutes(entry.phone_seconds),
                "looking_away_minutes": _minutes(entry.looking_away_seconds),
                "away_from_desk_minutes": _minutes(entry.face_absent_seconds),
                "eyes_closed_minutes": _minutes(entry.eyes_closed_seconds),
                "head_down_minutes": _minutes(entry.head_down_seconds),
                "someone_else_in_frame_minutes": _minutes(entry.multiple_faces_seconds),
                "drowsiness_events": entry.drowsiness_events,
                "open_tasks": open_counts.get(user.id, 0),
                "completed_tasks": sum(1 for t in completed if t.assignee_id == user.id),
            }
        )
    return _csv_response(list(rows[0].keys()) if rows else ["employee_id"], rows, "employees")


@router.get("/reports/tasks.csv")
def export_tasks(repo: BusinessRepository = Depends(business_repository)):
    """One row per task: the Employee Task Report."""
    tasks = repo.tasks()
    scores = repo.task_scores(tasks)
    names, projects = _names(repo), _project_names(repo)
    columns = [
        "task_id", "title", "category", "priority", "status", "project", "assignee",
        "deadline", "expected_minutes", "completed_at", "on_time", "quality_rating",
        "rework_count", "monitored_minutes", "task_focus_score", "task_productivity_score",
    ]
    rows = []
    for task in tasks:
        out = task_out(task, scores[task.id], names, projects)
        rows.append(
            {
                "task_id": out.id,
                "title": out.title,
                "category": out.category,
                "priority": out.priority,
                "status": out.status,
                "project": out.project_name or "",
                "assignee": out.assignee_name or "",
                "deadline": out.deadline.isoformat() if out.deadline else "",
                "expected_minutes": out.expected_minutes or "",
                "completed_at": out.completed_at.isoformat() if out.completed_at else "",
                "on_time": "" if out.on_time is None else ("yes" if out.on_time else "no"),
                "quality_rating": _round(out.quality_rating),
                "rework_count": out.rework_count,
                "monitored_minutes": _minutes(out.scores.monitored_seconds),
                "task_focus_score": _round(out.scores.task_focus_score),
                "task_productivity_score": _round(out.scores.task_productivity_score),
            }
        )
    return _csv_response(columns, rows, "tasks")
