# FocusGuard

A privacy-conscious attention, focus and productivity monitoring platform, built as a Final Year
Project. FocusGuard combines local computer vision with computer-activity monitoring to answer three
questions:

- **Student:** *How focused was I, what distracted me, and how can I improve?*
- **Employee:** *How focused and productive was I on this assigned task?*
- **Manager:** *How was work time used, and who has historically performed strongest on similar work?*

FocusGuard does **not** diagnose ADHD or any medical condition. It reports observed behaviour and the
evidence behind every observation.

> **Status: Phase 1 of 23 complete.** Foundations, configuration and hardware detection work.
> The vision pipeline begins in Phase 2. See [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md).

---

## Features

**Working now (Phase 1)**
- Vendor-first GPU detection with CUDA / DirectML / CPU provider selection
- Central configuration with every threshold named in one place
- Structured rotating logs with automatic secret redaction
- Shared event contracts used by both the agent and the backend
- Verified model caching outside the repository

**Planned** — webcam capture, face and iris landmarks, EAR and eye state, drowsiness, head pose and
gaze, phone detection, focus scoring, foreground-application monitoring, browser-extension domain
tracking, offline event queue, REST API, student portal, reports and recommendations, and the full
business/manager mode.

---

## Architecture

```
desktop_agent/     Windows agent: capture -> vision -> events -> scoring -> local queue -> sync
backend/           FastAPI service: auth, ingest, analytics, reporting        (Phase 12+)
frontend/          React + Vite + TypeScript dashboards                       (Phase 14+)
browser_extension/ One Manifest V3 codebase for Chrome and Edge               (Phase 10+)
shared/            Pydantic contracts and enums imported by agent AND backend
tests/  docs/  scripts/  requirements/
```

Three design rules run through the whole system:

1. **Detection, interpretation and scoring are separate.** A detector reports what it saw with a
   confidence. It never decides someone is distracted and never touches a score.
2. **Attention and productivity are independent.** Someone can stare attentively at Netflix. The two
   are scored separately and only combined at the end.
3. **Raw pixels never leave the machine.** The agent uploads derived events, never frames.

Full detail: [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md).

---

## Technology stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Vision | OpenCV, MediaPipe **Tasks** API, YOLOv8n via ONNX Runtime |
| Acceleration | DirectML (AMD/Intel/NVIDIA) or CUDA (NVIDIA), CPU fallback |
| Backend | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Database | SQLite (development), PostgreSQL (production) |
| Frontend | React, Vite, TypeScript, TailwindCSS, Recharts |
| Auth | JWT access + refresh tokens, Argon2 password hashing |

---

## Hardware requirements

FocusGuard is built to run on modest hardware. The reference development machine is:

| Component | Spec |
|---|---|
| CPU | Intel Core i5-6500 (4C/4T) |
| RAM | 16 GB |
| GPU | AMD Radeon RX 580 2048SP, 8 GB — **no CUDA** |
| OS | Windows 11 Pro |

**A CUDA GPU is not required.** The agent detects the installed adapter and selects DirectML, CUDA or
CPU automatically. Detection is vendor-first: the GPU actually present is identified *before* any GPU
library is trusted, because leftover driver files from a removed card can otherwise cause a false CUDA
report.

Webcam processing targets 640×480 with detection deliberately running slower than capture
(landmarks ~12 Hz, phone detection ~3 Hz) to stay within CPU budget.

---

## Installation

### 1. Prerequisites
- Python 3.11 (`python --version`)
- Git
- A webcam. A phone works well via **Camo**, **DroidCam**, or Windows 11's built-in Connected Camera.
- Node.js 22 LTS — only needed from Phase 14 for the frontend

### 2. Virtual environment

```bash
cd E:\focusguard
python -m venv .venv
.venv\Scripts\activate
```

> Keep the venv on a drive with room to spare. The dependencies total roughly 1.5 GB.

### 3. Dependencies

```bash
pip install -r requirements/dev.txt   # agent + test tooling
pip install -e .                      # makes the three packages importable
```

The `-e .` step matters: it installs `focusguard_shared`, `focusguard_agent` and `focusguard_backend`
as one workspace, so the agent and backend import identical contracts with no path manipulation.

### 4. Configuration

```bash
copy .env.example .env
```

Every setting has a working default, so editing `.env` is optional.

### 5. Models

```bash
python -m focusguard_agent.hardware.model_store
```

Downloads and checksum-verifies `face_landmarker.task` into `%LOCALAPPDATA%\FocusGuard\models`, kept
outside the repository so weights never enter git.

---

## Running

### Hardware detection

```bash
python -m focusguard_agent.hardware.detector
```

Expected output on the reference machine:

```
==============================================================
FocusGuard Hardware Detection
==============================================================
  CPU      : Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz - 4C/4T
  RAM      : 15.94 GB
  GPU      : AMD Radeon RX 580 2048SP (7.95 GB)
           : Microsoft Basic Render Driver (unknown VRAM)  [software adapter]
  CUDA     : Not available
  DirectML : Available
  ONNX RT  : 1.24.4
--------------------------------------------------------------
  Selected inference provider: DmlExecutionProvider
==============================================================
```

Backend, frontend, agent and browser-extension instructions arrive with Phases 12, 14, 2 and 10
respectively.

---

## Testing

```bash
pytest                       # full suite
pytest --cov=focusguard_shared --cov=focusguard_agent
ruff check .                 # lint
```

The suite deliberately runs with **no camera, GPU or database**: scoring, classification and event
logic are pure functions precisely so they can be tested deterministically.

---

## Troubleshooting

**`Not available` for DirectML.** Install the right runtime package — `onnxruntime-directml`, not
`onnxruntime`. The two provide the same module and cannot coexist; uninstall both, then reinstall
`onnxruntime-directml`.

**Camera not found, or the wrong camera opens.** Virtual cameras change index when their host app
starts or stops, so FocusGuard matches by name. Set `FOCUSGUARD_CAMERA__DEVICE_NAME` to a substring of
your device (`Camo`, `DroidCam`, `Pixel`). List devices with:
`ffmpeg -list_devices true -f dshow -i dummy`

**Phone camera image is rotated or mirrored.** Set `FOCUSGUARD_CAMERA__ROTATE_DEGREES` and
`FOCUSGUARD_CAMERA__FLIP_HORIZONTAL`.

**`AttributeError: module 'mediapipe' has no attribute 'solutions'`.** Expected on MediaPipe 1.x, which
removed the legacy Solutions API. FocusGuard uses the Tasks API instead. Most tutorials online are
written against the old API and will not run.

**Import errors for `focusguard_shared`.** Run `pip install -e .` from the repository root.

---

## Privacy

FocusGuard is designed so that invasive monitoring is *impossible*, not merely disabled.

**It does not:** log keystrokes, capture passwords, read the clipboard, take screenshots, record the
screen, record audio, read messages, access unrelated files, or run hidden.

The agent imports no keyboard, clipboard, screen-capture or audio library at all, so there is no code
path any setting could switch on. [`tests/test_privacy_imports.py`](tests/test_privacy_imports.py)
parses every agent source file and **fails the build** if such an import ever appears.

**It does store:** application names, durations, website domains with a coarse category, derived
attention *events* (never frames), and focus scores.

URLs are minimised inside the browser extension before transmission —
`youtube.com/watch?v=ABC&list=PRIVATE` becomes `youtube.com` + `/watch`. Query strings and fragments
are discarded outright, so they never cross a process boundary.

Webcam frames are held in memory and discarded; nothing is written to disk unless a user explicitly
enables the local debug preview. Monitoring is always visibly indicated, students control their own
monitoring entirely, and employees see and record consent at device enrollment.

---

## Documentation

| Document | Contents |
|---|---|
| [CURRENT_STATE.md](CURRENT_STATE.md) | Environment audit and measured findings |
| [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) | Full architecture and design decisions |
| [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) | 23 phases with acceptance criteria |
