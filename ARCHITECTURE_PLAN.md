# FocusGuard — ARCHITECTURE_PLAN.md

**Status:** Proposal — awaiting approval before any implementation begins.
**Companion documents:** `CURRENT_STATE.md` (audit), `IMPLEMENTATION_ROADMAP.md` (phasing).

---

## 1. Guiding principles

1. **Detection, interpretation, and scoring are three separate layers.** A detector reports what it
   saw with a confidence. It never decides that the user is distracted, and it never touches a score
   (§57).
2. **Attention and productivity are independent axes** and are never collapsed into a single number
   until the final combination step (§59).
3. **Evidence over instinct.** Every negative judgement requires *duration* plus *confidence*
   (§61). One bad frame is never an event.
4. **Raw pixels never leave the machine.** The agent uploads structured events, not frames (§33, §34).
5. **Thresholds live in configuration, not in source code** (§47).
6. **Everything must degrade cleanly** — no camera, no GPU, no network, no browser extension.

---

## 2. Proposed directory structure

Your proposed structure is sound and I am keeping its shape. I propose **five changes**, each
justified below as §55 Rule 3 requires.

```
focusguard/
├── pyproject.toml                 # [CHANGE 1] single installable workspace
├── .env.example
├── README.md
├── docker-compose.yml
│
├── shared/focusguard_shared/      # [CHANGE 1] importable package, not a loose folder
│   ├── schemas/                   # Pydantic contracts shared by agent + backend
│   ├── constants/                 # event types, categories, enums
│   └── utilities/
│
├── backend/focusguard_backend/
│   ├── api/v1/                    # routers
│   ├── auth/                      # JWT, hashing, RBAC, tenant guard
│   ├── database/                  # engine, session, Alembic migrations
│   ├── models/                    # SQLAlchemy ORM
│   ├── repositories/              # [CHANGE 2] data access, isolated from services
│   ├── services/                  # business logic
│   ├── analytics/                 # aggregation, reports, recommendations
│   ├── tasks/                     # task/project domain + background jobs
│   └── main.py
│
├── desktop_agent/focusguard_agent/
│   ├── hardware/                  # GPU/CPU detection, provider selection
│   ├── capture/                   # [CHANGE 3] camera source, enumeration, reconnect
│   ├── vision/
│   │   ├── landmarks.py           # MediaPipe Tasks FaceLandmarker wrapper
│   │   ├── eye_analysis.py        # EAR + blendshape cross-check
│   │   ├── head_pose.py           # yaw/pitch/roll from transformation matrix
│   │   ├── gaze.py                # iris-based gaze + confidence
│   │   └── phone_detector.py      # YOLO via ONNX Runtime
│   ├── events/                    # [CHANGE 3] state machines -> discrete events
│   ├── scoring/                   # focus scoring engine (pure functions)
│   ├── activity_monitor/          # Windows foreground window
│   ├── browser_monitor/           # receives extension messages
│   ├── ipc/                       # [CHANGE 4] localhost server for the extension
│   ├── local_store/               # [CHANGE 4] offline SQLite queue
│   ├── synchronization/           # batched upload, retry, dedupe
│   ├── privacy/                   # consent, pause, visible status
│   ├── calibration/
│   ├── debug_ui/                  # the §49 CV test screen
│   └── agent.py
│
├── browser_extension/             # [CHANGE 5] ONE MV3 codebase, Chrome + Edge
│   ├── src/
│   └── build.py
│
├── frontend/                      # React + Vite + TS (pending Node decision)
├── tests/
├── docs/
├── scripts/
└── requirements/
```

### Change 1 — make `shared/` a real installed package

**Why:** `backend` and `desktop_agent` are two separate processes that must agree exactly on event
schemas. A plain `shared/` folder forces `sys.path` manipulation to import across siblings, which is
fragile on Windows and breaks as soon as the agent is packaged as an executable. Declaring one
workspace with `pyproject.toml` and installing `-e .` makes `from focusguard_shared.schemas import
VisionEvent` work identically in both processes, in tests, and in a frozen build. This costs one file
and removes a whole category of import bugs.

### Change 2 — add a `repositories/` layer

**Why:** §51 requires that a manager from Company A can *never* read Company B data. If organisation
filtering is scattered across service functions, a single forgotten `.filter(org_id == ...)` becomes a
data breach. Funnelling all queries through repository methods that *take `org_id` as a mandatory
argument* makes tenant isolation structurally enforced and unit-testable, rather than a convention
people remember. You also asked for repositories in §56.

### Change 3 — split `capture/` and `events/` out of `vision/`

**Why:** Frame acquisition is not computer vision; on this machine it is virtual-camera plumbing with
reconnect logic (`CURRENT_STATE.md` §2.4) and deserves isolated tests. Likewise, converting a stream of
per-frame observations into *discrete, de-duplicated, duration-bearing events* is temporal state-machine
logic, not detection. Keeping it separate is what makes §16 ("one continuous phone appearance produces
ONE event") and §11/§14 grace periods testable **without a camera**, which matters enormously for
Rule 13.

### Change 4 — add `ipc/` and `local_store/`

**Why:** `ipc/` is missing from the original tree but is structurally required: a Chrome extension
cannot call a Python function. It can only reach a **localhost HTTP endpoint**, so the agent must run a
small loopback server bound to `127.0.0.1` with a shared token. Without this module, §20 cannot exist.
`local_store/` is the §37 offline queue; separating it from `synchronization/` keeps "durably record an
event" independent from "upload it", which is what lets the agent keep working with no network.

### Change 5 — one browser extension, not `chrome/` and `edge/`

**Why:** Microsoft Edge is Chromium-based and consumes the **same** Manifest V3 extension with the same
APIs. Maintaining `chrome/` and `edge/` as separate folders means every fix must be applied twice and
will eventually drift — a bug source and a §56 "copy-pasted logic" violation. One source tree with a
small build script that emits per-browser packages gives identical functionality with half the
maintenance. If a genuine Edge-specific need appears later, it becomes a documented override file
rather than a forked codebase.

---

## 3. Technology decisions

| Layer | Choice | Justification |
|---|---|---|
| Backend | **FastAPI** | Async (needed for WebSockets, §38), Pydantic-native so §36 schemas and validation are the same objects, automatic OpenAPI docs — a genuinely strong artefact to show at defence. **No reason to fall back to Flask.** |
| ORM | SQLAlchemy 2.0 (typed) | As specified; typed API fits §56. |
| Migrations | Alembic | As specified (§32). |
| DB (dev) | SQLite (WAL mode) | Zero setup; WAL matters because the agent writes while the dashboard reads. |
| DB (prod) | PostgreSQL | As specified. All models stay dialect-neutral so the switch is a URL change. |
| Validation | Pydantic v2 | Shared between agent and backend via `focusguard_shared`. |
| Auth | JWT access + refresh, `argon2` hashing | Argon2 over bcrypt: no 72-byte truncation, better defaults. |
| CV landmarks | **MediaPipe Tasks `FaceLandmarker`** | Forced by MediaPipe 1.x; also better (see `CURRENT_STATE.md` §2.6). |
| Object detection | **YOLOv8n exported to ONNX, run on ONNX Runtime** | See §3.1 below. |
| Inference provider | **DirectML → CPU** | No CUDA on this AMD GPU. |
| Frontend | React + Vite + TS + Tailwind + Recharts | Pending your Node decision. |

### 3.1 Why ONNX Runtime instead of the Ultralytics runtime — an important decision

The obvious route is `pip install ultralytics` and `YOLO("yolov8n.pt")`. **I recommend against it as a
runtime dependency on this machine**, for three measured reasons:

1. **Disk.** Ultralytics pulls in PyTorch — roughly **2.5–3 GB**. C: has **18.1 GB free**. Even installed
   to the E: venv, `torch` is by far the largest thing in the project, for one 6 MB model.
2. **It buys no acceleration here.** PyTorch on Windows offers CUDA (NVIDIA-only) or CPU. With an
   **AMD RX 580**, the PyTorch path is **CPU-only** — so the heaviest dependency in the stack would run
   YOLO on the already-scarce 4 threads, competing with MediaPipe.
3. **ONNX Runtime + DirectML actually uses your 8 GB AMD GPU**, moving phone detection off the CPU
   entirely. That is the difference between comfortably hitting the §44 CPU budget and not.

**Plan:** use `ultralytics` **once**, as a one-off developer tool in `scripts/export_yolo.py`, to export
`yolov8n.onnx`. It is listed in `requirements/dev.txt`, never in runtime requirements. Runtime loads the
`.onnx` through `onnxruntime-directml`. We write our own letterbox pre-processing and NMS
post-processing — about 100 lines, and a genuine asset at the viva, because you can explain exactly what
happens to the image instead of pointing at a library call.

**Model note:** COCO class 67 is `cell phone` — exactly what §16 needs, so no training is required.

---

## 4. Computer-vision architecture

### 4.1 Pipeline and threading

Three threads, because the machine has 4 cores and oversubscription would cost more than it buys.

```
[Thread A: Capture]        [Thread B: Vision]                [Thread C: Agent core]
VideoCapture(DSHOW)        FaceLandmarker  ~12 Hz            Event state machines
 640x480, buffer=1   -->   EAR / blendshapes                 Focus scoring
 latest-frame slot         Head pose / gaze                  Activity + browser monitors
 (drop, never queue)       PhoneDetector   ~3 Hz (DirectML)  Local store + sync
```

**Latest-frame slot, not a queue.** If vision falls behind, old frames are *dropped*, not buffered.
A queue would grow, add latency, and make the score describe the past. This matters more than usual here
because a Wi-Fi phone camera already adds latency.

### 4.2 Frame budget (the §44 constraint made concrete)

| Stage | Rate | Est. cost/frame | Est. CPU |
|---|---|---|---|
| Capture + resize | 25 Hz | ~2 ms | ~5% |
| FaceLandmarker (CPU/XNNPACK) | 12 Hz | ~12–20 ms | ~25–35% |
| EAR / pose / gaze (NumPy) | 12 Hz | <1 ms | ~2% |
| YOLOv8n 320×320 (DirectML) | 3 Hz | ~25 ms GPU | ~5% CPU |
| Events + scoring + IO | 5 Hz | <1 ms | ~2% |
| **Target total** | | | **≈ 40–50% of 4 threads** |

These are *estimates to be validated*, not claims. **Phase 2 ends with a benchmark script that measures
the real numbers on this exact machine**, and the rates above become configuration defaults tuned to
that measurement. If we miss budget, the documented levers in order are: drop landmark rate to 8 Hz,
drop YOLO to 2 Hz, drop YOLO input to 256×256.

### 4.3 Signal design — why each detector cross-checks another

| Signal | Primary method | Cross-check | Why |
|---|---|---|---|
| Eye closure | **EAR** from 6 landmarks/eye | MediaPipe `eyeBlinkLeft/Right` blendshape | EAR is sensitive to head yaw and to camera angle — a phone propped at a slant is exactly the failure case. Blendshapes are pose-normalised. Agreement raises confidence; disagreement lowers it. |
| Head pose | **4×4 facial transformation matrix** decomposed to yaw/pitch/roll | Landmark-geometry sanity check | Avoids a hand-tuned generic 3D model in `solvePnP`. Far easier to defend. |
| Gaze | Iris centre offset within eye opening | `eyeLookIn/Out/Up/Down` blendshapes + head pose | Webcam gaze is genuinely imprecise. §15 demands we expose `gaze_confidence` rather than pretend. |
| Phone | YOLOv8n `cell phone`, conf ≥ 0.5 | Temporal persistence over N frames | Single-frame detections are noisy; §16 requires one event per continuous appearance. |
| Face presence | FaceLandmarker face count | **Camera health flag** | A dead camera must yield `CAMERA_UNAVAILABLE`, never `FACE_ABSENT`. |

### 4.4 EAR and calibration (§12, §48)

EAR = (‖p2−p6‖ + ‖p3−p5‖) / (2·‖p1−p4‖), computed per eye on normalised landmarks.

A fixed 0.21 threshold is wrong for many people — eye shape, glasses, and camera angle all shift it.
Calibration records ~5 s of normal open-eye EAR and derives a **personal** threshold:

```
threshold = mean_open_EAR - k * std_open_EAR      (k configurable, default 2.5)
clamped to [0.15, 0.30]
```

The clamp is deliberate: it prevents a bad calibration (user blinked throughout) from producing a
nonsense threshold. If calibration is skipped, documented defaults apply. This is stored per user in
`calibrations` and is re-runnable.

### 4.5 Event state machines (§17)

Every behavioural event is produced by the same generic machine, which is why it is testable without
hardware:

```
IDLE --(condition true)--> CANDIDATE --(held > grace_seconds)--> ACTIVE --> emits event on exit
                                \--(condition false before grace)--> IDLE  (nothing emitted)
```

`grace_seconds` per type (all configurable): `FACE_ABSENT` 2 s, `GAZE_AWAY` 3–5 s, `EYES_CLOSED` 1 s,
`DROWSINESS` 2.5 s, `PHONE_VISIBLE` 1.5 s. A short hysteresis on exit prevents one recovered frame from
splitting a single event into several. Confidence recorded on the event is the mean over its ACTIVE
window, not the peak — peaks overstate certainty.

---

## 5. Focus-scoring architecture (§18, §59)

**Two independent axes, combined only at the end.**

### 5.1 Visual Attention Score (VAS)

```
penalty(t)  = Σ over currently-ACTIVE vision events of weight[type] × confidence
target(t)   = clamp(100 − penalty(t), 0, 100)
VAS(t)      = α · target(t) + (1 − α) · VAS(t−1)
```

with **asymmetric smoothing**: `α_down = 0.15` when the score is falling, `α_up = 0.04` when recovering.
Distraction registers promptly; recovery is earned gradually. This is what prevents the
100 → 20 → 100 oscillation §18 forbids, and it is a one-line, explainable rule.

Default weights (all configurable, all to be tuned after real testing):
`DROWSINESS 40 · PHONE_VISIBLE 35 · FACE_ABSENT 25 · GAZE_AWAY 15 · EYES_CLOSED 10 · MULTIPLE_FACES 10`

### 5.2 Digital Productivity Score (DPS)

Computed over a rolling window from time-in-classification:

```
DPS = 100 × (productive_sec + 0.5 × neutral_sec) / total_active_sec
```

Neutral counts as half rather than zero, because Slack and email are real work, and scoring them as
distraction would make the system unfair and encourage bad behaviour.

### 5.3 Combined score

```
Combined = w_v · VAS + w_d · DPS          (defaults w_v = w_d = 0.5, configurable)
```

**Multi-monitor adjustment (§60):** on a `dual`/`multi` monitor profile, the `GAZE_AWAY` weight is
scaled down (default ×0.4) **and** gaze-away is suppressed entirely while the foreground application is
classified PRODUCTIVE. Looking at your second screen while VS Code is focused is work, not distraction.
This is a configured profile, not a guess.

We persist all six values §18 asks for: `instant_focus_score`, `rolling_1_min_focus`,
`session_average_focus`, `visual_attention_score`, `digital_productivity_score`, `combined_focus_score`.

**The scoring engine is written as pure functions over an event list** — no camera, no database, no
clock. That is what makes Rule 13 unit tests trivial and gives you deterministic numbers to put in the
report.

---

## 6. Screen- and website-monitoring architecture (§58)

```
WindowsActivityCollector ─┐
                          ├─> ActivityNormalizer ─> ActivityClassifier ─> TaskContextEngine ─> ActivityAggregator
BrowserActivityCollector ─┘        (Code.exe ->        (github.com ->      (DEVELOPMENT +        (durations,
   (via ipc/ localhost)          Visual Studio Code)    DEVELOPMENT)      backend task ->        sessions)
                                                                            PRODUCTIVE)
```

- **WindowsActivityCollector** polls the foreground window at 1 Hz via `GetForegroundWindow` /
  `GetWindowThreadProcessId` plus `psutil` for the process name. **Metadata only** — no screen contents,
  no input capture (§34).
- **BrowserActivityCollector** consumes messages the MV3 extension POSTs to the agent's loopback
  endpoint. Only the **active tab of the focused window** while monitoring is on.
- **URL minimisation happens in the extension, before transmission** — not on the server. The extension
  sends `domain` + optionally a coarse path segment, with query strings and fragments stripped
  (`youtube.com/watch?v=ABC` → `youtube.com/watch`). Minimising at the source means the sensitive data
  never crosses a process boundary at all (§20, §35).
- **TaskContextEngine** is what makes §22 work: YouTube is not intrinsically distracting. Classification
  is `(category, task_context) -> PRODUCTIVE | NEUTRAL | DISTRACTING`, resolved by precedence:
  **task-specific rule → user/org override → global default**, and the winning rule is stored on the
  record so every judgement can be explained.

**Graceful degradation:** with no extension installed, the browser is still tracked as an application
(`Google Chrome`) with no domain detail. The system stays useful; it just loses resolution.

---

## 7. Database architecture (§32)

Grouped by concern. All tables carry `id`, `created_at`, `updated_at`.

**Identity & tenancy**
`users` (id, email UQ, password_hash, role, is_active) · `organizations` · `students` ·
`managers` (org_id FK) · `employees` (org_id FK, department_id FK) · `departments` (org_id FK) ·
`devices` (org_id, employee_id, enrollment_token_hash, device_name, agent_version, last_seen_at)

**Work domain**
`projects` (org_id) · `tasks` (org_id, project_id, category, priority, deadline, expected_minutes) ·
`task_assignments` (task_id, employee_id, status, quality_rating, rework_count) ·
`task_app_rules` (task_id, matcher, classification)

**Session & telemetry**
`focus_sessions` (user_id, device_id, task_session_id?, started_at, ended_at, session_name) ·
`task_sessions` (task_assignment_id, focus_session_id) ·
`vision_events` (**uuid UQ**, session_id, event_type, start/end, duration_s, confidence, metadata JSON) ·
`activity_events` (uuid UQ, session_id, app_name, window_title?, start/end, duration_s, classification, rule_source) ·
`website_usage` (uuid UQ, session_id, domain, path_prefix?, category, duration_s, classification) ·
`focus_scores` (session_id, ts, vas, dps, combined) — written at a coarse cadence, not per frame

**Derived & config**
`daily_summaries` · `employee_metrics` (employee_id, task_category, avg_focus, on_time_rate, avg_quality) ·
`student_recommendations` (rule_id, message, **evidence JSON**) · `settings` · `calibrations` ·
`privacy_consents` (user_id, device_id, consent_version, granted_at, ip) · `device_heartbeats`

### Key design points

- **`uuid` unique constraints on every event table.** This is the §37 idempotency mechanism: the agent
  generates the UUID locally, retries are safe, and duplicate uploads are rejected by the database
  rather than by hopeful application logic.
- **`org_id` is denormalised onto tenant-scoped tables** even where it is reachable through a join.
  It makes the §51 isolation filter a single indexed predicate on every query, and lets a DB constraint
  back up the application guard.
- **`rule_source` on classification rows** stores *why* something was called distracting, satisfying
  §9's "every recommendation explains why" and §41's "understand why the score changed".
- **Retention (§45):** raw `vision_events` / `activity_events` default to **30 days**, then roll up into
  `daily_summaries` and are deleted. Summaries are kept indefinitely — they are tiny. This is a
  scheduled job with a configurable window, which is what keeps a 128 GB SSD viable.
- **Indexes:** `(session_id, start_time)` on all event tables; `(org_id, employee_id, date)` on
  summaries; `(employee_id, task_category)` on `employee_metrics`.

---

## 8. Student workflow (§7)

```
Register/login -> Consent + first-run calibration (5 steps, §48) -> Camera check (pick device by name)
   -> Create session: name, task type, expected duration, allowed/blocked apps + sites
   -> Monitoring ON (visible indicator) -> live dashboard: focus ring, timers, current app/domain
   -> Events accumulate locally, stream to backend -> End session
   -> Session report -> rule-based recommendations with evidence -> daily/weekly trends
```

The recommendation engine is **rule-based and explainable first** (§9). Each rule is a small,
independently testable unit that emits a message **plus the evidence that fired it**:

> "Phone appeared 6 times for 11 minutes total during a 90-minute session. Sessions where phone time
> stayed under 2 minutes averaged 14 points higher focus."

That is a behavioural observation with its data attached. **No medical language, ever** (§8). A hard-coded
vocabulary blocklist in the recommendation module makes that a code-level guarantee, not a style guideline.

---

## 9. Business workflow (§24–§31)

```
Manager registers org -> creates departments, employees, projects
   -> generates a device enrollment token (single-use, expiring)
   -> employee installs agent, sees consent screen, enrolls -> device bound to org+employee
   -> manager creates task with productive/neutral/distracting rules -> assigns it
   -> employee starts the task; a TASK_SESSION opens
   -> agent records vision events + activity, uploads batched
   -> manager sees task analytics, team analytics, employee detail
   -> after several completed tasks, employee_metrics per task_category becomes meaningful
   -> on new task creation, the system suggests candidates and shows the evidence
```

**Employee strength analytics (§30) rests on outcomes, not surveillance.** The inputs are completion
rate, duration vs. estimate, task focus score, productive-app ratio, manager quality rating, rework
count, and deadline performance. Two safeguards I consider mandatory:

- **A minimum sample size** (default 3 completed tasks in a category) before any comparative claim is
  shown. Below it, the UI says "not enough data" rather than ranking someone on one task.
- **Fixed comparative phrasing.** "Historical task data suggests Employee A has performed more strongly
  on frontend assignments" — never "Employee A is bad at backend" (§30). Enforced in the template layer.

---

## 10. Security and privacy architecture (§34, §51, §62)

**Security**

| Control | Implementation |
|---|---|
| Password storage | Argon2id |
| Sessions | Short-lived JWT access (15 min) + rotating refresh token |
| Authorisation | Role-based dependency on every router: `student` / `manager` / `device` |
| **Tenant isolation** | `org_id` a mandatory parameter on every repository method; a shared FastAPI dependency resolves the caller's org and no service may bypass it. **Cross-tenant access is an explicit test suite**, not an assumption. |
| Device auth | Enrollment token is single-use and expiring; it is exchanged for a long-lived device credential stored hashed. Devices get a device-scoped role that can write events and read nothing about other employees. |
| Transport | HTTPS in production; agent↔extension IPC bound to `127.0.0.1` only, with a per-session shared token so other local processes cannot post fake activity. |
| Input | Pydantic validation on every endpoint; rate limiting on auth and ingest routes. |
| Logging (§50) | Structured, rotating. Passwords, tokens, and full URLs are never logged. |

**Privacy — what the system does NOT do, enforced structurally**

No keylogging, no clipboard access, no screenshots, no screen recording, no message reading, no
password capture, no file access beyond its own data, no hidden operation. These are absent by design:
the agent imports no keyboard-, clipboard-, or screen-capture library at all, so the capability does not
exist in the process. A dependency-audit test in `tests/` asserts this and fails the build if such an
import ever appears. That is a much stronger promise than a paragraph in the README, and it is a strong
point at defence.

**What it does store:** application names, window titles (optional and disableable), domains plus
coarse category, durations, derived vision *events* (never frames), and scores.

**Transparency:** a persistent visible monitoring indicator; students control their own monitoring
completely; employees see a consent screen at enrollment and a permanent status indicator; a
`privacy_consents` record retains what was agreed and when; camera frames are never written to disk
unless the user explicitly enables the local debug preview.

---

## 11. Offline and real-time behaviour

**Offline (§37).** Every event is written to the local SQLite queue with `sync_status = pending`
**first**, then uploaded. The uploader batches (default 50 events / 30 s), retries with exponential
backoff, and marks `synced`. Because the UUID is generated locally and the server has a unique
constraint, a retry after an ambiguous timeout is always safe. The agent is fully functional with the
backend switched off — which is also the cleanest way to demo the CV pipeline in isolation.

**Real-time (§38).** WebSockets are used in exactly two places: the student's live session view and the
manager's device-status board. Everything historical is plain REST. We push a score update at ~1 Hz,
not per frame.

---

## 12. What I explicitly recommend deferring

To protect the §67 MVP, these are consciously **not** in the first working build: PostgreSQL and Docker
(SQLite is sufficient until multi-machine), PDF export (CSV first — a PDF layout engine is a
disproportionate time sink), any LLM-based recommendations (§9 mandates rules first), and packaging the
agent into a signed installer. Each has a roadmap phase; none blocks the MVP.

---

## 13. Approval requested

The five structural changes in §2, the ONNX-over-PyTorch decision in §3.1, and the Node.js decision in
`CURRENT_STATE.md` §4 are the items I would like confirmed before Phase 1 begins. Everything else
follows your specification as written.
