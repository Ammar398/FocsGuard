"""Session report tests (spec sections 42, 43).

The report is rebuilt from stored rows, so a session whose agent was killed still
produces its full numbers. These tests write rows directly and check the derived
figures, which is exactly how the report will be used.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from focusguard_agent.local_store.database import LocalStore
from focusguard_agent.reporting.session_report import build_session_report
from focusguard_agent.tools.stop import (
    clear_pid_file,
    clear_stop_flag,
    read_pid_file,
    request_stop,
    stop_requested,
    write_pid_file,
)

START = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    instance = LocalStore(tmp_path / "report.db")
    yield instance
    instance.close()


def _session(store: LocalStore, **kwargs) -> int:
    return store.start_session(
        name=kwargs.pop("name", "Study session"),
        agent_version="0.1.0",
        started_at=START,
        **kwargs,
    )


def _scores(store: LocalStore, session_id: int, seconds: int, active: list[str] | None = None,
            offset: int = 0, score: float = 90.0) -> None:
    """Write one score sample per second, as the agent does."""
    for index in range(seconds):
        store.record_score(
            session_id=session_id,
            recorded_at=START + timedelta(seconds=offset + index),
            visual_attention_score=score,
            combined_focus_score=score,
            active_events=active,
        )


# --- basics -----------------------------------------------------------------


def test_a_missing_session_returns_none(store: LocalStore) -> None:
    assert build_session_report(store, 999) is None


def test_the_report_carries_the_session_identity(store: LocalStore) -> None:
    session_id = store.start_session(
        name="FYP work", agent_version="0.1.0", task_name="Build API",
        task_category="BACKEND", started_at=START,
    )
    report = build_session_report(store, session_id)

    assert report.name == "FYP work"
    assert report.task_name == "Build API"
    assert report.task_category == "BACKEND"


def test_an_unfinished_session_is_flagged(store: LocalStore) -> None:
    """A hard kill leaves no ended_at; the report must say so rather than lie."""
    session_id = _session(store)
    _scores(store, session_id, seconds=30)

    report = build_session_report(store, session_id)
    assert report.completed is False


def test_an_unfinished_session_is_measured_to_its_last_sample(store: LocalStore) -> None:
    """Otherwise a session killed last week would appear to have run for days."""
    session_id = _session(store)
    _scores(store, session_id, seconds=60)

    report = build_session_report(store, session_id)
    assert report.duration_seconds == pytest.approx(59, abs=2)


# --- attention time ---------------------------------------------------------


def test_focused_and_distracted_time_are_derived_from_the_score_timeline(
    store: LocalStore,
) -> None:
    session_id = _session(store)
    _scores(store, session_id, seconds=60, active=None)
    _scores(store, session_id, seconds=30, active=["PHONE_VISIBLE"], offset=60)

    report = build_session_report(store, session_id)

    assert report.focused_seconds == pytest.approx(59, abs=2)
    assert report.distracted_seconds == pytest.approx(29, abs=2)
    assert report.focused_ratio == pytest.approx(0.66, abs=0.05)


def test_a_pause_is_not_counted_as_session_time(store: LocalStore) -> None:
    """Camera down, machine asleep. Time nobody measured is charged to nobody."""
    session_id = _session(store)
    _scores(store, session_id, seconds=30)
    _scores(store, session_id, seconds=30, offset=600)  # ten-minute hole

    report = build_session_report(store, session_id)

    assert report.scored_seconds == pytest.approx(58, abs=3)
    assert report.unscored_seconds > 500


def test_the_longest_focus_block_is_measured(store: LocalStore) -> None:
    session_id = _session(store)
    _scores(store, session_id, seconds=20, active=None)
    _scores(store, session_id, seconds=5, active=["GAZE_AWAY"], offset=20)
    _scores(store, session_id, seconds=45, active=None, offset=25)

    report = build_session_report(store, session_id)
    assert report.longest_focus_block_seconds == pytest.approx(44, abs=3)


def test_a_focus_block_does_not_span_a_pause(store: LocalStore) -> None:
    session_id = _session(store)
    _scores(store, session_id, seconds=20, active=None)
    _scores(store, session_id, seconds=20, active=None, offset=900)

    report = build_session_report(store, session_id)
    assert report.longest_focus_block_seconds < 25


# --- scores -----------------------------------------------------------------


def test_score_statistics_are_computed(store: LocalStore) -> None:
    session_id = _session(store)
    for index, value in enumerate((100.0, 80.0, 60.0, 90.0)):
        store.record_score(
            session_id=session_id,
            recorded_at=START + timedelta(seconds=index),
            visual_attention_score=value,
            combined_focus_score=value,
        )

    report = build_session_report(store, session_id)

    assert report.average_focus_score == pytest.approx(82.5)
    assert report.lowest_focus_score == pytest.approx(60.0)
    assert report.highest_focus_score == pytest.approx(100.0)


def test_absent_productivity_stays_absent(store: LocalStore) -> None:
    """None must not become 0, which would read as terrible productivity."""
    session_id = _session(store)
    _scores(store, session_id, seconds=10)

    assert build_session_report(store, session_id).average_productivity is None


# --- events -----------------------------------------------------------------


def _event(store: LocalStore, session_id: int, event_type: str, seconds: float,
           offset: float = 0, confidence: float = 0.9) -> None:
    store.record_vision_event(
        session_id=session_id,
        event_type=event_type,
        start_time=START + timedelta(seconds=offset),
        end_time=START + timedelta(seconds=offset + seconds),
        duration_seconds=seconds,
        confidence=confidence,
    )


def test_events_are_totalled_and_counted(store: LocalStore) -> None:
    session_id = _session(store)
    _event(store, session_id, "PHONE_VISIBLE", 30, offset=0)
    _event(store, session_id, "PHONE_VISIBLE", 20, offset=60)
    _event(store, session_id, "GAZE_AWAY", 10, offset=100)

    report = build_session_report(store, session_id)
    phone = report.event("PHONE_VISIBLE")

    assert phone.seconds == pytest.approx(50)
    assert phone.count == 2
    assert phone.longest_seconds == pytest.approx(30)
    assert report.phone_appearances == 2


def test_event_confidence_is_averaged(store: LocalStore) -> None:
    """A single confident detection must not make the whole total look certain."""
    session_id = _session(store)
    _event(store, session_id, "GAZE_AWAY", 10, offset=0, confidence=1.0)
    _event(store, session_id, "GAZE_AWAY", 10, offset=20, confidence=0.6)

    assert build_session_report(store, session_id).event("GAZE_AWAY").mean_confidence == (
        pytest.approx(0.8)
    )


def test_events_are_ordered_by_time_spent(store: LocalStore) -> None:
    session_id = _session(store)
    _event(store, session_id, "GAZE_AWAY", 5)
    _event(store, session_id, "FACE_ABSENT", 60, offset=10)

    assert build_session_report(store, session_id).events[0].event_type == "FACE_ABSENT"


# --- activity ---------------------------------------------------------------


def _activity(store: LocalStore, session_id: int, name: str, seconds: float,
              classification: str = "PRODUCTIVE", domain: str | None = None,
              offset: float = 0) -> None:
    store.record_activity_event(
        session_id=session_id,
        application_name=name,
        category="DEVELOPMENT",
        classification=classification,
        rule_source="CATEGORY_DEFAULT",
        start_time=START + timedelta(seconds=offset),
        end_time=START + timedelta(seconds=offset + seconds),
        duration_seconds=seconds,
        domain=domain,
    )


def test_a_domain_is_reported_instead_of_the_browser(store: LocalStore) -> None:
    """"github.com" says far more than "Google Chrome"."""
    session_id = _session(store)
    _activity(store, session_id, "Google Chrome", 120, domain="github.com")

    report = build_session_report(store, session_id)

    assert report.applications[0].name == "github.com"
    assert report.applications[0].is_domain is True


def test_time_in_one_application_accumulates_across_spans(store: LocalStore) -> None:
    session_id = _session(store)
    _activity(store, session_id, "Visual Studio Code", 100, offset=0)
    _activity(store, session_id, "Visual Studio Code", 200, offset=300)

    assert build_session_report(store, session_id).applications[0].seconds == (
        pytest.approx(300)
    )


def test_the_biggest_distraction_is_identified(store: LocalStore) -> None:
    session_id = _session(store)
    _activity(store, session_id, "Visual Studio Code", 600)
    _activity(store, session_id, "Chrome", 300, classification="DISTRACTING",
              domain="instagram.com", offset=700)

    worst = build_session_report(store, session_id).most_distracting()

    assert worst is not None
    assert worst.name == "instagram.com"


def test_time_is_split_by_classification(store: LocalStore) -> None:
    session_id = _session(store)
    _activity(store, session_id, "Code", 300, classification="PRODUCTIVE")
    _activity(store, session_id, "Netflix", 100, classification="DISTRACTING", offset=400)

    split = build_session_report(store, session_id).classification_seconds

    assert split["PRODUCTIVE"] == pytest.approx(300)
    assert split["DISTRACTING"] == pytest.approx(100)


def test_an_empty_session_reports_zeros_rather_than_failing(store: LocalStore) -> None:
    """A session that recorded nothing must still produce a report."""
    report = build_session_report(store, _session(store))

    assert report.focused_seconds == 0.0
    assert report.events == []
    assert report.applications == []
    assert report.focused_ratio == 0.0


# --- graceful stop ----------------------------------------------------------


def test_the_stop_flag_round_trips(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOCUSGUARD_DATA_DIR", str(tmp_path))

    clear_stop_flag()
    assert stop_requested() is False

    request_stop()
    assert stop_requested() is True

    clear_stop_flag()
    assert stop_requested() is False


def test_the_pid_file_records_the_running_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOCUSGUARD_DATA_DIR", str(tmp_path))

    write_pid_file("Study session")
    running = read_pid_file()

    assert running is not None
    assert running[1] == "Study session"

    clear_pid_file()
    assert read_pid_file() is None


def test_a_missing_pid_file_is_not_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOCUSGUARD_DATA_DIR", str(tmp_path))
    assert read_pid_file() is None
