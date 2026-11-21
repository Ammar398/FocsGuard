"""Business-mode data access (spec sections 24-31, 51).

ONE ORGANISATION PER REPOSITORY
-------------------------------
`BusinessRepository(db, organization_id)` is bound to a single tenant when it is
built, and every query below filters on that id. There is no method that takes
an organisation as a parameter and no way to construct the repository without
one - the same structural defence as `Scope` in `telemetry.py`, applied to the
manager side, where every read is about colleagues and a missing filter would
show a stranger's staff.

Lookups by id (`get_task`, `get_employee`, ...) return None for a row in another
organisation, exactly as they do for a row that does not exist. The route turns
both into the same 404, so an id cannot be used to probe another tenant.

AGGREGATED IN SQL
-----------------
Window statistics group in the database and return totals; only per-task facts
are assembled in Python, because strength analytics needs them per task.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from focusguard_backend.models.base import as_utc
from focusguard_backend.models.business import Project, Task
from focusguard_backend.models.identity import Department, Device, PrivacyConsent, User
from focusguard_backend.models.telemetry import (
    ActivityEvent,
    FocusScore,
    FocusSession,
    VisionEvent,
)
from focusguard_shared.business import (
    TaskOutcome,
    TaskScores,
    TaskSessionFacts,
    score_task,
)
from focusguard_shared.constants import OPEN_TASK_STATUSES, TaskStatus, UserRole

#: A device seen within this long is "working" on the manager board. The agent
#: uploads or heartbeats every 30 s, so two minutes tolerates three missed beats
#: before calling someone offline.
ONLINE_WINDOW = timedelta(minutes=2)


@dataclass
class EmployeeWindowStats:
    """One employee's totals over a reporting window."""

    sessions: int = 0
    monitored_seconds: float = 0.0
    focused_seconds: float = 0.0
    distracted_seconds: float = 0.0
    weighted_focus: float = 0.0  # sum(score * duration); divided on read
    productive_seconds: float = 0.0
    neutral_seconds: float = 0.0
    distracting_seconds: float = 0.0
    visual_attention_score: float | None = None
    digital_productivity_score: float | None = None
    event_seconds: dict[str, float] = field(default_factory=dict)
    event_counts: dict[str, int] = field(default_factory=dict)

    @property
    def average_focus_score(self) -> float | None:
        if self.monitored_seconds <= 0:
            return None
        return self.weighted_focus / self.monitored_seconds


class BusinessRepository:
    """All manager-side reads, bound to one organisation."""

    def __init__(self, db: Session, organization_id: int) -> None:
        if organization_id is None:
            raise ValueError(
                "A BusinessRepository must be bound to an organisation; an unbound "
                "one would read across tenants."
            )
        self._db = db
        self.organization_id = organization_id

    # -- departments -------------------------------------------------------

    def departments(self) -> list[Department]:
        return list(
            self._db.scalars(
                select(Department)
                .where(Department.organization_id == self.organization_id)
                .order_by(Department.name)
            )
        )

    def get_department(self, department_id: int) -> Department | None:
        return self._db.scalar(
            select(Department).where(
                Department.id == department_id,
                Department.organization_id == self.organization_id,
            )
        )

    def department_names(self) -> dict[int, str]:
        return {department.id: department.name for department in self.departments()}

    # -- employees ---------------------------------------------------------

    def employees(self, department_id: int | None = None) -> list[User]:
        statement = select(User).where(
            User.organization_id == self.organization_id,
            User.role == UserRole.EMPLOYEE.value,
        )
        if department_id is not None:
            statement = statement.where(User.department_id == department_id)
        return list(self._db.scalars(statement.order_by(User.full_name, User.email)))

    def get_employee(self, user_id: int) -> User | None:
        return self._db.scalar(
            select(User).where(
                User.id == user_id,
                User.organization_id == self.organization_id,
                User.role == UserRole.EMPLOYEE.value,
            )
        )

    def consents(self, user_id: int) -> list[PrivacyConsent]:
        # Joined through the user so a consent row can only be read for a member
        # of this organisation.
        return list(
            self._db.scalars(
                select(PrivacyConsent)
                .join(User, User.id == PrivacyConsent.user_id)
                .where(
                    PrivacyConsent.user_id == user_id,
                    User.organization_id == self.organization_id,
                )
                .order_by(PrivacyConsent.granted_at.desc())
            )
        )

    # -- devices -----------------------------------------------------------

    def devices(self, user_id: int | None = None) -> list[Device]:
        statement = select(Device).where(Device.organization_id == self.organization_id)
        if user_id is not None:
            statement = statement.where(Device.user_id == user_id)
        return list(self._db.scalars(statement.order_by(Device.device_name)))

    def online_user_ids(self, now: datetime | None = None) -> set[int]:
        cutoff = (now or datetime.now(UTC)) - ONLINE_WINDOW
        rows = self._db.scalars(
            select(Device.user_id).where(
                Device.organization_id == self.organization_id,
                Device.revoked_at.is_(None),
                Device.last_seen_at >= cutoff,
            )
        )
        return {user_id for user_id in rows if user_id is not None}

    # -- projects ----------------------------------------------------------

    def projects(self) -> list[Project]:
        return list(
            self._db.scalars(
                select(Project)
                .where(Project.organization_id == self.organization_id)
                .order_by(Project.name)
            )
        )

    def get_project(self, project_id: int) -> Project | None:
        return self._db.scalar(
            select(Project).where(
                Project.id == project_id, Project.organization_id == self.organization_id
            )
        )

    # -- tasks -------------------------------------------------------------

    def tasks(
        self,
        status: str | None = None,
        assignee_id: int | None = None,
        project_id: int | None = None,
        category: str | None = None,
    ) -> list[Task]:
        statement = select(Task).where(Task.organization_id == self.organization_id)
        if status is not None:
            statement = statement.where(Task.status == status)
        if assignee_id is not None:
            statement = statement.where(Task.assignee_id == assignee_id)
        if project_id is not None:
            statement = statement.where(Task.project_id == project_id)
        if category is not None:
            statement = statement.where(Task.category == category)
        return list(self._db.scalars(statement.order_by(Task.created_at.desc(), Task.id.desc())))

    def get_task(self, task_id: int) -> Task | None:
        return self._db.scalar(
            select(Task).where(Task.id == task_id, Task.organization_id == self.organization_id)
        )

    def open_task_counts(self) -> dict[int, int]:
        rows = self._db.execute(
            select(Task.assignee_id, func.count(Task.id))
            .where(
                Task.organization_id == self.organization_id,
                Task.assignee_id.is_not(None),
                Task.status.in_([str(status) for status in OPEN_TASK_STATUSES]),
            )
            .group_by(Task.assignee_id)
        )
        return {user_id: int(count) for user_id, count in rows}

    def current_tasks(self) -> dict[int, Task]:
        """Each employee's in-progress task, most recently started if several."""
        current: dict[int, Task] = {}
        for task in self.tasks(status=TaskStatus.IN_PROGRESS.value):
            if task.assignee_id is None:
                continue
            existing = current.get(task.assignee_id)
            started = as_utc(task.started_at) or datetime.min.replace(tzinfo=UTC)
            if existing is None or started > (
                as_utc(existing.started_at) or datetime.min.replace(tzinfo=UTC)
            ):
                current[task.assignee_id] = task
        return current

    # -- task sessions and scores (spec section 29) ------------------------

    def task_sessions(self, task_id: int) -> list[FocusSession]:
        return list(
            self._db.scalars(
                select(FocusSession)
                .where(
                    FocusSession.organization_id == self.organization_id,
                    FocusSession.task_id == task_id,
                )
                .order_by(FocusSession.started_at)
            )
        )

    def _task_session_facts(self, task_ids: list[int]) -> dict[int, list[TaskSessionFacts]]:
        if not task_ids:
            return {}

        sessions = list(
            self._db.scalars(
                select(FocusSession).where(
                    FocusSession.organization_id == self.organization_id,
                    FocusSession.task_id.in_(task_ids),
                )
            )
        )
        if not sessions:
            return {}

        classified = self._classification_by_session([session.id for session in sessions])

        facts: dict[int, list[TaskSessionFacts]] = defaultdict(list)
        for session in sessions:
            seconds = classified.get(session.id, {})
            if session.raw_events_pruned_at is not None:
                # Raw rows are gone; the retention job left the totals behind.
                seconds = {
                    "PRODUCTIVE": session.productive_seconds or 0.0,
                    "NEUTRAL": session.neutral_seconds or 0.0,
                    "DISTRACTING": session.distracting_seconds or 0.0,
                }
            assert session.task_id is not None
            facts[session.task_id].append(
                TaskSessionFacts(
                    duration_seconds=session.duration_seconds,
                    average_focus_score=session.average_focus_score,
                    focused_seconds=session.focused_seconds,
                    productive_seconds=seconds.get("PRODUCTIVE", 0.0),
                    neutral_seconds=seconds.get("NEUTRAL", 0.0),
                    distracting_seconds=seconds.get("DISTRACTING", 0.0),
                )
            )
        return dict(facts)

    def _classification_by_session(self, session_ids: list[int]) -> dict[int, dict[str, float]]:
        rows = self._db.execute(
            select(
                ActivityEvent.session_id,
                ActivityEvent.classification,
                func.sum(ActivityEvent.duration_seconds),
            )
            .where(
                ActivityEvent.organization_id == self.organization_id,
                ActivityEvent.session_id.in_(session_ids),
            )
            .group_by(ActivityEvent.session_id, ActivityEvent.classification)
        )
        result: dict[int, dict[str, float]] = defaultdict(dict)
        for session_id, classification, seconds in rows:
            result[session_id][classification] = float(seconds or 0.0)
        return dict(result)

    def task_scores(self, tasks: Iterable[Task]) -> dict[int, TaskScores]:
        task_list = list(tasks)
        facts = self._task_session_facts([task.id for task in task_list])
        return {
            task.id: score_task(facts.get(task.id, []), expected_minutes=task.expected_minutes)
            for task in task_list
        }

    # -- strength analytics inputs (spec section 30) -----------------------

    def outcomes(self, user_ids: Iterable[int] | None = None) -> dict[int, list[TaskOutcome]]:
        """Completed tasks, as strength-analytics inputs, grouped by assignee."""
        statement = select(Task).where(
            Task.organization_id == self.organization_id,
            Task.status == TaskStatus.COMPLETED.value,
            Task.assignee_id.is_not(None),
        )
        if user_ids is not None:
            statement = statement.where(Task.assignee_id.in_(list(user_ids)))

        completed = list(self._db.scalars(statement))
        scores = self.task_scores(completed)

        grouped: dict[int, list[TaskOutcome]] = defaultdict(list)
        for task in completed:
            score = scores[task.id]
            deadline = as_utc(task.deadline)
            finished = as_utc(task.completed_at)
            on_time = (
                finished <= deadline if deadline is not None and finished is not None else None
            )
            assert task.assignee_id is not None
            grouped[task.assignee_id].append(
                TaskOutcome(
                    task_id=task.id,
                    category=task.category,
                    quality_rating=task.quality_rating,
                    on_time=on_time,
                    duration_ratio=score.duration_ratio,
                    focus_score=score.task_focus_score,
                    productivity_score=score.task_productivity_score,
                    rework_count=task.rework_count,
                )
            )
        return dict(grouped)

    # -- window statistics (spec sections 26, 27) --------------------------

    def employee_window_stats(
        self,
        since: datetime,
        until: datetime,
        user_ids: Iterable[int] | None = None,
    ) -> dict[int, EmployeeWindowStats]:
        """Totals per employee for sessions started in the window."""
        ids = list(user_ids) if user_ids is not None else None
        stats: dict[int, EmployeeWindowStats] = defaultdict(EmployeeWindowStats)

        def scoped(statement, model, time_column):
            statement = statement.where(
                model.organization_id == self.organization_id,
                time_column >= since,
                time_column < until,
            )
            if ids is not None:
                statement = statement.where(model.user_id.in_(ids))
            return statement

        sessions = self._db.execute(
            scoped(
                select(
                    FocusSession.user_id,
                    func.count(FocusSession.id),
                    func.sum(FocusSession.duration_seconds),
                    func.sum(FocusSession.focused_seconds),
                    func.sum(FocusSession.distracted_seconds),
                    func.sum(FocusSession.average_focus_score * FocusSession.duration_seconds),
                ),
                FocusSession,
                FocusSession.started_at,
            ).group_by(FocusSession.user_id)
        )
        for user_id, count, duration, focused, distracted, weighted in sessions:
            entry = stats[user_id]
            entry.sessions = int(count or 0)
            entry.monitored_seconds = float(duration or 0.0)
            entry.focused_seconds = float(focused or 0.0)
            entry.distracted_seconds = float(distracted or 0.0)
            entry.weighted_focus = float(weighted or 0.0)

        activity = self._db.execute(
            scoped(
                select(
                    ActivityEvent.user_id,
                    ActivityEvent.classification,
                    func.sum(ActivityEvent.duration_seconds),
                ),
                ActivityEvent,
                ActivityEvent.start_time,
            ).group_by(ActivityEvent.user_id, ActivityEvent.classification)
        )
        for user_id, classification, seconds in activity:
            entry = stats[user_id]
            value = float(seconds or 0.0)
            if classification == "PRODUCTIVE":
                entry.productive_seconds += value
            elif classification == "DISTRACTING":
                entry.distracting_seconds += value
            else:
                entry.neutral_seconds += value

        events = self._db.execute(
            scoped(
                select(
                    VisionEvent.user_id,
                    VisionEvent.event_type,
                    func.sum(VisionEvent.duration_seconds),
                    func.count(VisionEvent.id),
                ),
                VisionEvent,
                VisionEvent.start_time,
            ).group_by(VisionEvent.user_id, VisionEvent.event_type)
        )
        for user_id, event_type, seconds, count in events:
            entry = stats[user_id]
            entry.event_seconds[event_type] = float(seconds or 0.0)
            entry.event_counts[event_type] = int(count or 0)

        scores = self._db.execute(
            scoped(
                select(
                    FocusScore.user_id,
                    func.avg(FocusScore.visual_attention_score),
                    func.avg(FocusScore.digital_productivity_score),
                ),
                FocusScore,
                FocusScore.recorded_at,
            ).group_by(FocusScore.user_id)
        )
        for user_id, visual, digital in scores:
            entry = stats[user_id]
            entry.visual_attention_score = float(visual) if visual is not None else None
            # AVG ignores NULLs, so an unmeasured productivity stays None rather
            # than dragging the mean toward zero (see FocusScore).
            entry.digital_productivity_score = float(digital) if digital is not None else None

        return dict(stats)

    def top_usage(
        self,
        since: datetime,
        until: datetime,
        by: str,
        user_id: int | None = None,
        limit: int = 8,
    ) -> list[dict[str, object]]:
        """Most-used applications (`by="application"`) or domains (`by="domain"`).

        Each row carries the classification that accounted for most of its time,
        so a chart can colour it - and a manager sees *why* a domain counts as a
        distraction (it is the task's rule, not the domain's name).
        """
        column = ActivityEvent.domain if by == "domain" else ActivityEvent.application_name
        statement = select(
            column,
            ActivityEvent.classification,
            func.sum(ActivityEvent.duration_seconds),
        ).where(
            ActivityEvent.organization_id == self.organization_id,
            ActivityEvent.start_time >= since,
            ActivityEvent.start_time < until,
        )
        if by == "domain":
            statement = statement.where(ActivityEvent.domain.is_not(None))
        if user_id is not None:
            statement = statement.where(ActivityEvent.user_id == user_id)

        totals: dict[str, dict[str, float]] = defaultdict(dict)
        for label, classification, seconds in self._db.execute(
            statement.group_by(column, ActivityEvent.classification)
        ):
            totals[label][classification] = float(seconds or 0.0)

        rows = [
            {
                "label": label,
                "seconds": sum(split.values()),
                "classification": max(split.items(), key=lambda item: item[1])[0],
                "split": split,
            }
            for label, split in totals.items()
        ]
        rows.sort(key=lambda row: -float(row["seconds"]))  # type: ignore[arg-type]
        return rows[:limit]

    def recent_sessions(self, user_id: int, limit: int = 10) -> list[FocusSession]:
        return list(
            self._db.scalars(
                select(FocusSession)
                .where(
                    FocusSession.organization_id == self.organization_id,
                    FocusSession.user_id == user_id,
                )
                .order_by(FocusSession.started_at.desc())
                .limit(limit)
            )
        )
