"""Seed a demonstration database (spec section 53).

    python scripts/seed_demo.py              # writes focusguard_backend.db
    python scripts/seed_demo.py --reset      # deletes it first

WHY A DEMO NEEDS SEEDED HISTORY
-------------------------------
Strength analytics refuses to say anything until an employee has completed at
least three tasks in a category (section 30). A live demo cannot produce weeks
of completed work in ten minutes, so this script writes a plausible month of it:
an organisation, a team, projects, completed and rated tasks with their
monitored sessions, and a student with a week of study sessions.

Everything written is clearly fictional and goes through the same models and
repository the real ingest path uses. The live part of the demo - the webcam,
the phone, the YouTube tab - is then done for real on top of it.

DEMO ACCOUNTS
-------------
Printed at the end. All share the password in `DEMO_PASSWORD` below; they are
for a local demonstration database only.
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("shared", "backend", "desktop_agent"):
    sys.path.insert(0, str(ROOT / package))

from focusguard_backend.auth.passwords import NO_LOGIN_HASH, hash_password  # noqa: E402
from focusguard_backend.auth.tokens import hash_token  # noqa: E402
from focusguard_backend.database.session import (  # noqa: E402
    create_all,
    database_url,
    get_engine,
    get_session_factory,
)
from focusguard_backend.models import (  # noqa: E402
    Department,
    Device,
    Organization,
    PrivacyConsent,
    Project,
    Task,
    User,
)
from focusguard_backend.repositories.telemetry import TelemetryRepository  # noqa: E402

DEMO_PASSWORD = "FocusGuard-demo-2026"
NOW = datetime.now(UTC).replace(second=0, microsecond=0)
rng = random.Random(2026)


@dataclass
class Person:
    name: str
    email: str
    department: str
    #: Per-category (quality, focus) the history is drawn around, so the
    #: strength report has something real to find.
    skill: dict[str, tuple[float, float]]
    can_sign_in: bool = False


TEAM = [
    Person(
        "Ayesha Khan", "ayesha@nimbus.example.com", "Development",
        {"BACKEND": (4.7, 88), "DATABASE": (4.5, 84), "FRONTEND": (3.6, 72)},
        can_sign_in=True,
    ),
    Person(
        "Bilal Ahmed", "bilal@nimbus.example.com", "Design",
        {"DESIGN": (4.8, 90), "FRONTEND": (4.5, 85), "BACKEND": (3.4, 68)},
    ),
    Person(
        "Sara Malik", "sara@nimbus.example.com", "Development",
        {"FRONTEND": (4.2, 80), "BACKEND": (4.1, 79), "TESTING": (4.4, 83)},
    ),
    Person(
        "Usman Tariq", "usman@nimbus.example.com", "Support",
        {"SUPPORT": (4.3, 76), "DOCUMENTATION": (4.0, 74)},
    ),
]

TITLES = {
    "BACKEND": ["Build REST endpoint", "Add JWT refresh", "Optimise report query",
                "Webhook retry queue", "Rate-limit login"],
    "DATABASE": ["Index session tables", "Write retention job", "Migrate to PostgreSQL"],
    "FRONTEND": ["Landing page hero", "Dashboard charts", "Settings form validation",
                 "Responsive navigation"],
    "DESIGN": ["Onboarding illustrations", "Pricing page mockup", "Icon set refresh",
               "Design system tokens"],
    "TESTING": ["Regression suite for auth", "Load test ingest", "E2E checkout tests"],
    "SUPPORT": ["Triage enterprise tickets", "Customer onboarding call notes"],
    "DOCUMENTATION": ["API reference update", "Admin guide chapter"],
}

TOOLS = {
    "BACKEND": [("Visual Studio Code", None, "DEVELOPMENT"), ("Google Chrome", "github.com",
               "DEVELOPMENT"), ("Postman", None, "DEVELOPMENT"),
                ("Google Chrome", "stackoverflow.com", "DEVELOPMENT")],
    "DATABASE": [("DBeaver", None, "DEVELOPMENT"), ("Visual Studio Code", None, "DEVELOPMENT"),
                 ("Google Chrome", "postgresql.org", "DOCUMENTATION")],
    "FRONTEND": [("Visual Studio Code", None, "DEVELOPMENT"),
                 ("Google Chrome", "developer.mozilla.org", "DOCUMENTATION"),
                 ("Figma", None, "DESIGN")],
    "DESIGN": [("Figma", None, "DESIGN"), ("Google Chrome", "dribbble.com", "DESIGN"),
               ("Adobe Photoshop", None, "DESIGN")],
    "TESTING": [("Visual Studio Code", None, "DEVELOPMENT"), ("Windows Terminal", None,
                "DEVELOPMENT"), ("Google Chrome", "github.com", "DEVELOPMENT")],
    "SUPPORT": [("Google Chrome", "zendesk.com", "COMMUNICATION"),
                ("Microsoft Teams", None, "COMMUNICATION")],
    "DOCUMENTATION": [("Microsoft Word", None, "PRODUCTIVITY"),
                      ("Google Chrome", "docs.python.org", "DOCUMENTATION")],
}
NEUTRAL = [("Slack", None, "COMMUNICATION"), ("Microsoft Outlook", None, "COMMUNICATION")]
DISTRACTIONS = [("Google Chrome", "youtube.com", "CONTEXT_DEPENDENT"),
                ("Google Chrome", "instagram.com", "SOCIAL_MEDIA"),
                ("Google Chrome", "netflix.com", "ENTERTAINMENT")]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def write_session(repo, *, user, device, org_id, task, name, category, started, minutes,
                  focus, distracted_share, phone_per_15=None, distraction=None,
                  longest_block_minutes=None):
    """One finished session with activity, attention events and score samples."""
    session = repo.create_session(
        session_uuid=str(uuid.uuid4()), user_id=user.id, name=name, started_at=started,
        device_id=device.id if device else None, organization_id=org_id,
        task_name=task.title if task else name, task_category=category,
        agent_version="0.1.0", task_id=task.id if task else None,
    )
    duration = minutes * 60.0
    tools = TOOLS.get(category, TOOLS["DOCUMENTATION"])

    activity, cursor = [], started
    remaining = duration
    while remaining > 60:
        roll = rng.random()
        if roll < distracted_share:
            app, domain, cat = distraction or rng.choice(DISTRACTIONS)
            verdict = "DISTRACTING"
            # YouTube is context-dependent: productive for a study task.
            if domain == "youtube.com" and category == "STUDY":
                verdict = "PRODUCTIVE"
        elif roll < distracted_share + 0.12:
            app, domain, cat = rng.choice(NEUTRAL)
            verdict = "NEUTRAL"
        else:
            app, domain, cat = rng.choice(tools)
            verdict = "PRODUCTIVE"
        span = min(remaining, rng.uniform(3, 14) * 60)
        activity.append({
            "event_uuid": str(uuid.uuid4()), "application_name": app, "domain": domain,
            "category": cat, "classification": verdict, "rule_source": "TASK_RULE"
            if verdict != "NEUTRAL" else "CATEGORY_DEFAULT",
            "start_time": cursor, "end_time": cursor + timedelta(seconds=span),
            "duration_seconds": span,
        })
        cursor += timedelta(seconds=span)
        remaining -= span

    vision = []
    phone_rate = phone_per_15 if phone_per_15 is not None else 0.5 + distracted_share
    for event_type, chance, length in (("PHONE_VISIBLE", phone_rate, (15, 90)),
                                       ("GAZE_AWAY", 0.8, (5, 40)),
                                       ("FACE_ABSENT", 0.3, (20, 180)),
                                       ("DROWSINESS", 0.12, (3, 8))):
        for _ in range(int(chance * minutes / 15)):
            start = started + timedelta(seconds=rng.uniform(0, duration - 200))
            seconds = rng.uniform(*length)
            vision.append({
                "event_uuid": str(uuid.uuid4()), "event_type": event_type,
                "start_time": start, "end_time": start + timedelta(seconds=seconds),
                "duration_seconds": seconds, "confidence": rng.uniform(0.7, 0.95),
            })

    scores = []
    for minute in range(0, int(minutes), 1):
        drift = -8 * (minute / max(minutes, 1)) if minutes > 40 else 0
        value = _clamp(rng.gauss(focus + drift, 6), 15, 100)
        scores.append({
            "recorded_at": started + timedelta(minutes=minute),
            "visual_attention_score": _clamp(value + rng.uniform(-4, 6), 0, 100),
            "digital_productivity_score": _clamp(100 * (1 - distracted_share) - 5, 0, 100),
            "combined_focus_score": value,
        })

    repo.ingest_activity_events(session, activity)
    repo.ingest_vision_events(session, vision)
    repo.ingest_scores(session, scores)

    average = sum(s["combined_focus_score"] for s in scores) / max(len(scores), 1)
    focused = duration * _clamp(average / 100 + 0.05, 0, 1)
    repo.complete_session(
        session, ended_at=started + timedelta(seconds=duration), duration_seconds=duration,
        focused_seconds=focused, distracted_seconds=duration - focused,
        average_focus_score=average,
        longest_focus_block_seconds=min(
            duration, (longest_block_minutes or rng.uniform(15, 45)) * 60
        ),
    )
    return session


def seed(reset: bool) -> None:
    url = database_url()
    if reset and url.startswith("sqlite:///"):
        path = Path(url.removeprefix("sqlite:///"))
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)

    create_all(get_engine())
    password = hash_password(DEMO_PASSWORD)

    with get_session_factory()() as db:
        if db.query(Organization).filter_by(slug="nimbus-labs").first():
            print("Demo data already present. Use --reset to rebuild it.")
            return

        org = Organization(name="Nimbus Labs", slug="nimbus-labs", is_active=True)
        db.add(org)
        db.flush()
        departments = {}
        for name in ("Development", "Design", "Marketing", "Support"):
            departments[name] = Department(organization_id=org.id, name=name)
            db.add(departments[name])

        manager = User(email="manager@nimbus.example.com", full_name="Hina Qureshi",
                       password_hash=password, role="MANAGER", organization_id=org.id)
        db.add(manager)
        db.flush()

        projects = [Project(organization_id=org.id, name=name, description=text) for name, text in (
            ("Customer Portal", "Self-service portal for enterprise customers"),
            ("Mobile App Redesign", "Refresh of the iOS and Android apps"),
            ("Internal Tools", "Dashboards and automation for the operations team"),
        )]
        db.add_all(projects)
        db.flush()

        repo = TelemetryRepository(db)
        people = {}
        for index, person in enumerate(TEAM):
            user = User(email=person.email, full_name=person.name, role="EMPLOYEE",
                        organization_id=org.id, department_id=departments[person.department].id,
                        password_hash=password if person.can_sign_in else NO_LOGIN_HASH)
            db.add(user)
            db.flush()
            device = Device(
                device_uuid=str(uuid.uuid4()), device_name=f"{person.name.split()[0]}-PC",
                organization_id=org.id, user_id=user.id, platform="Windows 11",
                agent_version="0.1.0", enrolled_at=NOW - timedelta(days=35),
                secret_hash=hash_token(f"demo-secret-{index}-{uuid.uuid4()}"),
                # The first two are "working right now" on the dashboard.
                last_seen_at=NOW - timedelta(seconds=20) if index < 2 else NOW - timedelta(hours=5),
                is_active=True,
            )
            db.add(device)
            db.flush()
            db.add(PrivacyConsent(user_id=user.id, device_id=device.id, consent_version="2026-11",
                                  granted_at=NOW - timedelta(days=35)))
            people[person.name] = (person, user, device)

        # A month of completed, rated tasks.
        for person, user, device in people.values():
            for category, (quality, focus) in person.skill.items():
                for number in range(rng.randint(3, 5)):
                    days_ago = rng.uniform(2, 30)
                    started = (NOW - timedelta(days=days_ago)).replace(
                        hour=rng.choice([9, 10, 11, 14, 15]), minute=0)
                    expected = rng.choice([90, 120, 180, 240])
                    task = Task(
                        organization_id=org.id, project_id=rng.choice(projects).id,
                        title=f"{rng.choice(TITLES[category])} #{number + 1}",
                        category=category, priority=rng.choice(["LOW", "MEDIUM", "HIGH"]),
                        status="COMPLETED", assignee_id=user.id, created_by_id=manager.id,
                        expected_minutes=expected,
                        deadline=started + timedelta(days=rng.choice([1, 2, 3])),
                        productive_rules=[t[1] or t[0].lower() for t in TOOLS[category]],
                        neutral_rules=["slack"], distracting_rules=["instagram.com",
                                                                    "netflix.com"],
                        started_at=started,
                        quality_rating=round(_clamp(rng.gauss(quality, 0.3), 1, 5) * 2) / 2,
                        rework_count=rng.choice([0, 0, 0, 1]) if quality > 4 else rng.choice(
                            [0, 1, 1, 2]),
                    )
                    db.add(task)
                    db.flush()
                    worked = 0.0
                    sessions = rng.randint(1, 3)
                    ratio = rng.uniform(0.8, 1.15) if quality > 4.2 else rng.uniform(1.0, 1.6)
                    for part in range(sessions):
                        minutes = expected * ratio / sessions
                        write_session(
                            repo, user=user, device=device, org_id=org.id, task=task,
                            name=task.title, category=category,
                            started=started + timedelta(hours=part * 3), minutes=minutes,
                            focus=_clamp(rng.gauss(focus, 4), 30, 98),
                            distracted_share=_clamp((100 - focus) / 300, 0.02, 0.25),
                        )
                        worked += minutes
                    late = rng.random() < (0.08 if quality > 4.2 else 0.35)
                    task.completed_at = (task.deadline + timedelta(hours=6) if late
                                         else started + timedelta(minutes=worked + 60))

        # Today: two people mid-task, one open task waiting for an assignee.
        ayesha = people["Ayesha Khan"]
        bilal = people["Bilal Ahmed"]
        for (person, user, device), title, category in (
            (ayesha, "Implement authentication API", "BACKEND"),
            (bilal, "Create Landing Page UI", "FRONTEND"),
        ):
            task = Task(
                organization_id=org.id, project_id=projects[0].id, title=title,
                category=category, priority="HIGH", status="IN_PROGRESS", assignee_id=user.id,
                created_by_id=manager.id, expected_minutes=240,
                deadline=NOW + timedelta(days=2), started_at=NOW - timedelta(hours=3),
                productive_rules=[t[1] or t[0].lower() for t in TOOLS[category]],
                neutral_rules=["slack"], distracting_rules=["instagram.com", "netflix.com"],
            )
            db.add(task)
            db.flush()
            write_session(repo, user=user, device=device, org_id=org.id, task=task, name=title,
                          category=category, started=NOW - timedelta(hours=3), minutes=95,
                          focus=person.skill[category][1],
                          distracted_share=0.08 if person.name.startswith("Ayesha") else 0.16)

        db.add(Task(
            organization_id=org.id, project_id=projects[2].id, title="Build REST API for exports",
            description="CSV and PDF export endpoints for the operations dashboard.",
            category="BACKEND", priority="MEDIUM", status="TODO", created_by_id=manager.id,
            expected_minutes=300, deadline=NOW + timedelta(days=5),
            productive_rules=["visual studio code", "github.com", "postman"],
            neutral_rules=["slack"], distracting_rules=["netflix.com"],
        ))

        # A student with a week of study sessions.
        student = User(email="student@uni.example.com", full_name="Zara Hussain",
                       password_hash=password, role="STUDENT")
        db.add(student)
        db.flush()
        # Evenings are focused; late nights are phone- and Instagram-heavy, so the
        # recommendation engine has real patterns to find (phone habit, a named
        # distraction, time of day, long unbroken sessions).
        plan = [
            (19, "Study Data Structures", 30, 84, 0.06, 0.4, None, None),
            (20, "Watch Python Lecture", 35, 78, 0.10, 0.5, None, None),
            (22, "Work on FYP", 80, 58, 0.30, 1.6,
             ("Google Chrome", "instagram.com", "SOCIAL_MEDIA"), 6),
        ]
        # Hours are the machine's local time, so "evening" means evening in the
        # portal, which groups by the browser's time zone.
        local_now = datetime.now().astimezone()
        for day in range(7):
            for hour, name, minutes, focus, share, phone, distraction, block in plan:
                local = (local_now - timedelta(days=day)).replace(
                    hour=hour, minute=0, second=0, microsecond=0
                )
                started = local.astimezone(UTC)
                if started > NOW:
                    continue
                write_session(repo, user=student, device=None, org_id=None, task=None,
                              name=name, category="STUDY", started=started, minutes=minutes,
                              focus=rng.gauss(focus, 4), distracted_share=share,
                              phone_per_15=phone, distraction=distraction,
                              longest_block_minutes=block)

        db.commit()

    print("Demo data written to", url)
    print("\nDemo accounts (password is DEMO_PASSWORD in scripts/seed_demo.py):")
    print("  Manager  : manager@nimbus.example.com")
    print("  Employee : ayesha@nimbus.example.com   (can sign in; sees only her own data)")
    print("  Student  : student@uni.example.com")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete the database first")
    seed(parser.parse_args().reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
