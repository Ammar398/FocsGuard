# Agent → Backend Synchronisation

How events recorded on a student's machine reach the server, and why the design
survives a dropped connection without losing or duplicating anything.

Spec sections: **§25** (enrolment, consent), **§33** (what may be transmitted),
**§37** (offline operation and retries).

---

## The one rule that matters

> **Upload first. Mark synced only after the server confirms. Never the reverse.**

If the agent marked rows synced before the response arrived and the connection
then dropped, those events would be gone forever — the local queue would no
longer offer them, and the server never received them.

In this order the worst case is a **duplicate send**, and a duplicate costs
nothing because every event carries a client-generated `event_uuid` with a
`UNIQUE` constraint behind it on both sides.

That is the whole of the §37 retry design: it lets the agent be careless about
retrying and still be exactly correct.

```
  record  ──▶  local SQLite  ──▶  upload  ──▶  server confirms  ──▶  mark synced
                    ▲                                                     │
                    └───────────── still PENDING on any failure ──────────┘
```

---

## Enrolment is three steps on purpose (§25)

| Step | Who | What they get |
|---|---|---|
| 1 | a manager, or a student for their own machine | a **single-use enrolment token**, valid one hour |
| 2 | the person at the machine | types it into `tools.enroll` |
| 3 | the agent | a **long-lived device secret**, stored locally |

The obvious shortcut — handing out the long-lived secret directly — is the wrong
shape. The enrolment token travels through a chat message, an email, or a sticky
note, and none of those is a confidential channel. So the thing that **travels**
expires quickly and works once, and the thing that **lasts** never leaves the
machine it was issued to.

```
python -m focusguard_agent.tools.enroll --token <token>   # connect this machine
python -m focusguard_agent.tools.enroll --status          # am I enrolled?
python -m focusguard_agent.tools.enroll --forget          # disconnect it
```

The consent notice (§25) is printed **before** anything is sent, and enrolment
stops if it is declined. It is not marketing copy — it lists the literal contents
of the upload payload.

### Not being enrolled is a normal state

An unenrolled agent runs completely normally and records everything locally. It
simply uploads nothing. A student with no server still gets the full local
experience, which is what §37 requires and what makes the project demonstrable
without a backend running.

---

## What is stored on the machine

`%LOCALAPPDATA%\FocusGuard\device.json`

```json
{
  "device_uuid": "5c732072-...",
  "device_secret": "...",
  "backend_url": "http://127.0.0.1:8000",
  "user_id": 1
}
```

Written owner-only where the platform allows, best-effort on Windows via
`icacls`. The threat model is stated plainly rather than overclaimed:

> An attacker who can already read this user's `LOCALAPPDATA` can read the
> credential. What it gets them is the ability to upload junk telemetry for one
> user — it **cannot read anybody's data**. That is a deliberately small blast
> radius, enforced by the backend, not by this file.

The `device_uuid` is generated **before** enrolment and survives `--forget`, so a
machine that is revoked and re-enrolled reads as one machine's history rather
than two.

---

## Threading: uploads never block the camera

The agent's tick runs about a hundred times a second with a frame budget measured
in milliseconds. One HTTP request can take fifteen seconds when a network is
failing badly — which is exactly when it is least acceptable to stop looking at
the camera.

So uploads run on their own thread. It is almost always blocked on a socket, and
`requests` releases the GIL while it waits, which matters on a 4-core/4-thread
i5-6500 where a busy thread would be stealing from the vision pipeline.

**The exception is shutdown**, where `flush()` runs on the main thread
deliberately: the user is already waiting for the agent to finish, and a session
that appears on the dashboard immediately is far friendlier than one that waits
for the next launch.

---

## Failure handling

| Failure | Classified as | What happens |
|---|---|---|
| offline, DNS, timeout, 5xx | temporary | backoff doubles, up to 10 minutes |
| **401 / 403** credential wrong or revoked | **permanent** | the thread stops |
| **422** this server will never accept this payload | **permanent** | the thread stops |

A success resets the backoff immediately rather than gradually — a server that
just answered is not a server to keep treating as down.

Stopping on a permanent refusal is the point. Retrying forever would hammer the
server and hide a problem that needs a person to fix it.

---

## Batching

One batch = **one session** plus that session's pending rows, capped per table.

* Never two sessions in one request, so a partial failure cannot strand half of
  one session's events.
* Capped **per table** rather than in total, or a session with thousands of score
  samples would starve its vision events out of every batch.
* A **finished** session carries its totals; a **running** one omits them rather
  than sending numbers that are wrong now and corrected later.

Those totals come from `reporting/session_report.py` — the same code that prints
the local summary — so the dashboard and the terminal cannot disagree. Verified:
a live run showed `Average focus 72.3` in the terminal and `avg=72.3` through the
API.

---

## What is never sent

Frames, screenshots, window contents, typed input, clipboard, full URLs.

Every value in a payload is read from a database column, and the local schema has
nowhere to hold any of those in the first place (§33). The backend's upload
schemas use `extra="forbid"`, so a field that should not exist is a `422` rather
than a silent drop.

`tests/test_synchronization.py` serialises a real payload and asserts none of
those words appear in it, and that an activity event's keys are exactly the
twelve allowed ones.

---

## Verified end to end

On the reference machine, against a live uvicorn backend:

| Step | Result |
|---|---|
| student registers and logs in | 201, then a token pair |
| manager/student creates an enrolment token | 201 |
| `tools.enroll --token` | enrolled; secret stored, never printed |
| the same token again | `400` — single use |
| the real agent runs 25 s and stops | `Sync: 1 session, 21 events accepted, 0 duplicates` |
| student reads `/api/v1/sessions` | 1 session, 1 vision event, 1 app, 19 scores |
| response scanned for forbidden content | none |
| **backend stopped**, 5 events recorded | `failures=1`, all 5 still `PENDING` |
| **backend restarted**, sync again | `accepted=5`, queue empty |
| one more pass | nothing sent — no pointless request |

---

## Configuration

```
FOCUSGUARD_SYNC__ENABLED=true
FOCUSGUARD_SYNC__UPLOAD_INTERVAL_SECONDS=30
FOCUSGUARD_SYNC__BATCH_SIZE=50
FOCUSGUARD_SYNC__MAX_BACKOFF_SECONDS=600
FOCUSGUARD_SYNC__REQUEST_TIMEOUT_SECONDS=15
```

`ENABLED=false` is for someone who *is* enrolled and wants to stop uploading
without unenrolling. An unenrolled machine skips uploading regardless.

---

## Not yet done

**Heartbeats are implemented but not yet scheduled.** `BackendClient.heartbeat()`
works and is tested; the agent does not call it on a timer yet. It has no
consumer until the manager dashboard (Phase 18) needs a live device board.

**No live score stream.** The portal will poll `/api/v1/sessions` until a
WebSocket is added.
