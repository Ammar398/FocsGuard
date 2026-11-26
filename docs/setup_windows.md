# Setup on Windows

Tested on Windows 11 Pro, Python 3.11.9, Node 22, Intel i5-6500, AMD RX 580. Everything installs to the
project drive (`E:` here); nothing needs to go on `C:`.

## 1. Prerequisites

| Tool | Check | Notes |
|---|---|---|
| Python 3.11 (64-bit) | `python --version` | 3.12+ is not supported by the pinned MediaPipe |
| Git | `git --version` | |
| Node.js 22 | `node --version` | Optional: a project-local copy lives in `.tools\node` |
| A webcam | — | A phone via Camo / DroidCam / Windows Connected Camera works |

## 2. Python environment

```powershell
cd E:\focusguard
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements/dev.txt -r requirements/backend.txt
pip install -e .
```

`requirements/agent.txt` pins `onnxruntime-directml`. Do **not** also install `onnxruntime` — they
provide the same module and break each other.

## 3. Configuration

```powershell
copy .env.example .env
```

Worth setting:

| Setting | Why |
|---|---|
| `JWT_SECRET_KEY` | A long random value; without it every backend restart signs everyone out |
| `FOCUSGUARD_DATA_DIR=E:\focusguard-data` | Keeps the agent's models, logs and local store off `C:` |
| `FOCUSGUARD_CAMERA__DEVICE_NAME` | Part of your camera's name, e.g. `Camo` |
| `FOCUSGUARD_SCORING__MONITOR_PROFILE` | `DUAL` or `MULTI` softens gaze-away penalties (§60) |

## 4. Check the hardware and fetch models

```powershell
python -m focusguard_agent.hardware.detector
python -m focusguard_agent.hardware.model_store
python -m focusguard_agent.tools.camera_test --list
```

The detector should report your GPU and the selected provider. On an AMD/Intel GPU that is
`DmlExecutionProvider`; with no GPU it is `CPUExecutionProvider` — both work.

The YOLO model (`yolov8n.onnx`) is already exported in the repository root. To re-export it you need
Ultralytics (and PyTorch, ~3 GB) once: `python scripts/export_yolo.py`.

## 5. Calibrate (recommended)

```powershell
python -m focusguard_agent.tools.calibrate --show
```

Look at the screen normally and blink as usual. Your personal eye-closure threshold is saved and used
from then on; skipping it falls back to a sensible default.

## 6. Backend

```powershell
$env:FOCUSGUARD_BACKEND_DB = "E:ocusguardocusguard_backend.db"
cd backend; python -m alembic upgrade head; cd ..
python scripts/seed_demo.py           # optional demo data
python -m focusguard_backend.main     # http://127.0.0.1:8000/docs
```

Pin `FOCUSGUARD_BACKEND_DB` to an absolute path (or put it in your shell profile): a relative default
would put the database wherever each command happens to run. In development the backend also creates
missing tables itself; a database made that way needs `alembic stamp head` before it is migrated.

For PostgreSQL, set `DATABASE_URL=postgresql+psycopg://user:pass@host/focusguard` before running the
migration.

Schedule retention daily (Task Scheduler → *Create Basic Task* → run
`E:\focusguard\.venv\Scripts\python.exe -m focusguard_backend.tasks.retention`).

## 7. Portal

```powershell
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to :8000
```

With the project-local Node: `..\.tools\node\npm.cmd install` and `..\.tools\node\npm.cmd run dev`.

## 8. Browser extension

```powershell
python browser_extension/build.py
python -m focusguard_agent.tools.show_token
```

Load `browser_extension/dist/chrome` (or `dist/edge`) as an unpacked extension in Developer mode and
paste the token into its popup.

## 9. Install the helper, then connect from the portal

```powershell
python -m focusguard_agent.helper --install-startup
```

Then sign in to the portal on that computer and click **Connect this computer**. Windows shows what is
and is not collected; click **Yes**. From then on, start and stop sessions with the portal's buttons —
each start is approved on the computer. Details: [helper.md](helper.md).

Command-line alternative (no helper):

```powershell
python -m focusguard_agent.tools.enroll --token <token>
python -m focusguard_agent.agent --name "Study session" --task "Trees" --task-category STUDY
```

The agent works without enrolment too — it just keeps everything local.
