# Focus Scoring

How FocusGuard turns observed behaviour into a number, and why each decision was
made that way. Everything here is implemented in
[`scoring/engine.py`](../desktop_agent/focusguard_agent/scoring/engine.py) and
covered by [`tests/test_scoring.py`](../tests/test_scoring.py).

---

## The separation that makes it defensible

```
Detectors            -> what was observed, with a confidence
Event engine         -> discrete events with start, end, duration
Scoring engine       -> what any of it COSTS
```

No detector may modify a score (spec §57). The scoring engine takes a set of
active events and a timestamp and is otherwise a pure function of its own state —
which is why all 36 scoring tests run with no camera, no GPU and no database.

---

## Two axes, combined last

| Axis | Question | Source |
|---|---|---|
| **Visual attention (VAS)** | Were they looking at the screen? | vision events |
| **Digital productivity (DPS)** | Was what they were doing worth doing? | activity monitoring |

They stay independent until the final step, because the interesting cases are the
ones where they disagree (spec §59):

- Staring intently at Netflix → attention high, productivity low
- Reading docs across two monitors → attention lower, productivity high

Collapsing them early would hide exactly the behaviour the project exists to
describe.

```
Combined = (w_v · VAS + w_d · DPS) / (w_v + w_d)          defaults 0.5 / 0.5
```

**When productivity data is unavailable, Combined = VAS.** Not 50, not a blend
with an invented neutral value — a fabricated number would drag every score toward
the middle and the user would still believe it. Until Phase 9 supplies foreground
application data, DPS is `None` and the combined score is simply attention.

---

## Visual attention

```
penalty(t) = Σ  weight[type] × confidence          over ACTIVE events
target(t)  = clamp(100 − penalty, 0, 100)
VAS(t)     = smooth(VAS(t−1), target, dt)
```

### Default weights

| Event | Weight | Reasoning |
|---|---|---|
| `DROWSINESS` | 40 | strongest evidence attention has gone |
| `PHONE_VISIBLE` | 35 | a deliberate, sustained distraction |
| `FACE_ABSENT` | 25 | away from the desk |
| `GAZE_AWAY` | 15 | common in normal work, so weighted lower |
| `HEAD_DOWN` | 15 | often a keyboard glance |
| `EYES_CLOSED` | 10 | brief and frequently innocent |
| `MULTIPLE_FACES` | 10 | context, not necessarily distraction |

**These are starting points from the spec, not tuned values** (§18 is explicit
about this). They are configurable and should be revised after real use.

**Penalties scale with confidence.** A reading where EAR and the blendshapes
disagree costs about half of a confident one (§61).

---

## The anti-oscillation mechanism

Spec §18 forbids `100 → 20 → 100`. The raw penalty *does* jump like that — a phone
appears and 35 points vanish in one frame. The reported score therefore follows the
target through an **asymmetric** exponential filter:

| Direction | α (per second) | Time constant |
|---|---|---|
| Falling | 0.15 | ~6 s |
| Recovering | 0.04 | ~25 s |

A distraction registers promptly; the score has to be earned back. That asymmetry
is the entire mechanism, and it is one line of code.

### Why the alphas are per second, not per update

The pipeline updates at whatever rate it achieves — 12 Hz for landmarks, 1 Hz for
score samples. An alpha applied *per update* would make the score fall ten times
faster on a fast machine than a slow one, and the same user would get different
scores on different hardware.

```python
effective_alpha = 1 − (1 − α) ** dt
```

Tested directly: 10 updates at 1 Hz and 100 updates at 0.1 Hz produce the same
score to within 0.5 points.

A single step is capped at 5 seconds, so resuming after a pause cannot snap the
score to its target.

---

## No double counting

A long eye closure raises **both** `EYES_CLOSED` and `DROWSINESS` — one behaviour,
two events. Adding both penalties (10 + 40 = 50) would charge the user twice and
exceed what either event is meant to cost.

`EVENT_SUPERSEDES` in `focusguard_shared.constants` records that DROWSINESS
supersedes EYES_CLOSED, and `resolve_superseded()` drops the covered event before
any weight is applied. The result is 40, not 50.

---

## Suspended time belongs to nobody

While the camera is unavailable — the phone locked, Wi-Fi dropped, Camo showing
its placeholder — the score **freezes** and no time is accumulated as either
focused or distracted.

This closes the loophole the whole camera-health design exists for: a technical
fault must never quietly drag the session average down.

---

## Multi-monitor (spec §60)

On a `DUAL` or `MULTI` profile:

| Adjustment | Effect |
|---|---|
| Gaze-away grace ×1.75 | a longer glance is tolerated before an event starts |
| Gaze-away weight ×0.4 | the same behaviour costs less |
| Productive-app veto | gaze-away is suppressed entirely while a productive app is focused |

The discount applies **only to gaze**. A phone is a phone regardless of how many
screens are on the desk.

---

## Session summary (spec §42)

Produced by `engine.finish()`:

- duration, monitored, and **suspended** time, kept separate
- focused vs distracted seconds, and the ratio
- average and lowest focus score
- **longest uninterrupted focus block**
- seconds *and* occurrence count per event type

### How "focused" is defined

**Focused = no penalising event active.** Deliberately *not* "score above N": a
threshold on a smoothed value would make focused-time depend on how recently
something else happened, which is neither explainable nor stable.

---

## Digital productivity

```
DPS = 100 × (productive_seconds + 0.5 × neutral_seconds) / total_seconds
```

**Neutral counts as half.** Slack and email are real work. Scoring them zero would
be unfair and — worse — would push users toward gaming the metric by avoiding
communication, a bad outcome for a tool meant to help. Scoring them fully
productive would make the number meaningless.

What counts as productive is **not decided here**. That verdict comes from the
task-context engine (Phase 10), because it depends entirely on what the user is
meant to be doing: YouTube during "Watch Python Lecture" is productive; during
"Implement Authentication" it is not (spec §22).

Gaps longer than 5 seconds between samples are not counted, so a pause is never
attributed to whatever happened to be on screen.

---

## Values that still need tuning

Recorded honestly, since none has been validated against real sessions yet:

| Value | Current | Needs |
|---|---|---|
| Event weights | spec defaults | real sessions to calibrate relative severity |
| α down / up | 0.15 / 0.04 | user feedback on whether recovery feels fair |
| Neutral credit | 0.5 | review once app classification exists |
| VAS/DPS split | 50/50 | revisit after Phase 10 |
