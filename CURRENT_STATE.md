# FocusGuard — CURRENT_STATE.md

**Audit date:** 2026-08-31
**Scope:** Phase 0 — repository and environment audit
**Verdict:** Greenfield project. There is no existing code to preserve, migrate, or break.

---

## 1. Repository state

| Item | Finding |
|---|---|
| Location | `E:\focusguard` |
| Total files | **1** |
| `main.py` | **0 bytes — completely empty** |
| Git repository | **No** (`.git` absent) |
| Virtual environment | **None** |
| `requirements.txt` / `pyproject.toml` | **None** |
| Tests / docs / CI | **None** |
| Frontend project | **None** |
| Browser extension | **None** |
| Database / migrations | **None** |

### Implication

Nothing is implemented yet, therefore:

- Nothing is "broken" — there is simply no code.
- **Rule 2 and Rule 3 (do not delete / do not rewrite working functionality) are not triggered.**
  There is no working functionality to protect.
- The architecture can be chosen cleanly, with no legacy constraints.

---

## 2. Verified development environment

Every value below was **measured on this machine**, not assumed.

### 2.1 Python toolchain

| Item | Value | Status |
|---|---|---|
| Python | **3.11.9** (`C:\Users\IC\AppData\Local\Programs\Python\Python311`) | Matches the required 3.11 |
| Architecture | AMD64, 64-bit | OK |
| pip | 26.2.1 | OK |
| `venv` module | Available | OK |
| Other Python installs | None (only 3.11) | Clean — no version ambiguity |

### 2.2 Hardware (measured)

| Component | Detected value |
|---|---|
| CPU | **Intel Core i5-6500 @ 3.20 GHz — 4 cores / 4 threads** |
| RAM | **15.94 GB** |
| GPU | **AMD Radeon RX 580 2048SP** |
| GPU VRAM | **8 GB** (confirmed via registry `qwMemorySize`) |
| Driver | 31.0.21910.5 |
| OS | Windows 11 Pro (build 26100); Python reports it as `Windows 10` |
| Disk C: | 118.3 GB total — **only 18.1 GB free** |
| Disk E: | 465.7 GB total — **100.1 GB free** (project lives here) |

**Note on VRAM.** `Win32_VideoController.AdapterRAM` reported only 4 GB. That field is a 32-bit value
and saturates at 4 GB, so it is unreliable for cards above 4 GB. The registry value
`HardwareInformation.qwMemorySize` confirms the true size is **8 GB**, matching your stated
"8 GB GDDR5, 256-bit". Our hardware detector must read the registry value, not `AdapterRAM`, or it
will misreport your GPU during the FYP demo.

### 2.3 GPU acceleration — the single most important finding

| Path | Available? | Evidence |
|---|---|---|
| **CUDA** | **NO** | GPU is AMD, not NVIDIA. `nvidia-smi` fails. |
| **ROCm** | **NO** | AMD ROCm has no Windows support for Polaris (RX 580). |
| **DirectML** | **YES** | `DirectML.dll` and `d3d12.dll` present in `System32`; `onnxruntime-directml` 1.24.4 available on PyPI. |
| CPU (XNNPACK) | YES | Always-available fallback. |

**There is a decoy on this machine.** `C:\Windows\System32\nvcuda.dll` and `nvml.dll` **are present**,
and the registry still holds driver entries for a **GeForce GTX 750 Ti** and a **Radeon RX 570** —
leftovers from previously installed cards.

**Consequence:** a naive detector that checks "does `nvcuda.dll` exist?" will **wrongly report CUDA
support on this machine**. The hardware detector must identify the *active adapter vendor* first, and
only then probe CUDA. This is exactly the kind of silent misdetection that breaks a live demo.

**Decision:** the acceleration ladder for this project is **DirectML (primary) → CPU (fallback)**.
A CUDA branch stays in the code for portability to other machines, but it will never activate here.

### 2.4 Camera hardware — this changes the capture design

`ffmpeg -list_devices` reports **no physical webcam**. Three *virtual* cameras exist:

| Device | Type |
|---|---|
| **Camo** | Phone-as-webcam (Reincubate Camo Studio) |
| **DroidCam Video** | Phone-as-webcam (DroidCam) |
| **Pixel 7 (Windows Virtual Camera)** | Windows 11 native "Connected Camera" |

You confirmed you will use **Camo Studio with your phone as the camera**. That is fully workable, but
it is not equivalent to a built-in laptop webcam, and the code must account for five things:

1. **Device index is not stable.** Virtual cameras register and de-register as their host app starts and
   stops, so `cv2.VideoCapture(0)` may point at Camo today and DroidCam tomorrow.
   → The capture layer selects a camera **by name**, with index as a fallback.
2. **Backend matters.** Virtual DirectShow cameras are markedly more reliable under `cv2.CAP_DSHOW`
   than under the default MSMF backend on Windows.
3. **Resolution must be forced down.** Phone cameras default to 1080p or higher. We explicitly request
   640×480 and set `CAP_PROP_BUFFERSIZE = 1`, or latency accumulates and the i5-6500 burns cycles
   downscaling frames it never needed (§3, §44).
4. **Orientation varies.** Phone cams may deliver portrait or mirrored frames, so rotation and
   horizontal flip are **configuration options**, never hardcoded.
5. **The camera can vanish mid-session** — phone locks, Wi-Fi drops, USB unplugged. This is a
   *correctness* issue for scoring: a dropped camera must raise `CAMERA_UNAVAILABLE` and must **never**
   be recorded as `FACE_ABSENT`. Penalising a student because their phone locked would be a genuine
   bug, and an examiner is likely to probe exactly this.

### 2.5 Already-installed Python packages (global interpreter)

```
mediapipe 1.0.1        opencv-python 5.0.0.93      opencv-contrib-python 5.0.0.93
numpy 2.4.6            matplotlib 3.11.1           pillow 12.3.0
sounddevice 0.5.6      absl-py 2.5.0               flatbuffers 25.12.19
```

Two problems:

- **Both `opencv-python` and `opencv-contrib-python` are installed.** They overwrite the same `cv2`
  module and are a classic source of "it worked yesterday" breakage. Our venv will install
  **`opencv-contrib-python` only** (it is a strict superset).
- These live in the **global** interpreter. We will not use them. A dedicated venv gives reproducible,
  pinned versions (Rule 8, Rule 9).

### 2.6 MediaPipe 1.0.1 — the second architecture-changing finding

I probed the installed MediaPipe API directly rather than trusting tutorials:

```
mediapipe 1.0.1
has solutions: False          <-- mp.solutions DOES NOT EXIST
tasks.vision OK: FaceDetector, FaceLandmarker, FaceLandmarkerOptions,
                 FaceLandmarkerResult, HandLandmarker, HolisticLandmarker, ...
```

**The legacy `mp.solutions.face_mesh` API was removed in MediaPipe 1.x.**

Effectively every FocusGuard-style tutorial, blog post, and prior FYP report online uses
`mp.solutions.face_mesh.FaceMesh(refine_landmarks=True)`. **That code raises `AttributeError` on this
machine.** We must use the **MediaPipe Tasks API** (`FaceLandmarker`).

Inconvenient for copy-paste — but **technically better for us**. The Tasks API exposes three things the
old Solutions API did not:

| Capability | Benefit to FocusGuard |
|---|---|
| 478 landmarks including **iris** | Gaze estimation (§15) |
| **`output_face_blendshapes`** (`eyeBlinkLeft/Right`, `eyeLookDown/In/Out/Up`) | An independent second signal to cross-check EAR and gaze — directly supports §61 "no false claims" |
| **`output_facial_transformation_matrixes`** (4×4 pose matrix) | Head yaw/pitch/roll **without** hand-rolled `solvePnP` and a guessed generic 3D face model — more accurate and much easier to defend in the viva |

**Costs:** the Tasks API needs a downloadable model bundle (`face_landmarker.task`, ~3.8 MB), so a
first-run model-fetch step is mandatory. Also `mp.solutions.drawing_utils` is gone, so the debug
overlay (§49) needs roughly 40 lines of our own OpenCV drawing code.

### 2.7 Other tooling

| Tool | Status | Impact |
|---|---|---|
| Git | 2.55.0 installed | Available, but repo not initialised |
| **Node.js / npm** | **NOT INSTALLED** | **Blocks the React + Vite frontend.** Decision needed — see §4. |
| Docker | Not installed | Affects only `docker-compose.yml` / PostgreSQL. The SQLite dev path is unaffected. |
| FFmpeg | 9.0 installed | Useful for camera enumeration and debugging |
| Internet / PyPI | Reachable | Package and model downloads will work |

---

## 3. Blockers and risks, ranked

| # | Issue | Severity | Resolution |
|---|---|---|---|
| 1 | **No CUDA — AMD GPU.** Stale NVIDIA DLLs can cause false CUDA detection. | **High** | Vendor-first hardware detector; DirectML EP via ONNX Runtime; CPU fallback. |
| 2 | **MediaPipe 1.x removed `mp.solutions`.** All tutorial code is invalid. | **High** | Build on the Tasks API from day one; ship a model-download script. |
| 3 | **C: has only 18.1 GB free.** A PyTorch install alone is ~2.5–3 GB. | **High** | venv on `E:`. **Do not make PyTorch/Ultralytics a runtime dependency** — run YOLO through ONNX Runtime. |
| 4 | **Node.js absent** — React/Vite cannot build. | **Medium** | Decision required (§4). |
| 5 | Camera is a **virtual phone camera**: index-unstable, droppable. | **Medium** | Name-based selection, DSHOW backend, forced 640×480, distinct `CAMERA_UNAVAILABLE` state. |
| 6 | Duplicate `opencv-python` + `opencv-contrib-python`. | **Low** | Clean venv, contrib only. |
| 7 | Not a git repository. | **Low** | `git init` — awaiting your approval. |
| 8 | 4 cores / 4 threads, **no hyperthreading**. | **Medium** | Strict frame-budget design, capped thread counts, benchmark gate in Phase 2. |

---

## 4. Decision required from you

**Node.js is not installed, so the React + Vite + TypeScript frontend cannot be built yet.**
Two viable routes:

**Option A — Install Node.js 22 LTS (recommended).**
Delivers the stack you asked for: React + Vite + TypeScript + Tailwind + Recharts. Best-looking
dashboard, strongest for the demo and the report. Costs roughly one evening of setup and ~400 MB
(installable to `E:` to protect the C: drive).

**Option B — No Node at all: FastAPI + Jinja2 + HTMX + Chart.js via CDN.**
Zero extra toolchain, one language, one process, faster to build. But it is a weaker answer when the
examiner asks about your frontend stack, and a harder path to a polished UI.

**Recommendation: Option A.** The dashboard is the part of the project the examiner actually *sees*.
Nothing else is blocked by this choice — the backend, the desktop agent, and the entire computer-vision
pipeline are frontend-agnostic and get built first either way.

---

## 5. Summary

Nothing exists yet, the environment is healthy, and Python 3.11 is correct. The audit surfaced four
issues that would have caused real failures had we coded from assumptions: **no CUDA (an AMD GPU with
misleading leftover NVIDIA DLLs)**, **MediaPipe 1.x removing the API every tutorial uses**, **an
almost-full C: drive that PyTorch would fill**, and **a virtual phone camera needing name-based,
drop-tolerant capture**. All four are now designed around rather than discovered mid-build.
