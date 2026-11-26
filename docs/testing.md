# Testing

## Automated

```powershell
.venv\Scripts\activate
pytest -q                 # whole suite, ~45 s; no camera, GPU, network or running server
ruff check .
cd frontend
npm run lint
npm run build             # includes a full TypeScript type-check
```

The suite runs without hardware because every judgement in the system is a pure function fed with
synthetic data: EAR maths on synthetic landmarks, event state machines on scripted observation
streams, scoring on scripted event lists, classification on names and domains, analytics on plain
dataclasses. The backend tests use a fresh SQLite file per test through FastAPI's `TestClient`.

## What the suite proves

| Area | Files | Key properties |
|---|---|---|
| Vision maths | `test_eye_analysis`, `test_head_pose`, `test_calibration`, `test_phone_detector` | EAR, pose angles, personal thresholds, YOLO post-processing |
| Event engines | `test_presence`, `test_eye_events`, `test_drowsiness`, `test_attention`, `test_phone_events`, `test_event_state_machine` | Grace periods; one continuous phone appearance = one event; blink ≠ closure ≠ drowsiness; camera loss is never "face absent" |
| Scoring | `test_scoring` | Smooth, bounded, no 100 → 20 → 100 jumps; multi-monitor scaling |
| Activity | `test_activity`, `test_browser_monitoring`, `test_urls`, `test_extension_parity` | Normalisation, task-aware classification, URL minimisation identical in Python and the extension |
| Local store and sync | `test_local_store`, `test_synchronization`, `test_agent_tasks` | Nothing marked synced before the server confirms; retries add nothing; v1 → v2 store upgrade |
| Privacy | `test_privacy_imports`, `test_logging_redaction` | No keyboard / clipboard / screen-capture imports; secrets never logged |
| Backend | `test_backend_api`, `test_backend_auth`, `test_backend_reports` | Ingest idempotency, token rotation and reuse detection, reports |
| Tenant isolation | `test_backend_isolation`, `test_backend_business` | Every user-reachable route listed and probed from another company |
| Business logic | `test_business_logic` | Task scores; no score below 3 tasks; no judgemental language across 1,500 fuzzed histories |
| Business API | `test_backend_business` | The §53 business demo end to end over HTTP |
| Helper | `test_helper` | Foreign origins / rebound hosts refused before any dialog; "No" changes nothing; argument-list launch |
| Retention | `test_backend_retention` | Pruning raw rows changes no task score; idempotent; running sessions untouched |
| Recommendations | `test_recommendations` | Evidence always attached; no clinical wording across 2,000 fuzzed inputs |

## Migrations

```powershell
cd backend
$env:FOCUSGUARD_BACKEND_DB="E:\focusguard\migration_check.db"
python -m alembic upgrade head
python -m alembic check          # "No new upgrade operations detected." = models and migrations agree
python -m alembic downgrade -1
```

## Manual checks with the camera (§49)

| Check | Command | Expect |
|---|---|---|
| Hardware | `python -m focusguard_agent.hardware.detector` | Your GPU, CUDA/DirectML status, chosen provider |
| Camera | `python -m focusguard_agent.tools.camera_test` | Stable ≥ 20 FPS at 640×480 |
| CV debug screen | `python -m focusguard_agent.tools.vision_test` | FPS, face, EAR, eye state, yaw/pitch/roll, gaze, phone, score, CPU, provider |
| Blink vs closure | debug screen | Normal blinks do not raise EYES_CLOSED; a 2 s closure does |
| Look away | debug screen | A 1 s glance: nothing. 5 s: GAZE_AWAY |
| Phone | hold a phone up for 30 s | Exactly one PHONE_VISIBLE event of ~30 s |
| Camera loss | unplug / lock the phone camera | CAMERA_UNAVAILABLE, never FACE_ABSENT; recovers on reconnect |
| Activity | `python -m focusguard_agent.tools.activity_test --seconds 60` | Apps and durations within ±2 s |
| Offline | stop the backend mid-session, restart it | Events queue, then upload with no duplicates |
| Benchmark | `python -m focusguard_agent.tools.benchmark` | Per-stage ms and CPU on this machine |
