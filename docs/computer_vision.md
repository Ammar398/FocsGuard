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

## Phase 3 onwards

*To be written as each phase lands: FaceLandmarker and the MediaPipe Tasks API,
EAR with blendshape cross-checking, drowsiness, head pose from the facial
transformation matrix, iris-based gaze, and YOLOv8n phone detection through ONNX
Runtime.*
