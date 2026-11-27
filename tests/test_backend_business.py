"""Business mode over HTTP (spec sections 24-31, 51).

Runs the section 53 business demo as a test: a manager signs up, builds a team,
creates and assigns a task, the employee's agent enrols with consent, fetches the
task, uploads a task session, and the manager reads task analytics, strengths
and suggestions. Then every manager route is probed from a second company.
"""

from __future__ import annotations

import re
import uuid as uuid_module
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from focusguard_backend.database.session import create_all, get_engine, reset_engine
from focusguard_backend.services.email import MemoryMailer, set_mailer

NOW = datetime.now(UTC)
PASSWORD = "correct horse battery"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "business.db"))
    reset_engine()

    from focusguard_backend.auth.rate_limit import ip_limiter
    from focusguard_backend.main import app

    ip_limiter.clear("testclient")
    MAILER.messages.clear()
    set_mailer(MAILER)
    create_all(get_engine())
    with TestClient(app) as test_client:
        yield test_client
    set_mailer(None)
    reset_engine()


#: Self-registered accounts must enter an emailed code before signing in; the
#: tests read it from here, as a person would read it from their inbox.
MAILER = MemoryMailer()


def _emailed_code(email: str) -> str:
    message = [m for m in MAILER.messages if m.to == email][-1]
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


def _login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    if response.status_code == 403:
        response = client.post(
            "/api/v1/auth/verify-email", json={"email": email, "code": _emailed_code(email)}
        )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _company(client: TestClient, name: str) -> dict[str, str]:
    email = f"boss@{name.lower()}.example.com"
    response = client.post(
        "/api/v1/auth/register-organization",
        json={
            "organization_name": name,
            "email": email,
            "password": PASSWORD,
            "full_name": f"{name} Boss",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "MANAGER"
    return _login(client, email)


def _employee(client, headers, email, name, password=None, department_id=None) -> int:
    body = {"email": email, "full_name": name}
    if password:
        body["password"] = password
    if department_id:
        body["department_id"] = department_id
    response = client.post("/api/v1/manager/employees", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _enrol(client, manager_headers, user_id: int) -> dict[str, str]:
    token = client.post(
        "/api/v1/devices",
        json={"device_name": "Workstation", "user_id": user_id},
        headers=manager_headers,
    ).json()["enrollment_token"]
    device_uuid = str(uuid_module.uuid4())
    response = client.post(
        "/api/v1/devices/enroll",
        json={
            "enrollment_token": token,
            "device_uuid": device_uuid,
            "platform": "Windows",
            "agent_version": "0.1.0",
            "consent_version": "2026-11",
        },
    )
    assert response.status_code == 200, response.text
    return {"X-Device-Secret": response.json()["device_secret"], "_uuid": device_uuid}


def _task(client, headers, **overrides) -> dict:
    body = {
        "title": "Build REST API",
        "category": "BACKEND",
        "priority": "HIGH",
        "expected_minutes": 60,
        "productive_rules": ["Visual Studio Code", "github.com", " GitHub.com "],
        "distracting_rules": ["netflix.com"],
    }
    body.update(overrides)
    response = client.post("/api/v1/manager/tasks", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client, agent, *, task_id=None, minutes=60, score=80.0, productive=40, distracting=10):
    started = NOW - timedelta(hours=1)
    prefix = uuid_module.uuid4().hex[:8]
    headers = {"X-Device-Secret": agent["X-Device-Secret"]}

    def span(index, app, domain, classification, length):
        start = started + timedelta(minutes=index)
        return {
            "event_uuid": f"{prefix}-a{index}",
            "application_name": app,
            "domain": domain,
            "category": "DEVELOPMENT",
            "classification": classification,
            "rule_source": "TASK_RULE",
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(minutes=length)).isoformat(),
            "duration_seconds": length * 60.0,
        }

    batch = {
        "device_uuid": agent["_uuid"],
        "agent_version": "0.1.0",
        "session": {
            "session_uuid": str(uuid_module.uuid4()),
            "name": "Task session",
            "task_id": task_id,
            "started_at": started.isoformat(),
            "ended_at": (started + timedelta(minutes=minutes)).isoformat(),
            "duration_seconds": minutes * 60.0,
            "focused_seconds": minutes * 60.0 * 0.8,
            "distracted_seconds": minutes * 60.0 * 0.2,
            "average_focus_score": score,
        },
        "vision_events": [
            {
                "event_uuid": f"{prefix}-v0",
                "event_type": "PHONE_VISIBLE",
                "start_time": started.isoformat(),
                "end_time": (started + timedelta(seconds=30)).isoformat(),
                "duration_seconds": 30.0,
                "confidence": 0.87,
            },
            *[
                {
                    "event_uuid": f"{prefix}-v{index}",
                    "event_type": kind,
                    "start_time": (started + timedelta(seconds=60 * index)).isoformat(),
                    "end_time": (started + timedelta(seconds=60 * index + seconds)).isoformat(),
                    "duration_seconds": float(seconds),
                    "confidence": 0.8,
                }
                for index, (kind, seconds) in enumerate(
                    [("EYES_CLOSED", 4), ("HEAD_DOWN", 12), ("MULTIPLE_FACES", 6),
                     ("FACE_ABSENT", 20)],
                    start=1,
                )
            ],
        ],
        "activity_events": [
            span(0, "Visual Studio Code", None, "PRODUCTIVE", productive),
            span(productive, "Google Chrome", "netflix.com", "DISTRACTING", distracting),
        ],
        "focus_scores": [
            {
                "recorded_at": started.isoformat(),
                "visual_attention_score": 85.0,
                "digital_productivity_score": 70.0,
                "combined_focus_score": score,
            }
        ],
    }
    response = client.post("/api/v1/agent/events", json=batch, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- organisation sign-up ---------------------------------------------------


def test_registering_an_organisation_creates_a_manager_and_starter_departments(client):
    headers = _company(client, "Acme")

    org = client.get("/api/v1/manager/organization", headers=headers).json()
    assert org["name"] == "Acme" and org["slug"] == "acme"

    departments = client.get("/api/v1/manager/departments", headers=headers).json()
    assert {d["name"] for d in departments} == {"Development", "Design", "Marketing", "Support"}


def test_organisation_slugs_stay_unique(client):
    _company(client, "Acme")
    response = client.post(
        "/api/v1/auth/register-organization",
        json={
            "organization_name": "ACME",
            "email": "other@acme2.example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201
    other = _login(client, "other@acme2.example.com")
    assert client.get("/api/v1/manager/organization", headers=other).json()["slug"] == "acme-2"


def test_a_taken_email_cannot_register_an_organisation(client):
    _company(client, "Acme")
    response = client.post(
        "/api/v1/auth/register-organization",
        json={"organization_name": "Other", "email": "boss@acme.example.com", "password": PASSWORD},
    )
    assert response.status_code == 409


def test_students_and_employees_cannot_use_manager_routes(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "student@uni.example.com", "password": PASSWORD},
    )
    student = _login(client, "student@uni.example.com")
    assert client.get("/api/v1/manager/dashboard", headers=student).status_code == 403

    boss = _company(client, "Acme")
    _employee(client, boss, "dev@acme.example.com", "Dev One", password=PASSWORD)
    employee = _login(client, "dev@acme.example.com")
    assert client.get("/api/v1/manager/employees", headers=employee).status_code == 403


# --- employees and departments ----------------------------------------------


def test_an_employee_without_a_password_cannot_sign_in(client):
    boss = _company(client, "Acme")
    _employee(client, boss, "agent-only@acme.example.com", "Agent Only")

    response = client.post(
        "/api/v1/auth/login", json={"email": "agent-only@acme.example.com", "password": "!no-login"}
    )
    assert response.status_code == 401

    listed = client.get("/api/v1/manager/employees", headers=boss).json()
    assert listed[0]["can_sign_in"] is False


def test_an_employee_with_a_password_sees_only_their_own_sessions(client):
    boss = _company(client, "Acme")
    dev = _employee(client, boss, "dev@acme.example.com", "Dev One", password=PASSWORD)
    other = _employee(client, boss, "dev2@acme.example.com", "Dev Two")
    _upload(client, _enrol(client, boss, dev))
    _upload(client, _enrol(client, boss, other))

    employee = _login(client, "dev@acme.example.com")
    sessions = client.get("/api/v1/sessions", headers=employee).json()
    assert len(sessions) == 1


def test_departments_can_be_managed_and_deleting_one_keeps_its_people(client):
    boss = _company(client, "Acme")
    created = client.post(
        "/api/v1/manager/departments", json={"name": "QA"}, headers=boss
    ).json()
    duplicate = client.post("/api/v1/manager/departments", json={"name": "qa"}, headers=boss)
    assert duplicate.status_code == 409

    dev = _employee(client, boss, "dev@acme.example.com", "Dev", department_id=created["id"])
    renamed = client.patch(
        f"/api/v1/manager/departments/{created['id']}", json={"name": "Quality"}, headers=boss
    ).json()
    assert renamed == {"id": created["id"], "name": "Quality", "employee_count": 1}

    assert (
        client.delete(f"/api/v1/manager/departments/{created['id']}", headers=boss).status_code
        == 200
    )
    detail = client.get(f"/api/v1/manager/employees/{dev}", headers=boss).json()
    assert detail["department_id"] is None


def test_deactivating_an_employee_ends_their_portal_access(client):
    boss = _company(client, "Acme")
    dev = _employee(client, boss, "dev@acme.example.com", "Dev", password=PASSWORD)
    employee = _login(client, "dev@acme.example.com")

    client.patch(f"/api/v1/manager/employees/{dev}", json={"is_active": False}, headers=boss)
    assert client.get("/api/v1/auth/me", headers=employee).status_code == 401


# --- the full task flow (spec sections 28, 29) ------------------------------


def test_enrolment_records_consent_and_the_agent_sees_only_its_own_tasks(client):
    boss = _company(client, "Acme")
    dev = _employee(client, boss, "dev@acme.example.com", "Dev One")
    other = _employee(client, boss, "dev2@acme.example.com", "Dev Two")
    agent = _enrol(client, boss, dev)

    mine = _task(client, boss, assignee_id=dev)
    _task(client, boss, title="Someone else's", assignee_id=other)
    _task(client, boss, title="Unassigned")

    tasks = client.get(
        "/api/v1/agent/tasks", headers={"X-Device-Secret": agent["X-Device-Secret"]}
    ).json()
    assert [t["id"] for t in tasks] == [mine["id"]]
    # Rules were trimmed, lowercased and de-duplicated on the way in.
    assert tasks[0]["productive_rules"] == ["visual studio code", "github.com"]

    detail = client.get(f"/api/v1/manager/employees/{dev}", headers=boss).json()
    assert detail["consents"][0]["consent_version"] == "2026-11"
    assert detail["devices"][0]["enrolled"] is True


def test_a_task_session_produces_task_scores_and_moves_the_task_in_progress(client):
    boss = _company(client, "Acme")
    dev = _employee(client, boss, "dev@acme.example.com", "Dev One")
    agent = _enrol(client, boss, dev)
    task = _task(client, boss, assignee_id=dev)
    assert task["status"] == "TODO"

    _upload(client, agent, task_id=task["id"], minutes=60, score=80, productive=40, distracting=10)

    detail = client.get(f"/api/v1/manager/tasks/{task['id']}", headers=boss).json()
    assert detail["status"] == "IN_PROGRESS"
    assert detail["started_at"] is not None
    assert len(detail["sessions"]) == 1
    scores = detail["scores"]
    assert scores["task_focus_score"] == pytest.approx(80.0)
    assert scores["task_productivity_score"] == pytest.approx(100 * 40 / 50)
    assert scores["duration_ratio"] == pytest.approx(1.0)
    assert detail["event_counts"] == {
        "PHONE_VISIBLE": 1, "EYES_CLOSED": 1, "HEAD_DOWN": 1, "MULTIPLE_FACES": 1, "FACE_ABSENT": 1,
    }
    assert detail["top_domains"][0] == {
        "label": "netflix.com",
        "seconds": 600.0,
        "classification": "DISTRACTING",
    }
    assert detail["scores"]["explanation"]


def test_a_device_cannot_attach_its_session_to_someone_elses_task(client):
    boss = _company(client, "Acme")
    dev = _employee(client, boss, "dev@acme.example.com", "Dev One")
    other = _employee(client, boss, "dev2@acme.example.com", "Dev Two")
    agent = _enrol(client, boss, dev)
    foreign = _task(client, boss, assignee_id=other)

    _upload(client, agent, task_id=foreign["id"])

    detail = client.get(f"/api/v1/manager/tasks/{foreign['id']}", headers=boss).json()
    assert detail["sessions"] == []
    assert detail["status"] == "TODO"
    # The monitoring data itself was still kept, just without the task link.
    assert client.get(f"/api/v1/manager/employees/{dev}", headers=boss).json()[
        "recent_sessions"
    ][0]["task_id"] is None


def test_a_rating_is_only_accepted_on_a_completed_task(client):
    boss = _company(client, "Acme")
    task = _task(client, boss)

    early = client.patch(
        f"/api/v1/manager/tasks/{task['id']}", json={"quality_rating": 4}, headers=boss
    )
    assert early.status_code == 422

    done = client.patch(
        f"/api/v1/manager/tasks/{task['id']}",
        json={"status": "COMPLETED", "quality_rating": 4.5, "rework_count": 1},
        headers=boss,
    ).json()
    assert done["status"] == "COMPLETED"
    assert done["completed_at"] is not None
    assert done["quality_rating"] == 4.5

    reopened = client.patch(
        f"/api/v1/manager/tasks/{task['id']}", json={"status": "IN_PROGRESS"}, headers=boss
    ).json()
    assert reopened["completed_at"] is None


def test_strengths_need_three_completed_tasks_then_drive_suggestions(client):
    boss = _company(client, "Acme")
    strong = _employee(client, boss, "a@acme.example.com", "Employee A")
    steady = _employee(client, boss, "b@acme.example.com", "Employee B")
    newcomer = _employee(client, boss, "c@acme.example.com", "Employee C")
    agents = {person: _enrol(client, boss, person) for person in (strong, steady)}

    def complete(person, rating, score):
        task = _task(
            client, boss, assignee_id=person, deadline=(NOW + timedelta(days=3)).isoformat()
        )
        if person in agents:
            _upload(client, agents[person], task_id=task["id"], score=score)
        client.patch(
            f"/api/v1/manager/tasks/{task['id']}",
            json={"status": "COMPLETED", "quality_rating": rating},
            headers=boss,
        )

    complete(strong, 5.0, 90)
    early = client.get(f"/api/v1/manager/employees/{strong}/strengths", headers=boss).json()
    assert early["categories"][0]["sufficient_data"] is False
    assert early["categories"][0]["historical_score"] is None
    assert "not yet enough" in early["statements"][0]

    for _ in range(2):
        complete(strong, 5.0, 90)
    for _ in range(3):
        complete(steady, 3.5, 70)
    complete(newcomer, 5.0, 0)

    report = client.get(f"/api/v1/manager/employees/{strong}/strengths", headers=boss).json()
    assert report["categories"][0]["historical_score"] > 80
    assert report["minimum_tasks"] == 3

    suggestions = client.get(
        "/api/v1/manager/tasks/suggestions", params={"category": "BACKEND"}, headers=boss
    ).json()
    assert [c["name"] for c in suggestions["ranked"]] == ["Employee A", "Employee B"]
    assert suggestions["ranked"][0]["rank"] == 1
    assert suggestions["ranked"][0]["reasons"][0].startswith("Backend historical score")
    assert [c["name"] for c in suggestions["insufficient_data"]] == ["Employee C"]


def test_the_dashboard_reports_online_staff_usage_and_departments(client):
    boss = _company(client, "Acme")
    departments = client.get("/api/v1/manager/departments", headers=boss).json()
    development = next(d for d in departments if d["name"] == "Development")
    dev = _employee(
        client, boss, "dev@acme.example.com", "Dev One", department_id=development["id"]
    )
    _employee(client, boss, "idle@acme.example.com", "Idle Person")
    task = _task(client, boss, assignee_id=dev)
    _upload(client, _enrol(client, boss, dev), task_id=task["id"])

    board = client.get("/api/v1/manager/dashboard", params={"days": 1}, headers=boss).json()
    assert board["total_employees"] == 2
    assert board["employees_online"] == 1
    assert board["employees_offline"] == 1
    assert board["active_tasks"] == 1
    assert board["totals"]["productive_seconds"] == 40 * 60
    assert board["totals"]["distracting_seconds"] == 10 * 60
    assert board["totals"]["phone_seconds"] == 30
    # Every camera signal the student sees reaches the manager too.
    assert board["totals"]["eyes_closed_seconds"] == 4
    assert board["totals"]["head_down_seconds"] == 12
    assert board["totals"]["multiple_faces_seconds"] == 6
    assert board["totals"]["face_absent_seconds"] == 20
    assert board["top_applications"][0]["label"] == "Visual Studio Code"
    assert board["top_domains"][0]["label"] == "netflix.com"

    row = next(e for e in board["employees"] if e["id"] == dev)
    assert row["online"] is True
    assert row["current_task"]["title"] == "Build REST API"
    dept = next(d for d in board["departments"] if d["name"] == "Development")
    assert dept["monitored_seconds"] == 3600


def test_manager_csv_exports(client):
    boss = _company(client, "Acme")
    dev = _employee(client, boss, "dev@acme.example.com", "Dev One")
    task = _task(client, boss, assignee_id=dev)
    _upload(client, _enrol(client, boss, dev), task_id=task["id"])

    employees = client.get("/api/v1/manager/reports/employees.csv", headers=boss)
    assert employees.status_code == 200
    text = employees.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("employee_id,name,department")
    assert "Dev One" in text

    tasks = client.get("/api/v1/manager/reports/tasks.csv", headers=boss)
    assert "Build REST API" in tasks.content.decode("utf-8-sig")


# --- cross-tenant (spec section 51) -----------------------------------------


@pytest.fixture
def two_companies(client):
    alpha = _company(client, "Alpha")
    beta = _company(client, "Beta")
    worker = _employee(client, beta, "worker@beta.example.com", "Beta Worker")
    department = client.post(
        "/api/v1/manager/departments", json={"name": "Secret Lab"}, headers=beta
    ).json()["id"]
    project = client.post(
        "/api/v1/manager/projects", json={"name": "Beta Moonshot"}, headers=beta
    ).json()["id"]
    task = _task(client, beta, title="Beta confidential task", assignee_id=worker)
    _upload(client, _enrol(client, beta, worker), task_id=task["id"])
    return {
        "client": client,
        "alpha": alpha,
        "beta": beta,
        "worker": worker,
        "department": department,
        "project": project,
        "task": task["id"],
    }


def test_alpha_lists_contain_nothing_of_beta(two_companies):
    client, alpha = two_companies["client"], two_companies["alpha"]

    for path in (
        "/api/v1/manager/employees",
        "/api/v1/manager/projects",
        "/api/v1/manager/tasks",
        "/api/v1/manager/departments",
        "/api/v1/manager/dashboard",
        "/api/v1/manager/reports/employees.csv",
        "/api/v1/manager/reports/tasks.csv",
    ):
        response = client.get(path, headers=alpha)
        assert response.status_code == 200, path
        body = response.content.decode("utf-8-sig")
        for secret in ("Beta Worker", "Beta Moonshot", "Beta confidential", "Secret Lab"):
            assert secret not in body, f"{secret} leaked through {path}"

    suggestions = client.get(
        "/api/v1/manager/tasks/suggestions", params={"category": "BACKEND"}, headers=alpha
    ).json()
    assert suggestions["ranked"] == [] and suggestions["insufficient_data"] == []


def test_alpha_cannot_read_or_write_betas_records_by_id(two_companies):
    w = two_companies
    client, alpha = w["client"], w["alpha"]

    probes = [
        ("GET", f"/api/v1/manager/employees/{w['worker']}", None),
        ("GET", f"/api/v1/manager/employees/{w['worker']}/strengths", None),
        ("PATCH", f"/api/v1/manager/employees/{w['worker']}", {"full_name": "Hijacked"}),
        ("GET", f"/api/v1/manager/tasks/{w['task']}", None),
        ("PATCH", f"/api/v1/manager/tasks/{w['task']}", {"title": "Hijacked"}),
        ("PATCH", f"/api/v1/manager/projects/{w['project']}", {"name": "Hijacked"}),
        ("PATCH", f"/api/v1/manager/departments/{w['department']}", {"name": "Hijacked"}),
        ("DELETE", f"/api/v1/manager/departments/{w['department']}", None),
    ]
    for method, path, body in probes:
        response = client.request(method, path, json=body, headers=alpha)
        assert response.status_code == 404, f"{method} {path} -> {response.status_code}"
        assert "Beta" not in response.text

    # Nor can Alpha point its own records at Beta's.
    assert client.post(
        "/api/v1/manager/tasks",
        json={"title": "x", "category": "BACKEND", "assignee_id": w["worker"]},
        headers=alpha,
    ).status_code == 404
    assert client.post(
        "/api/v1/manager/tasks",
        json={"title": "x", "category": "BACKEND", "project_id": w["project"]},
        headers=alpha,
    ).status_code == 404
    assert client.post(
        "/api/v1/manager/employees",
        json={
            "email": "spy@alpha.example.com",
            "full_name": "Spy",
            "department_id": w["department"],
        },
        headers=alpha,
    ).status_code == 404

    # And nothing changed on Beta's side.
    beta_task = client.get(f"/api/v1/manager/tasks/{w['task']}", headers=w["beta"]).json()
    assert beta_task["title"] == "Beta confidential task"


# --- weekly work time ------------------------------------------------------------


def test_a_task_work_time_is_saved_shown_and_sent_to_the_agent(client):
    boss = _company(client, "Acme")
    dev = _employee(client, boss, "dev@acme.example.com", "Dev One")
    agent = _enrol(client, boss, dev)
    schedule = {"days": [4, 0, 1, 1], "start": "09:00", "minutes": 240}

    task = _task(client, boss, assignee_id=dev, schedule=schedule)
    assert task["schedule"] == {"days": [0, 1, 4], "start": "09:00", "minutes": 240}

    tasks = client.get(
        "/api/v1/agent/tasks", headers={"X-Device-Secret": agent["X-Device-Secret"]}
    ).json()
    assert tasks[0]["schedule_days"] == [0, 1, 4]
    assert tasks[0]["schedule_time"] == "09:00" and tasks[0]["schedule_minutes"] == 240

    changed = client.patch(f"/api/v1/manager/tasks/{task['id']}", headers=boss,
                           json={"schedule": {"days": [2], "start": "14:30", "minutes": 60}})
    assert changed.json()["schedule"]["start"] == "14:30"
    cleared = client.patch(f"/api/v1/manager/tasks/{task['id']}", headers=boss,
                           json={"clear_schedule": True})
    assert cleared.json()["schedule"] is None


@pytest.mark.parametrize("schedule", [
    {"days": [], "start": "09:00", "minutes": 60},
    {"days": [7], "start": "09:00", "minutes": 60},
    {"days": [0], "start": "9am", "minutes": 60},
    {"days": [0], "start": "23:00", "minutes": 120},  # past midnight
    {"days": [0], "start": "09:00", "minutes": 1},
])
def test_an_impossible_work_time_is_refused(client, schedule):
    boss = _company(client, "Acme")
    response = client.post("/api/v1/manager/tasks", headers=boss, json={
        "title": "x", "category": "BACKEND", "schedule": schedule,
    })
    assert response.status_code == 422


def test_new_columns_are_added_to_an_existing_database(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from focusguard_backend.database.session import add_missing_columns
    from focusguard_backend.models.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE tasks DROP COLUMN schedule_minutes"))
        connection.execute(text("ALTER TABLE tasks DROP COLUMN schedule_days"))

    assert sorted(add_missing_columns(engine)) == ["tasks.schedule_days", "tasks.schedule_minutes"]
    assert "schedule_minutes" in {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert add_missing_columns(engine) == []
    engine.dispose()


# --- editing, removing, office hours and alerts -------------------------------------


def test_a_manager_edits_and_removes_an_employee(client):
    boss = _company(client, "Acme")
    dev = _employee(client, boss, "dev@acme.example.com", "Dev One")
    _employee(client, boss, "taken@acme.example.com", "Taken")
    agent = _enrol(client, boss, dev)
    task = _task(client, boss, assignee_id=dev)

    clash = client.patch(f"/api/v1/manager/employees/{dev}", headers=boss,
                         json={"email": "taken@acme.example.com"})
    assert clash.status_code == 409

    edited = client.patch(f"/api/v1/manager/employees/{dev}", headers=boss, json={
        "full_name": "Dev Renamed", "email": "New@Acme.example.com", "password": PASSWORD,
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["email"] == "new@acme.example.com"
    assert edited.json()["full_name"] == "Dev Renamed" and edited.json()["can_sign_in"]
    _login(client, "new@acme.example.com")

    other = _company(client, "Globex")
    assert client.delete(f"/api/v1/manager/employees/{dev}", headers=other).status_code == 404

    assert client.delete(f"/api/v1/manager/employees/{dev}", headers=boss).status_code == 200
    assert client.get(f"/api/v1/manager/employees/{dev}", headers=boss).status_code == 404
    assert client.get(f"/api/v1/manager/tasks/{task['id']}", headers=boss).json()[
        "assignee_id"] is None
    # Its device is gone too, so it can no longer upload.
    gone = client.get("/api/v1/agent/tasks", headers={"X-Device-Secret": agent["X-Device-Secret"]})
    assert gone.status_code == 401


def test_office_hours_reach_the_computer_and_an_early_close_reaches_the_manager(client):
    boss = _company(client, "Acme")
    dev = _employee(client, boss, "dev@acme.example.com", "Dev One")
    agent = {"X-Device-Secret": _enrol(client, boss, dev)["X-Device-Secret"]}
    hours = {"days": [0, 1, 2, 3, 4], "start": "09:00", "minutes": 480}

    saved = client.patch(f"/api/v1/manager/employees/{dev}", headers=boss,
                         json={"office_hours": hours})
    assert saved.json()["office_hours"] == hours
    schedule = client.get("/api/v1/agent/schedule", headers=agent).json()
    assert schedule == {"office_hours": hours, "restart_alert_id": None}

    raised = client.post("/api/v1/agent/alerts", headers=agent, json={
        "kind": "CLOSED_EARLY", "window_end": "2026-11-30T12:00:00Z",
    })
    assert raised.status_code == 201
    alerts = client.get("/api/v1/manager/alerts", headers=boss).json()
    assert len(alerts) == 1 and alerts[0]["employee_name"] == "Dev One"
    alert_id = alerts[0]["id"]

    other = _company(client, "Globex")
    assert client.get("/api/v1/manager/alerts", headers=other).json() == []
    assert client.post(f"/api/v1/manager/alerts/{alert_id}/restart",
                       headers=other).status_code == 404
    assert client.post(f"/api/v1/manager/alerts/{alert_id}/dismiss",
                       headers=other).status_code == 404

    assert client.post(f"/api/v1/manager/alerts/{alert_id}/restart",
                       headers=boss).status_code == 200
    assert client.get("/api/v1/agent/schedule", headers=agent).json()[
        "restart_alert_id"] == alert_id

    assert client.post(f"/api/v1/agent/alerts/{alert_id}/done",
                       headers=agent).status_code == 204
    assert client.get("/api/v1/manager/alerts", headers=boss).json() == []
    assert client.get("/api/v1/agent/schedule", headers=agent).json()[
        "restart_alert_id"] is None

    cleared = client.patch(f"/api/v1/manager/employees/{dev}", headers=boss,
                           json={"clear_office_hours": True})
    assert cleared.json()["office_hours"] is None
