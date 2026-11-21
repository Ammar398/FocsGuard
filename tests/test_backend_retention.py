"""Server-side retention (spec section 45).

The property that matters: pruning raw rows must not change any answer a
manager or student gets about an old task or session - only the timeline detail
goes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from focusguard_backend.database.session import (
    create_all,
    get_engine,
    get_session_factory,
    reset_engine,
)
from focusguard_backend.models import FocusSession, Organization, Task, User
from focusguard_backend.models.telemetry import ActivityEvent, FocusScore, VisionEvent
from focusguard_backend.repositories.business import BusinessRepository
from focusguard_backend.repositories.telemetry import TelemetryRepository
from focusguard_backend.tasks.retention import prune_raw_events

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "retention.db"))
    reset_engine()
    create_all(get_engine())
    session = get_session_factory()()
    yield session
    session.close()
    reset_engine()


def _session(db, user, org, task, started, *, ended=True) -> FocusSession:
    repo = TelemetryRepository(db)
    session = repo.create_session(
        session_uuid=f"s-{started.isoformat()}", user_id=user.id, name="work",
        started_at=started, organization_id=org.id, task_id=task.id,
    )
    repo.ingest_activity_events(session, [
        {"event_uuid": f"a1-{session.id}", "application_name": "Code", "category": "DEVELOPMENT",
         "classification": "PRODUCTIVE", "rule_source": "TASK_RULE", "start_time": started,
         "end_time": started + timedelta(minutes=40), "duration_seconds": 2400.0},
        {"event_uuid": f"a2-{session.id}", "application_name": "Chrome", "domain": "netflix.com",
         "category": "ENTERTAINMENT", "classification": "DISTRACTING",
         "rule_source": "TASK_RULE", "start_time": started,
         "end_time": started + timedelta(minutes=10), "duration_seconds": 600.0},
    ])
    repo.ingest_vision_events(session, [
        {"event_uuid": f"v-{session.id}", "event_type": "PHONE_VISIBLE", "start_time": started,
         "end_time": started + timedelta(seconds=20), "duration_seconds": 20.0,
         "confidence": 0.9},
    ])
    repo.ingest_scores(session, [
        {"recorded_at": started, "visual_attention_score": 80.0, "combined_focus_score": 80.0},
    ])
    if ended:
        repo.complete_session(
            session, ended_at=started + timedelta(minutes=50), duration_seconds=3000.0,
            average_focus_score=80.0,
        )
    db.commit()
    return session


@pytest.fixture
def world(db):
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.flush()
    user = User(email="e@acme.example.com", password_hash="x", role="EMPLOYEE",
                organization_id=org.id)
    db.add(user)
    db.flush()
    task = Task(organization_id=org.id, title="Old API", category="BACKEND", status="COMPLETED",
                assignee_id=user.id, expected_minutes=50, quality_rating=4.0,
                productive_rules=[], neutral_rules=[], distracting_rules=[])
    db.add(task)
    db.flush()
    old = _session(db, user, org, task, NOW - timedelta(days=60))
    recent = _session(db, user, org, task, NOW - timedelta(days=5))
    running = _session(db, user, org, task, NOW - timedelta(days=90), ended=False)
    return {"org": org, "task": task, "old": old, "recent": recent, "running": running}


def _rows(db, model, session_id) -> int:
    return db.query(model).filter(model.session_id == session_id).count()


def test_only_ended_sessions_past_the_window_are_pruned(db, world) -> None:
    result = prune_raw_events(db, 30, now=NOW)

    assert result.sessions == 1
    for model in (VisionEvent, ActivityEvent, FocusScore):
        assert _rows(db, model, world["old"].id) == 0
        expected = 2 if model is ActivityEvent else 1
        assert _rows(db, model, world["recent"].id) == expected
        # A session that never ended is never pruned under itself.
        assert _rows(db, model, world["running"].id) > 0


def test_task_scores_survive_pruning(db, world) -> None:
    repo = BusinessRepository(db, world["org"].id)
    before = repo.task_scores([world["task"]])[world["task"].id]

    prune_raw_events(db, 30, now=NOW)
    db.expire_all()
    after = repo.task_scores([world["task"]])[world["task"].id]

    assert after.task_productivity_score == pytest.approx(before.task_productivity_score)
    assert after.productive_seconds == pytest.approx(before.productive_seconds)
    assert after.task_focus_score == pytest.approx(before.task_focus_score)


def test_pruning_is_idempotent_and_a_dry_run_deletes_nothing(db, world) -> None:
    preview = prune_raw_events(db, 30, now=NOW, dry_run=True)
    assert preview.sessions == 1 and preview.activity_events == 2
    assert _rows(db, ActivityEvent, world["old"].id) == 2

    prune_raw_events(db, 30, now=NOW)
    again = prune_raw_events(db, 30, now=NOW)
    assert again.sessions == 0


def test_retention_below_one_day_is_refused(db) -> None:
    with pytest.raises(ValueError):
        prune_raw_events(db, 0)
