# FocusGuard

A privacy-conscious attention, focus and productivity analytics platform, built as a Final Year
Project. FocusGuard combines **local** computer vision with metadata-only computer-activity monitoring
to answer three questions:

- **Student:** *How focused was I, what distracted me, and how can I improve?*
- **Employee:** *How focused and productive was I on this assigned task?*
- **Manager:** *How was work time used, and who has historically performed strongest on similar work?*

FocusGuard does **not** diagnose ADHD or any medical condition. It reports observed behaviour, with the
evidence behind every observation, and the wording is enforced by tests.

> **Status: all 23 phases implemented.** Student mode (Phases 0–15) and business mode (Phases 16–22)
> are complete; Phase 23 hardening is done. 686 automated tests. See
> [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) and [CURRENT_STATE.md](CURRENT_STATE.md).

---

## Features

**Computer vision (runs on the local machine only)**
- Webcam capture by device name (works with Camo / DroidCam / phone cameras), 640×480, auto-recovery
- MediaPipe face landmarks: face present / absent / multiple faces, with grace periods
- Eye Aspect Ratio with personal calibration: blinks, eye closure, drowsiness (duration + PERCLOS)
- Head pose (yaw / pitch / roll) and iris gaze, each with a confidence
- Phone detection with YOLOv8n through ONNX Runtime (DirectML on AMD, CUDA on NVIDIA, CPU fallback)
- One event per continuous behaviour, with start, end, duration and confidence
- Focus score 0–100: visual attention, digital productivity and combined, temporally smoothed

**Computer activity (metadata only)**
- Foreground application and duration, normalised (`Code.exe` → Visual Studio Code)
- Chrome / Edge extension: active-tab **domain** only, minimised inside the browser
- Task-context-aware classification: productive / neutral / distracting depends on the task

**Student mode**
- Live focus ring, timeline with event overlays explaining every dip
- Daily / weekly / monthly reports, rule-based recommendations with evidence, CSV and PDF export

**Business mode**
- Organisation sign-up, departments, employees, projects
- Device enrolment with single-use tokens and recorded consent
- Tasks with per-task productive / neutral / distracting rules; the agent works under those rules
- Task focus and task productivity scores, explained
- Manager dashboard: who is working, team time split, top apps and domains, team comparison
- Employee detail and task-category history; assignee suggestions with evidence
- CSV and print-to-PDF reports

---

## Architecture

```
desktop_agent/     Windows agent: camera -> vision -> events -> scoring -> local SQLite queue -> sync
browser_extension/ One Manifest V3 codebase, built for Chrome and Edge
backend/           FastAPI: auth, ingest, sessions, reports, manager API (SQLAlchemy + Alembic)
frontend/          React + Vite + TypeScript portal (student and manager)
shared/            Pydantic contracts, enums, recommendation rules, business analytics
tests/  docs/  scripts/  requirements/
```

Three design rules run through the whole system:

1. **Detection, interpretation and scoring are separate.** A detector reports what it saw with a
   confidence. An event engine decides duration. Only the scoring engine applies penalties.
2. **Attention and productivity are independent.** Someone can stare attentively at Netflix.
3. **Raw pixels never leave the machine.** The agent uploads derived events, never frames.

Full detail: [docs/architecture.md](docs/architecture.md) and [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md).

---

## Technology stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Vision | OpenCV, MediaPipe **Tasks** API, YOLOv8n via ONNX Runtime |
| Acceleration | DirectML (AMD/Intel/NVIDIA) or CUDA (NVIDIA), CPU fallback |
| Backend | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Database | SQLite (development), PostgreSQL (production, via `DATABASE_URL`) |
| Frontend | React 19, Vite 8, TypeScript, Tailwind 4, Recharts 3 |
| Auth | JWT access + rotating refresh tokens, Argon2id, RBAC, tenant isolation |

---

## Hardware requirements

| Component | Reference machine | Notes |
|---|---|---|
| CPU | Intel Core i5-6500 (4C/4T) | Capture + face landmarks measured at ~38% of one core (9% of all 4); YOLO runs on the GPU |
| RAM | 16 GB | Agent uses a few hundred MB |
| GPU | AMD RX 580, 8 GB — **no CUDA** | Optional. DirectML is used if present; CPU works |
| Disk | ~2 GB for the venv + Node | No video or screenshots are ever stored |
| OS | Windows 11 | The agent's activity monitor is Windows-only |

The agent detects the GPU vendor *before* trusting any GPU library, because leftover NVIDIA driver files
from a removed card would otherwise cause a false CUDA report. Detection runs slower than capture
(landmarks ~12 Hz, phone detection ~3 Hz) to stay inside the CPU budget.

---

## Installation (Windows)

Step-by-step with screenshots of expected output: [docs/setup_windows.md](docs/setup_windows.md).

```powershell
cd E:\focusguard
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements/dev.txt -r requirements/backend.txt
pip install -e .
copy .env.example .env
python -m focusguard_agent.hardware.model_store     # downloads the face model
```

> Keep the venv, Node and data on a drive with room (this project uses `E:`). To keep the agent's
> local data off `C:` as well, set `FOCUSGUARD_DATA_DIR=E:\focusguard-data` in `.env`.

### Database

```powershell
$env:FOCUSGUARD_BACKEND_DB = "E:ocusguardocusguard_backend.db"   # one path for every command
cd backend; python -m alembic upgrade head; cd ..
python scripts/seed_demo.py          # optional: a demo organisation, team, history and student
```

In development you can skip Alembic: the backend creates missing tables on start. Use one approach
per database — a database created by the backend must be stamped (`alembic stamp head`) before it is
migrated later.

---

## Running

| What | Command |
|---|---|
| Hardware check | `python -m focusguard_agent.hardware.detector` |
| Backend API | `python -m focusguard_backend.main` → http://127.0.0.1:8000/docs |
| Portal (dev) | `cd frontend` then `npm install` and `npm run dev` → http://localhost:5173 |
| Webcam test | `python -m focusguard_agent.tools.camera_test` |
| CV debug screen | `python -m focusguard_agent.tools.vision_test` |
| Calibration | `python -m focusguard_agent.tools.calibrate` |
| **Helper (once per PC)** | `python -m focusguard_agent.helper --install-startup` |
| Student session (CLI) | `python -m focusguard_agent.agent --name "Study Data Structures" --task "Trees" --task-category STUDY` |
| Enrol a machine (CLI) | `python -m focusguard_agent.tools.enroll --token <token>` |
| Employee tasks (CLI) | `python -m focusguard_agent.tools.tasks` |
| Work on a task (CLI) | `python -m focusguard_agent.agent --assigned-task <id>` |
| Stop the agent (CLI) | `python -m focusguard_agent.tools.stop` (or Ctrl+C) |
| Session report | `python -m focusguard_agent.tools.report --session <id>` |

**No terminal needed day to day.** Once the helper is installed on a computer, people sign in to the
portal and click **Connect this computer**, then **Start session** (students) or **Start** next to an
assigned task (employees). Each of those asks for permission in a Windows dialog on that computer.
See [docs/helper.md](docs/helper.md). The CLI rows above remain for scripting and troubleshooting.

If Node is not on `PATH`, the project-local copy is at `.tools\node` (`.tools\node\npm.cmd run dev`).

### Browser extension

```powershell
python browser_extension/build.py
python -m focusguard_agent.tools.show_token
```

Chrome: `chrome://extensions` → Developer mode → *Load unpacked* → `browser_extension/dist/chrome`.
Edge: `edge://extensions` → the same, with `dist/edge`. Paste the token from `show_token` into the
extension popup. Details: [docs/browser_extension.md](docs/browser_extension.md).

---

## Testing

```powershell
pytest                      # 686 tests, ~60 s, no camera / GPU / network needed
ruff check .
cd frontend; npm run lint; npm run build
```

What the suite proves, and how to run the manual camera checks:
[docs/testing.md](docs/testing.md).

---

## FYP demonstration

A scripted student demo and business demo, with timings and what to say:
[docs/fyp_demo.md](docs/fyp_demo.md).

---

## Troubleshooting

**`Not available` for DirectML.** Install `onnxruntime-directml`, not `onnxruntime`. They provide the
same module and cannot coexist; uninstall both, then reinstall `onnxruntime-directml`.

**Camera not found, or the wrong camera opens.** Virtual cameras change index when their host app
starts or stops, so FocusGuard matches by name. Set `FOCUSGUARD_CAMERA__DEVICE_NAME` to a substring of
your device (`Camo`, `DroidCam`, `Pixel`). List devices with
`ffmpeg -list_devices true -f dshow -i dummy`.

**Phone camera image rotated or mirrored.** Set `FOCUSGUARD_CAMERA__ROTATE_DEGREES` and
`FOCUSGUARD_CAMERA__FLIP_HORIZONTAL`.

**`module 'mediapipe' has no attribute 'solutions'`.** Expected on MediaPipe 1.x. FocusGuard uses the
Tasks API; most online tutorials use the removed one.

**Signed out every time the backend restarts.** Set `JWT_SECRET_KEY` in `.env`. Without it a random key
is generated per start, deliberately — there is no default secret in source.

**Manager board shows everyone offline.** "Working" means a heartbeat within two minutes. The agent
heartbeats after each upload pass (every 30 s) only while it is running and enrolled.

**`--assigned-task` says the task is not yours.** Only open (to-do / in-progress) tasks assigned to
the person the machine is enrolled for are visible. List them with `tools.tasks`.

**"The FocusGuard Helper is not running on this computer."** Run
`python -m focusguard_agent.helper --install-startup` once. If the portal is served from somewhere other
than `localhost:5173`, add its origin to `FOCUSGUARD_HELPER__ALLOWED_ORIGINS`.

**Import errors for `focusguard_shared`.** Run `pip install -e .` from the repository root.

---

## Privacy

FocusGuard is designed so that invasive monitoring is *impossible*, not merely disabled.

**It does not:** log keystrokes, capture passwords, read the clipboard, take screenshots, record the
screen, record audio, read messages, access unrelated files, or run hidden.

The agent imports no keyboard, clipboard, screen-capture or audio library, and
[`tests/test_privacy_imports.py`](tests/test_privacy_imports.py) fails the build if one ever appears.
The upload schema has no field for a frame, a screenshot or a URL, so even a modified agent could not
send one.

**It does store:** application names, durations, website domains with a category, derived attention
*events* (never frames), focus scores, and — in business mode — task outcomes.

Students control their own monitoring entirely. Employees see exactly what is and is not collected and
must agree before their machine is enrolled; the consent is recorded and visible to them and their
manager. Full statement: [docs/privacy.md](docs/privacy.md).

---

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components, data flow, threading, key decisions |
| [docs/database.md](docs/database.md) | Agent local store and backend schema |
| [docs/computer_vision.md](docs/computer_vision.md) | Camera, landmarks, EAR, drowsiness, pose, gaze, YOLO |
| [docs/focus_scoring.md](docs/focus_scoring.md) | The scoring engine and its weights |
| [docs/screen_monitoring.md](docs/screen_monitoring.md) | Foreground application tracking |
| [docs/browser_extension.md](docs/browser_extension.md) | Domain monitoring and URL minimisation |
| [docs/synchronization.md](docs/synchronization.md) | Offline queue and upload |
| [docs/helper.md](docs/helper.md) | One-click connect / start from the portal, and why it is safe |
| [docs/student_mode.md](docs/student_mode.md) | The student workflow end to end |
| [docs/student_portal.md](docs/student_portal.md) | Portal screens and design choices |
| [docs/reports_and_recommendations.md](docs/reports_and_recommendations.md) | Reports and the rule engine |
| [docs/business_mode.md](docs/business_mode.md) | Organisations, tasks, task scores, strength analytics |
| [docs/privacy.md](docs/privacy.md) | What is and is not collected; retention; consent |
| [docs/api.md](docs/api.md) | Every endpoint, authentication, isolation |
| [docs/setup_windows.md](docs/setup_windows.md) | Installation on Windows |
| [docs/testing.md](docs/testing.md) | Automated and manual testing |
| [docs/fyp_demo.md](docs/fyp_demo.md) | The demonstration script |
| [CURRENT_STATE.md](CURRENT_STATE.md) | Latest audit |
| [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) | Design decisions, including as-built business schema |
| [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) | 23 phases with acceptance criteria |
