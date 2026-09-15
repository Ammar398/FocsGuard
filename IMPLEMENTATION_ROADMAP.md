# FocusGuard — IMPLEMENTATION_ROADMAP.md

**Status:** Proposal — awaiting approval.
**Rule:** the project must be **runnable at the end of every phase**. No phase may leave the tree broken.

Your 23 phases (§54) are preserved. I have grouped them into six milestones, added explicit
**acceptance criteria** to each, and inserted two checkpoints the audit showed are necessary
(a hardware/camera probe, and a performance gate). Phases are ordered so that **nothing depends on
Node.js until Milestone D**, which keeps the frontend decision off the critical path.

---

## Milestone A — Foundations (your Phases 0–1)

### Phase 0 — Audit and plan ✅ COMPLETE
Delivered: `CURRENT_STATE.md`, `ARCHITECTURE_PLAN.md`, `IMPLEMENTATION_ROADMAP.md`.

### Phase 1 — Project skeleton, environment, hardware detection
- `pyproject.toml` workspace; `requirements/{base,agent,backend,dev}.txt` with pinned versions
- **venv created on `E:`** (C: has only 18.1 GB free)
- `focusguard_shared` package: event enums, categories, Pydantic schemas
- Central configuration (§47): Pydantic `Settings` + `.env`, every threshold named and defaulted
- Structured rotating logging (§50)
- **`hardware/detector.py`** — vendor-first GPU detection, true VRAM from registry, DirectML/CUDA/CPU
  provider selection, prints the §46 startup banner
- `scripts/fetch_models.py` — downloads `face_landmarker.task`, verifies checksum, caches **outside the
  repo** (§45)
- `.gitignore`; `git init` on your approval

**Acceptance:** `python -m focusguard_agent.hardware.detector` prints
`GPU: AMD Radeon RX 580 2048SP (8 GB) · CUDA: Not available · DirectML: Available · Provider: DmlExecutionProvider`
— and does **not** report CUDA despite the stale `nvcuda.dll` on this machine.

---

## Milestone B — Local computer vision (your Phases 2–8)

### Phase 2 — Camera capture + performance gate
- Device **enumeration by name** (Camo / DroidCam / Pixel 7), `CAP_DSHOW`, forced 640×480, `BUFFERSIZE=1`
- Latest-frame slot with drop-on-late; rotation/flip config; reconnect loop
- **`CAMERA_UNAVAILABLE` distinct from `FACE_ABSENT`**
- `scripts/benchmark.py` — measures real FPS, per-stage ms, and CPU on this i5-6500

**Acceptance:** live preview from your phone via Camo at a stable ≥ 20 FPS; unplugging mid-run
produces `CAMERA_UNAVAILABLE` and clean auto-recovery, with **zero** false `FACE_ABSENT` events.
Benchmark numbers recorded in `docs/computer_vision.md` and used to set the default rates.

### Phase 3 — Face detection and landmarks
- MediaPipe **Tasks** `FaceLandmarker` wrapper (478 landmarks, blendshapes, transformation matrix)
- Face presence / absence / multiple faces with grace periods (§11)
- Debug overlay drawn with our own OpenCV code (`mp.solutions.drawing_utils` no longer exists)

**Acceptance:** landmarks track your face from the phone camera; two faces in frame raises
`MULTIPLE_FACES`; a 1-second face drop produces **no** event, a 5-second drop produces one.

### Phase 4 — EAR and eye state
EAR per eye, blendshape cross-check, blink counting, `EYES_CLOSED`, plus calibration capture.
**Acceptance:** natural blinking never registers as `EYES_CLOSED`; a deliberate 2-second closure does.
Unit tests run the EAR maths on synthetic landmarks with **no camera**.

### Phase 5 — Drowsiness
Duration-based `DROWSINESS`, distinct from `BLINK` and `EYES_CLOSED`; incorporates repeated slow
closures and downward head angle (§13).
**Acceptance:** the three states are provably separable in the debug view and in unit tests.

### Phase 6 — Head pose and gaze
Yaw/pitch/roll decomposed from the transformation matrix; `CENTER / LEFT / RIGHT / UP / DOWN`;
iris-based gaze with an explicit `gaze_confidence` (§15); grace periods before `GAZE_AWAY`.
**Acceptance:** angles read sensibly and stably; a 1-second glance produces no event, a 5-second one does.

### Phase 7 — Phone detection
`scripts/export_yolo.py` (dev-only, uses Ultralytics once) → `yolov8n.onnx`; runtime inference through
`onnxruntime-directml` with our own letterbox + NMS; temporal persistence so one continuous appearance
yields **one** event with start/end/duration (§16).
**Acceptance:** phone detected at ≥ 0.5 confidence; holding it for 30 s produces exactly **one** event of
~30 s, not hundreds. DirectML confirmed active; CPU fallback verified by forcing it.

### Phase 8 — Focus scoring engine + CV debug screen
Pure-function VAS engine with asymmetric smoothing; configurable weights; the §49 debug screen showing
FPS, EAR, angles, gaze, phone, score, CPU, and active provider.
**Acceptance:** score falls promptly on distraction and recovers gradually, never oscillating
100 → 20 → 100. **Unit tests score a scripted event list deterministically, with no hardware.**

> **End of Milestone B = a fully demonstrable CV system, standalone, no backend, no internet.**
> This alone is a defensible FYP demo, and it is the highest-risk work — so it comes first.

---

## Milestone C — Local monitoring and persistence (your Phases 9–11)

### Phase 9 — Windows foreground application monitoring
1 Hz foreground-window polling, normalisation (`Code.exe` → `Visual Studio Code`), aggregation into
durations. Metadata only.
**Acceptance:** switching apps for known intervals produces durations accurate to ±2 s.

### Phase 10 — Browser extension and domain monitoring
Single MV3 codebase for Chrome + Edge; active tab of the focused window only; **URL minimised in the
extension before sending**; agent `ipc/` loopback endpoint on `127.0.0.1` with a shared token;
classification engine + `TaskContextEngine`.
**Acceptance:** browsing produces domain records with query strings stripped; the extension's on/off
state is plainly visible; **with the extension uninstalled the agent still runs**, tracking Chrome as an
application only.

### Phase 11 — Local database and offline queue
Local SQLite store, event persistence with locally generated UUIDs, `sync_status`, retention job.
**Acceptance:** kill the agent mid-session and restart — no events lost, none duplicated.

---

## Milestone D — Backend, auth, and student portal (your Phases 12–15)

### Phase 12 — Backend API
FastAPI, SQLAlchemy models, Alembic migrations, `/api/v1` routers, ingest endpoints, WebSocket for live
score. **Acceptance:** agent uploads batches; replaying the same batch creates no duplicates.

### Phase 13 — Authentication and RBAC ✅ COMPLETE
Argon2, JWT access + refresh, role dependencies, tenant-isolation guard, rate limiting.
**Acceptance:** a **cross-tenant access test suite** proves a Company A manager receives 403/404 — never
data — on every org-scoped route.

**Met.** `tests/test_backend_isolation.py` covers every organisation-scoped route with a genuine
manager token, and includes a guard test that fails when a new such route appears with no cross-tenant
coverage. Device enrolment (§25) is built server-side here because agent authentication needed a
credential to check; the agent's own enrolment command and consent screen remain Phase 17.

### Phase 14a — Agent synchronisation ✅ COMPLETE
*(Inserted.)* The upload client, the local credential store, the enrolment command with its consent
notice, and the background upload thread.

**Why this was inserted.** Phase 12's acceptance criterion was *"agent uploads batches"*, but the agent
had no upload client — Phase 12 proved the endpoint, not the path to it. The portal has nothing to show
until the agent can upload, so the §53 demo flow could not have run end to end without this. It is pure
Python and needs no Node, so it is cheap to do first.

**Verified live:** the real agent ran for 25 s and uploaded 21 events; the student read them back
through the API; with the backend stopped, 5 events stayed queued and uploaded intact once it returned.
See `docs/synchronization.md`.

### Phase 14b — Student portal ✅ COMPLETE
React 19 + TypeScript + Vite 8 + Tailwind 4 + Recharts 3 (Option A). Login, live focus ring, timers,
current app/domain, focus timeline with event overlays (§41).

**Met**, with one deviation stated plainly: there is **no start/stop button**. A web page cannot start a
process on the user's machine, and a button that quietly does nothing is worse than none. The dashboard
shows the commands instead. That is also the right boundary for a monitoring tool — a dashboard that
could switch someone's webcam on remotely is a different product.

**Verified:** a 52-minute session with 6 attention events, 6 application spans and 1040 score samples
rendered end to end through the dev proxy against a live backend. See `docs/student_portal.md`.

**Node:** the existing install at `E:iles` was incomplete (no `node_modules`, so npm could not run) and
that directory is not writable. A verified Node 22.23.2 now lives in `.tools/node`, gitignored.

### Phase 15 — Student reports and recommendations ✅ COMPLETE
Daily/weekly reports, rule-based recommendation engine with evidence attached, CSV export.
**Acceptance:** every recommendation displays the data that triggered it; an automated test asserts **no
medical or diagnostic vocabulary** can appear in output (§8).

**Met, both halves.** Evidence is enforced by the dataclass — a recommendation cannot be constructed
without it. The language guarantee is proven by running **every rule against 2000 randomised
statistics**, because "cannot appear" is a claim about all inputs, not about the strings someone
happened to check. A companion test asserts every rule fires under fuzzing, so no rule goes
unexercised by the language check.

**One real bug found by running it against realistic data:** the distraction rule attributed by total
time and so named Visual Studio Code — the editor the student was *working in* — as their distraction.
Attribution now follows the classification. See `docs/reports_and_recommendations.md`.

> **End of Milestone D = the §67 MVP is complete.** ✅
>
> Agent → backend → portal runs end to end: monitoring, scoring, upload, auth, dashboard, session
> timeline, reports, recommendations and export.

---

## Milestone E — Business mode (your Phases 16–22)

| Phase | Deliverable | Acceptance |
|---|---|---|
| 16 | Organisations, managers, employees, departments | Full CRUD under strict tenant isolation |
| 17 | Device enrollment | Single-use expiring token; consent screen shown and recorded; token cannot be reused |
| 18 | Manager dashboard | Live device status, aggregate productivity, top apps/domains |
| 19 | Projects, tasks, assignment | Per-task productive/neutral/distracting rules |
| 20 | Task-specific scoring | A `TASK_SESSION` yields a task focus score and productivity score |
| 21 | Employee historical analytics | `employee_metrics` per task category; **"insufficient data" below 3 completed tasks** |
| 22 | Task-assignment recommendation | Ranked candidates, each with its supporting evidence and comparative-only phrasing |

---

## Milestone F — Hardening (your Phase 23)

Unit tests for scoring/classification/recommendations; integration tests for ingest and isolation; the
**privacy dependency-audit test** (asserts no keyboard/clipboard/screen-capture library is importable in
the agent); performance profiling; retention verification; the full `docs/` set (§63); the README (§64);
and the §53 demo script.

---

## Sequencing rationale

1. **Highest technical risk first.** The CV pipeline on an AMD GPU with MediaPipe 1.x is the part most
   likely to surprise us. Milestone B front-loads it, and finishing it yields a working demo very early.
2. **Node.js stays off the critical path** until Phase 14, so the frontend decision can be made calmly.
3. **Student mode before business mode**, exactly as §67 requires — business mode reuses the same
   scoring, activity, and event machinery, so it becomes largely additive.
4. **Tests accompany logic, not the end.** Scoring, classification, and event state machines are all
   pure functions specifically so they can be tested without a camera, a GPU, or a database.

## Suggested working rhythm

One phase per working session. At the end of each I will report: what changed, the files touched, how to
run it, and how to test it (Rule 5).

---

## Immediate next step

Awaiting your approval on three questions (see the summary message), after which **Phase 1** begins.
