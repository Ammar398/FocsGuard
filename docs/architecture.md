# Architecture

FocusGuard is four programs and one shared library. Everything that touches a webcam frame runs on the
user's own machine; the server only ever sees structured events.

```
┌──────────────────────── user's Windows PC ─────────────────────────┐      ┌──────── server ────────┐
│                                                                    │      │                        │
│  Chrome / Edge ── extension ──(domain only, loopback IPC)──┐       │      │  FastAPI  /api/v1      │
│                                                            ▼       │      │   auth · ingest ·      │
│  Webcam → capture → landmarks → eye / pose / gaze ─┐   ┌────────┐  │HTTPS │   sessions · reports · │
│                     YOLOv8n (phone, ~3 Hz) ────────┼──►│ event  │  │─────►│   manager              │
│  Foreground window → normalise → classify ─────────┘   │engines │  │events│        │               │
│        (task context: productive/neutral/distracting)  └───┬────┘  │ only │  SQLAlchemy → SQLite / │
│                                                            ▼       │      │               PostgreSQL│
│                                    scoring engine (VAS, DPS, combined)    │        ▲               │
│                                                            ▼       │      │        │ JSON          │
│                                  local SQLite queue (UUID per event)      │  React portal          │
│                                                            └─ sync worker ┘  student · manager     │
└────────────────────────────────────────────────────────────────────┘      └────────────────────────┘
```

## Components

| Component | Path | Responsibility |
|---|---|---|
| Desktop agent | `desktop_agent/focusguard_agent` | Capture, vision, activity, events, scoring, local store, sync |
| Browser extension | `browser_extension/` | Active-tab domain, minimised in the browser, sent to the agent on 127.0.0.1 |
| Backend | `backend/focusguard_backend` | Auth, ingest, tenant-scoped reads, analytics, manager API |
| Portal | `frontend/` | Student dashboard and reports; manager dashboard, employees, tasks |
| Shared | `shared/focusguard_shared` | Enums, Pydantic contracts, recommendation rules, business analytics |

## The separation that makes it testable (§57, §58)

```
Detector            → "phone detected, confidence 0.86"          (no duration, no penalty)
Event engine        → "PHONE_VISIBLE 10:22:15–10:22:47, 32 s"    (no penalty)
Scoring engine      → "visual attention 74 → 61, recovering"     (the only place a score moves)
```

The activity side mirrors it: `WindowsActivityCollector` → `ActivityNormalizer` → `ActivityClassifier`
→ `TaskContextEngine` → `ActivityAggregator`. Each stage is a small class with one job, so every
judgement can be unit-tested with synthetic input and no hardware.

## Threading on a 4-core CPU (§44)

* Camera capture runs on its own thread into a one-frame slot; late frames are dropped, never queued.
* Landmarks run at ~12 Hz, YOLO at ~3 Hz (on the GPU via DirectML), activity polling at 1 Hz, score
  sampling at 1 Hz — each behind a rate limiter, not per frame.
* Upload runs on a daemon thread every 30 s with exponential back-off when offline.

## Business mode on top of the same machinery

A **task** is a `TaskContext` the agent already understood in student mode, now fetched from the
server (`GET /agent/tasks`). A **task session** is an ordinary focus session that carries `task_id`.
Nothing in the CV or scoring path changed for business mode; it is additive. See
[business_mode.md](business_mode.md) and `ARCHITECTURE_PLAN.md` §14 for the as-built schema.

## Tenant isolation (§51)

* Student / employee reads use a `Scope` whose constructor refuses to be empty.
* Manager reads go through `BusinessRepository(db, organization_id)`, bound to one tenant at creation.
* The organisation comes from the signed token, never the request.
* `tests/test_backend_isolation.py` fails if a new user-reachable route is not on its covered list, and
  `tests/test_backend_business.py` probes every manager route from a second company.

## Key decisions

| Decision | Reason |
|---|---|
| ONNX Runtime + DirectML instead of PyTorch | The RX 580 has no CUDA; DirectML uses it, PyTorch would not, and PyTorch costs ~3 GB of disk |
| MediaPipe Tasks API | MediaPipe 1.x removed `mp.solutions`; Tasks also gives iris landmarks and a head-pose matrix |
| FastAPI | Pydantic models are the validation; OpenAPI docs at `/docs` |
| Polling, not WebSockets | The agent uploads every 30 s; a socket could not deliver data sooner |
| Strength analytics computed on read | Dozens of tasks per person; never stale after a rating changes |
| PDF via the browser's print dialog | No layout engine dependency; identical on screen and paper |
