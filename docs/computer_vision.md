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

## Phase 4 onwards

*To be written as each phase lands: EAR with blendshape cross-checking, drowsiness, head pose from the facial
transformation matrix, iris-based gaze, and YOLOv8n phone detection through ONNX
Runtime.*
