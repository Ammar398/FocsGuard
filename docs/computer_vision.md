# Computer Vision — design, measurements and limitations

Living document. Each phase adds its algorithm description and its measured cost
on the reference machine (Intel i5-6500, 4C/4T, AMD RX 580 8 GB, Windows 11).

---

## Pipeline overview

```
Camera (Camo, 1280x720)
   -> capture thread: downscale to 640x360, latest-frame slot
   -> vision thread:  FaceLandmarker ~12 Hz | PhoneDetector ~3 Hz
   -> event engine:   grace periods, de-duplication, discrete events
   -> scoring engine: visual attention + digital productivity
```

Detection, interpretation and scoring stay separate. A detector reports what it
observed with a confidence; it never concludes that the user is distracted, and it
never modifies a score (spec section 57).

---

## Phase 2 — Camera capture

### The camera on this machine

There is **no physical webcam**. All three capture devices are virtual:

| Index | Device | Notes |
|---|---|---|
| 0 | Camo | Phone as webcam (Reincubate Camo Studio) — the one in use |
| 1 | DroidCam Video | Alternative phone-as-webcam |
| 2 | Pixel 7 (Windows Virtual Camera) | Windows 11 Connected Camera |

This shapes three design decisions.

**Devices are selected by name, not index.** Virtual cameras register when their
host app launches and de-register when it closes, so index 0 is Camo today and
DroidCam tomorrow. `capture/devices.py` enumerates through **DirectShow** — chosen
specifically because OpenCV's `CAP_DSHOW` backend indexes cameras in DirectShow
order, so enumeration positions map directly onto the index OpenCV expects.
PowerShell and Media Foundation list the same devices in a different order and
their positions cannot be used as OpenCV indices.

**Camo ignores every resolution request.** Measured directly:

| Requested | Delivered |
|---|---|
| 320×180, 320×240, 640×360, 640×480, 854×480, 960×540 | **1280×720** |
| 1280×720 | 1280×720 |

The device exposes one fixed mode. Downscaling in software is therefore mandatory,
not a fallback.

**The downscale must preserve aspect ratio.** This is a correctness requirement,
not a cosmetic one. Forcing 1280×720 (16:9) into 640×480 (4:3) compresses the image
horizontally by 25%, and the vision pipeline measures geometry directly from pixels:

- **EAR** is a ratio of eye height to eye *width*. Horizontal compression inflates
  it by roughly a third, making blink thresholds and calibration meaningless.
- **Head yaw** is inferred from a face that would no longer have face proportions.

`fit_within()` scales to fit inside the configured box while preserving aspect, so
1280×720 becomes **640×360** — under the 640×480 budget and geometrically faithful.
`frame_width`/`frame_height` in the configuration are a *bounding box*, not an exact
output shape.

### Latest-frame slot, not a queue

The capture thread overwrites a single frame slot. When the pipeline falls behind,
uncollected frames are dropped rather than buffered.

A queue would grow whenever processing lagged, so the pipeline would analyse
progressively older frames and the focus score would describe the past rather than
the present. A phone camera over Wi-Fi already adds latency of its own. A drop rate
above zero is the expected healthy state, not an error.

### Camera health is not user behaviour

When frames stop arriving — phone locked, Wi-Fi dropped, Camo Studio closed — the
source reports `CameraState.UNAVAILABLE` after a configurable 2 s grace period.

This must never be confused with the user leaving. A face detector handed no frames
finds no face, and recording that as `FACE_ABSENT` would penalise someone for a
technical fault they did not cause. Camera health is exposed as a first-class signal
that the event engine consumes *before* drawing any behavioural conclusion
(spec sections 11, 61). `VisionFrameObservation.face_present` returns True only when
the camera is healthy **and** a face was found.

Reconnection is automatic: ten consecutive failed reads trigger a reopen, with a
configurable wait between attempts so a missing camera cannot spin the CPU.

### Two camera failures, not one

The first implementation checked only "have frames stopped arriving?". That misses
the failure that actually happens most often with a phone camera.

| State | Meaning | Cause |
|---|---|---|
| `UNAVAILABLE` | No frames arriving | Camo Studio closed, device unplugged, Wi-Fi lost |
| `NO_SIGNAL` | Frames arriving, but not live video | **Phone locked** |

**Camo does not stop streaming when the phone locks.** It substitutes a static
placeholder image - a grey frame with a camera icon - and keeps sending it. Every
stopped-stream check reports a perfectly healthy camera, the face detector finds
nobody, and after the two-second grace `FACE_ABSENT` fires against a user who did
nothing wrong. This was found during Phase 3 testing, by locking the phone and
observing that the status panel stayed green.

**Detection.** Each frame is reduced to a 64x36 greyscale fingerprint and compared
with the previous one. Measured on this machine, **199 consecutive placeholder
frames had a mean absolute difference of exactly 0.0000**. A real sensor cannot do
that: even a perfectly still scene carries noise. Frames that stay bit-identical
for longer than `static_feed_seconds` (default 3 s) are therefore treated as a
synthetic source, and monitoring suspends exactly as it does for any other camera
fault.

The comparison tolerance defaults to **0.0 - bit-identical**, deliberately the most
conservative setting. Missing a placeholder is a much better failure than wrongly
discarding a genuine absence, so the test errs towards trusting the feed. Cost is a
few microseconds per frame.

A useful secondary signal, not currently used for detection: Camo also throttles to
about 1 FPS while showing the placeholder, against 25-28 FPS when live.

### Measured performance

25-second run, Camo over Wi-Fi, capture only (no detection yet):

| Metric | Value |
|---|---|
| Camera resolution | 1280×720 |
| Processed resolution | **640×360** |
| Capture rate | **26.1 FPS** |
| Frames consumed | 25.0 FPS |
| Frames dropped | 4% |
| `capture_read` latency | 0.01 ms mean, 0.02 ms p95 |
| Process CPU | **~7–14% of one core** (≈2–3% of 4 cores) |
| Process CPU peak | 42% of one core |
| Process memory | 96 MB |

Mean CPU varied between 7% and 14% of one core across runs; treat it as a range
rather than a single figure. The reader thread costs almost nothing — the work is
the 1280×720 → 640×360 `INTER_AREA` resize, which happens once per frame.

**Budget:** spec section 44 requires the system never to sit at full CPU. Target is
60% of 4 cores (240% of one core). Capture uses roughly 14% of that, leaving ample
headroom for FaceLandmarker (~12 Hz) and YOLO (~3 Hz, on the GPU via DirectML).

### Limitations

- Camo delivers only 720p, so a software resize is unavoidable on this machine.
- Wi-Fi capture adds latency the agent cannot measure or correct for.
- The driver reports `CAP_PROP_FPS` as −1, so the real rate is measured over a
  rolling window instead of trusted from the device.
- If the configured camera is absent, the agent falls back to another device and
  logs a warning. Fine for development; Phase 17 (device enrollment) should
  reconsider whether silent fallback is right for a managed deployment.

---

## Phase 3 — Face landmarks and presence

### Algorithm

MediaPipe Tasks `FaceLandmarker` in VIDEO mode, `face_landmarker.task` (float16,
3.8 MB), 478 landmarks including iris, with blendshapes and the 4x4 facial
transformation matrix enabled.

The legacy `mp.solutions.face_mesh` API used by almost every tutorial **does not
exist in MediaPipe 1.x** and raises `AttributeError`. See CURRENT_STATE.md §2.6.

### Measured performance

25-second run, Camo live, user in frame for the whole run:

| Metric | Value |
|---|---|
| Frames with a face | **219/219 (100%)** |
| Landmark inference | **13.80 ms mean**, 15.36 ms p95, 22.30 ms max |
| Capture rate | 26.1 FPS at 640×360 |
| Process CPU | **38% of one core (9% of 4 cores)** |
| Process memory | 174 MB |

The Phase 0 estimate was 12–20 ms per landmark call. Measured 13.8 ms, so the
frame budget in ARCHITECTURE_PLAN.md §4.2 holds on real hardware. At 12.5 Hz the
landmark stage costs about 17% of one core, leaving ample room for YOLO.

**Note:** with no face in frame FaceLandmarker skips the landmark model entirely
and reports 2–5 ms. Any timing taken without a face is meaningless, so the
benchmark prints the face-detection share and warns when it is low.

### Detection rate is quantised by the frame grid

The same run reported `Detections run: 219 (8.8 Hz, target 12 Hz)` — a 30%
shortfall with no CPU saving.

A stage can only run when a frame is available, so achievable rates are divisors
of the capture rate. The camera delivers a frame every 40 ms; a 12 Hz target asks
for one every 83.3 ms. A naive `now - last >= interval` test rejects the frame at
80 ms for being 3 ms early and waits for 120 ms, giving **8.3 Hz**.

`FrameRateLimiter` allows a stage to run up to **half a frame period early**, so
it snaps to the nearest frame boundary instead of overshooting. The 12 Hz target
then settles at **12.5 Hz** — the closest rate a 25 FPS grid can deliver.

The half-frame allowance matters at low rates too. Phone detection at 3 Hz
(333 ms) lands on the 320 ms frame for 3.1 Hz, whereas a cruder "run at 60% of the
interval" rule would fire at 200 ms and give 5 Hz — two-thirds faster than asked,
on the most expensive stage in the pipeline.

This is worth knowing for Phase 4: a blink lasts 100–400 ms, so 8.3 Hz would
cover a fast blink with one or two samples where 12.5 Hz gives three or four.

### Event engine

All behavioural events come from one `EventStateMachine`
(`IDLE → CANDIDATE → ACTIVE`). Its rules apply to every detector in the system:

- **Grace period** — the condition must hold before it is believed. Face absence
  2 s, gaze away 4 s, phone 1.5 s (all configurable).
- **Event start is when the condition began**, not when the grace elapsed. A 6 s
  look-away with a 3 s grace is recorded as 6 s.
- **Exit hysteresis** — one recovered frame cannot split a continuous event in two.
- **Confidence is the mean** over the active window, never the peak.
- **Minimum duration** — separates a blink from an `EYES_CLOSED` event.

Verified in live testing: `FACE_ABSENT` events of 3.0 s and 6.7 s were recorded
with correct durations, and brief absences produced nothing.

---

## Phase 4 — Eye Aspect Ratio, blinks and eye closure

### The algorithm

EAR (Soukupova & Cech, 2016) reduces an eye to one number from six landmarks:

```
EAR = (‖p2-p6‖ + ‖p3-p5‖) / (2 * ‖p1-p4‖)

        p2   p3
   p1 .--.---.--. p4      numerator   = eye HEIGHT (two vertical spans)
        p6   p5           denominator = eye WIDTH  (corner to corner)
```

Landmark indices: left `(33, 160, 158, 133, 153, 144)`, right `(362, 385, 387, 263, 373, 380)`.

| | |
|---|---|
| **Input** | 6 landmarks per eye, in **pixel** coordinates |
| **Output** | one float per eye, ~0.25–0.35 open, ~0.05–0.10 closed |
| **Why chosen** | scale-invariant, 12 subtractions to compute, and fully explainable — no model, no training |
| **Limitation** | degrades at steep head yaw, where the eye foreshortens |

Dividing by eye width is the point: sitting closer grows height and width
together, so the ratio holds regardless of distance from the camera.

### Pixels, not normalised coordinates

MediaPipe normalises x against image *width* and y against image *height*. On a
640×360 frame those axes have different pixel scales, so computing EAR directly
from normalised coordinates multiplies it by width/height — **1.78 on this
camera**. Every published threshold and every saved calibration would be wrong.

This is the same class of bug as the Phase 2 aspect-squash, so it is pinned by a
test asserting the same eye scores identically at 640×360 and 640×480.

### Two independent signals

EAR is geometric and degrades with head angle. MediaPipe's `eyeBlinkLeft` /
`eyeBlinkRight` blendshapes come from a largely pose-normalised model, so they
fail differently.

**EAR decides; the blendshapes say how confident that answer is.** Agreement gives
confidence 0.95, disagreement 0.5, and the scoring engine multiplies penalties by
confidence — so a contested reading costs the user roughly half as much
(spec section 61).

### Personal calibration

The commonly quoted 0.21 threshold is an average, not a fit. A user whose relaxed
EAR is 0.19 would read as permanently asleep; one at 0.34 would never blink.

```
threshold = mean_open_EAR - (k * std_open_EAR)     k = 2.5, clamped to [0.15, 0.30]
```

- Blinks **are expected** during calibration; the lowest 20% of readings are
  dropped before the statistics are computed. Asking a user to hold their eyes
  open produces strained, unrepresentative eyes.
- The clamp bounds a bad calibration to "slightly wrong" instead of "completely
  broken", and a clamped result is flagged so it can be re-run rather than hidden.
- Stored at `%LOCALAPPDATA%\FocusGuard\calibration.json`; a corrupt file falls
  back to the default rather than stopping the agent.

### Blink versus closure

| Duration | Classification | Cost |
|---|---|---|
| < 0.4 s | `BLINK` | none — counted only |
| ≥ 1 s | `EYES_CLOSED` | penalised |
| ≥ 2.5 s | `DROWSINESS` | heavily penalised (Phase 5) |

A long closure is **not** also counted as a blink, or resting your eyes would look
like rapid blinking. Blink rate over a rolling minute is accumulated here for
Phase 5, since both suppressed and elevated blink rates are drowsiness indicators.

Eye monitoring **suspends** whenever the camera fails, the face leaves frame, or
the eye is too small to measure. In none of those cases do we know whether the
eyes are shut, so nothing is attributed to the user.

---

## Phase 5 — Drowsiness

### Measurement versus interpretation

`EYES_CLOSED` is a measurement: the eyes were shut for over a second.
`DROWSINESS` is an *interpretation* of that measurement, and interpretations need
more evidence (spec section 61).

A prolonged closure (>= 2.5 s) is the **trigger**. Three further signals set the
**confidence**:

| Signal | What it catches |
|---|---|
| Prolonged closure >= 2.5 s | the trigger — far longer than any blink |
| **PERCLOS** | many partial closures instead of one long one |
| Slow closures (0.4–2.5 s) | blinks that have become sluggish |
| Head pitched down | the nodding that accompanies real sleepiness (Phase 6) |

```
confidence = eye_confidence * (0.80 + 0.06 * corroborating_signals)   capped at 0.98
```

An uncorroborated closure lands around 0.76; a fully corroborated one near 0.95.
`eye_confidence` carries through from the EAR/blendshape agreement, so uncertainty
in the measurement propagates into the interpretation rather than being lost.

### PERCLOS

PERCLOS — the percentage of time the eyes are closed over a rolling 60-second
window — is the most extensively validated drowsiness measure in the
driver-fatigue literature. It catches a failure mode invisible to a duration
threshold: somebody fighting sleep produces many half-second closures, never one
obvious three-second one, and a single-closure test scores them wide awake.

It **corroborates only**. A user reading a long document blinks more and could
drift over the threshold, and "you look sleepy" on that basis alone is exactly the
weak claim section 61 forbids.

Samples more than 1 s apart are excluded from the calculation, so camera downtime
is never counted as eyes-closed time.

### No double counting

A `DROWSINESS` event always overlaps an `EYES_CLOSED` event — the same closure
produced both. Summing their weights (40 + 10) would charge the user twice for one
behaviour and exceed what either event is meant to cost.

`EVENT_SUPERSEDES` in `focusguard_shared.constants` records that DROWSINESS
supersedes EYES_CLOSED, and `resolve_superseded()` drops the covered event before
scoring. Phase 8 must use it.

### What is not claimed

Observable behaviour only: the eyes were shut for N seconds, with these
corroborating signals, at this confidence. No diagnosis of fatigue, sleep
disorders or any medical condition (spec section 8). User-facing wording is
"extended eye closure detected", never "you are asleep".

---

## Phase 6 — Head pose and gaze

### Head pose: two estimators, deliberately

**Angles come from MediaPipe's 4x4 facial transformation matrix.** The usual
alternative, `cv2.solvePnP` against a generic 3D face model, adds error nobody can
quantify — the model is not the user's face. The matrix comes from the same model
that placed the landmarks, so no geometry is assumed.

**Direction comes from landmark geometry in image space.** A rotation matrix's
sign convention depends on the axis convention of whoever produced it, and getting
it backwards would label a left turn as a right one. Image-space geometry has no
such ambiguity.

So: **magnitude from the matrix, direction from the landmarks.** They are
independent, which gives the same cross-check used elsewhere — agreement raises
confidence, disagreement lowers it.

The landmark proxy:

```
offset = (nose_x - face_centre_x) / face_width

offset > 0  ->  nose right of centre  ->  user turned to their own LEFT
offset < 0  ->  nose left of centre   ->  user turned to their own RIGHT
```

Scale is divided out of the matrix before decomposing — it carries head *size* as
well as rotation, and leaving it in makes the angles drift with how close the user
sits.

**Convention:** angles are from the user's own perspective. `yaw` positive = turned
left, `pitch` positive = looking up. With `flip_horizontal = false` a left turn
appears on the *right* of the preview, because the camera is not mirrored.

Yaw takes precedence over pitch when classifying: turning away from the screen is
a stronger signal than a downward glance, which often still faces the monitor.

### Gaze: the honest position

The specification is explicit — *do not pretend webcam gaze tracking is accurate*.
Proper gaze tracking uses infrared illumination and corneal reflections. This has a
phone camera at 640×360 where **the iris is about 15 pixels across**, so one pixel
of landmark error is several degrees of gaze.

Output is a coarse direction plus a confidence, never a screen coordinate.
`UNKNOWN` is a normal result, not a failure.

```
gaze = 0.7 * head_direction + 0.3 * iris_offset_within_eye
```

Head dominates because looking away is mostly a head movement. Confidence falls
when the eyes close (the iris is hidden), the face is small, yaw is large, or the
iris geometry disagrees with the `eyeLookIn/Out` blendshapes.

### The asymmetry that protects the user

When gaze confidence drops below the threshold, the direction is `UNKNOWN`, and the
attention monitor treats that as **no evidence of looking away** — never as
evidence of it.

This is deliberate. A missed distraction costs a little accuracy. A fabricated one
costs the user points they did not lose and tells them something false about their
own behaviour. With a 15-pixel iris the uncertain case is common, so it must fail
towards the user (spec section 61).

### Multi-monitor (spec section 60)

On a dual or multi-monitor profile:

- the gaze-away grace period is extended by 1.75×;
- gaze-away is **suppressed entirely** while the foreground application is
  classified productive — reading documentation on a second screen with the IDE
  focused is work, not distraction.

Phase 8 additionally scales down the penalty weight. This phase decides whether the
*event* happens at all.

### Grace periods

| Event | Grace | Why |
|---|---|---|
| `GAZE_AWAY` | 4 s (7 s multi-monitor) | normal work is full of brief glances |
| `HEAD_DOWN` | 3 s | glancing at a keyboard is not distraction |

### Limitations

- Beyond ~45° of yaw one side of the face is barely visible and its landmarks are
  extrapolated; both estimators degrade, reported as falling confidence.
- Gaze cannot distinguish looking at a second monitor from looking out of a window.
  Only the activity context (Phase 9/10) can tell those apart.
- No screen-coordinate gaze point is produced, and none should be inferred.

---

## Phase 7 — Phone detection

### Why ONNX Runtime rather than the Ultralytics runtime

`YOLO("yolov8n.pt").predict()` is one line, and the wrong choice on this machine:

| | Ultralytics + PyTorch | ONNX Runtime + DirectML |
|---|---|---|
| Uses the RX 580 | **No** — PyTorch on Windows is CUDA or CPU | **Yes** |
| Disk cost | ~2.5 GB | ~12 MB model |
| Competes with MediaPipe for CPU | Yes | No — runs on the GPU |

PyTorch is used **once**, by `scripts/export_yolo.ps1`, to produce the ONNX file.
It never becomes a runtime dependency.

The export runs in a **throwaway venv**, because `ultralytics` depends on
`opencv-python`, which installs the same `cv2` module as the
`opencv-contrib-python` the agent uses — installing it into the main environment
risks replacing a working OpenCV.

### The pipeline, written by hand

```
frame (640x360 BGR)
  -> letterbox to 320x320, aspect preserved     _letterbox
  -> BGR->RGB, HWC->CHW, /255, batch            _preprocess
  -> ONNX Runtime (DirectML, CPU fallback)      session.run
  -> output (1, 84, 2100)                       4 box rows + 80 class rows
  -> filter class 67 above threshold            _postprocess
  -> non-maximum suppression                    _nms
  -> boxes mapped back to frame coordinates
```

Writing this by hand costs ~100 lines and buys the ability to explain every step
from pixels to bounding box at defence, rather than pointing at a library call.

**Letterbox, not resize.** Squashing 16:9 into a square would distort every
object; a phone is strongly rectangular and would be stretched into a shape the
model never saw in training. Same aspect-ratio discipline as Phase 2.

**Padding is removed before the scale is undone.** Doing it the other way round
shifts every box by `padding x scale` — a plausible-looking but wrong result, so
it has its own test.

**NMS is required.** YOLO proposes many overlapping boxes per object; without
suppression one phone becomes a dozen detections.

**Class 67 = `cell phone`** in COCO. No training or dataset needed.

### One event per appearance

Detection at 3 Hz over a 30-second appearance produces ~90 positive frames.
Naively that is 90 events, and detection flickers — a hand crosses the phone, it
tilts and catches a reflection — so even grouping consecutive positives would
split one appearance into several.

Both are handled by the shared state machine:

| Mechanism | Value | Effect |
|---|---|---|
| Grace period | 1.5 s | a phone glimpsed while reaching past the desk is not an event |
| Exit hysteresis | 2 s (`phone_merge_gap_seconds`) | a detection dropout shorter than this keeps one event open |

Result: **one event per continuous appearance**, with an honest start, end and
duration — which is what the timeline and the "phone distractions: 6" figure need.

Unlike the eye and gaze monitors, phone detection requires only a working camera,
**not** a visible face: a user holding a phone below the desk may be out of frame
themselves while the phone is not.

---

## Phase 8 onwards

*To be written as each phase lands: EAR with blendshape cross-checking, drowsiness, head pose from the facial
transformation matrix, iris-based gaze, and YOLOv8n phone detection through ONNX
Runtime.*
