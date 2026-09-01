# Local Database

The agent's SQLite store: what it holds, why it is shaped this way, and how it is
kept from growing forever.

Location: `%LOCALAPPDATA%\FocusGuard\focusguard.db`
Override with `FOCUSGUARD_DATA_DIR`.

---

## Write first, upload later

Everything is written locally **before** anything else happens to it (§37). A
dropped network connection costs nothing; a crash loses at most the last few
seconds. Uploading (Phase 12) reads from the same rows and marks them synced.

## Schema

| Table | Holds |
|---|---|
| `sessions` | one row per monitoring session, with its task |
| `vision_events` | completed attention events with start, end, duration, confidence |
| `activity_events` | application and domain spans with their classification |
| `focus_scores` | the score timeline, ~1 Hz |
| `daily_summaries` | rolled-up totals that outlive the raw rows |
| `meta` | schema version |

### Idempotency — the §37 guarantee

Every event carries a `uuid` generated **on this machine, before the write**, with
a `UNIQUE` constraint behind it.

That is the whole retry design: if an upload times out ambiguously, the agent
simply sends again and the duplicate is rejected **by the database**, not by
hopeful application logic. `record_vision_event()` returns `False` for a
duplicate — not an error, just "already have it".

### WAL mode

The agent writes continuously while a dashboard may be reading. Without
write-ahead logging those readers would block the writer and the capture thread
would stall behind a UI query.

### Foreign keys are ON

Deleting a session removes its events. Without `PRAGMA foreign_keys=ON` SQLite
silently ignores the constraint and leaves orphans.

---

## Time: two clocks, converted once

Durations are measured on a **monotonic** clock, because a wall clock can jump
backwards when the system syncs time — which would produce negative durations and
nonsense reports.

Storage needs real times, so `SessionClock` captures the offset once at session
start and converts on the way out:

```python
wall_time = started_wall + (monotonic_time - started_monotonic)
```

---

## Retention (§45)

On a 128 GB SSD, an unbounded database is a real hazard.

```
raw events        30 days (configurable)
      ↓ roll up
daily_summaries   kept indefinitely — a few hundred bytes per day
```

`roll_up_and_prune()` summarises each day past the window into
`daily_summaries` — total seconds per event type, per application — then deletes
the raw rows. Long-term reports keep working; the row count does not grow forever.

The roll-up is idempotent: running it twice produces one summary, not two.

### Measured

A 25-second session with full monitoring produced:

| | |
|---|---|
| Vision events | 2 |
| Activity events | 2 |
| Score samples | 14 |
| Database size | **72 KB** |

Score samples are written at ~1 Hz rather than per frame. At frame rate this
would be millions of rows a day for no analytical gain — exactly what §45 rules
out.

---

## Verified end to end

A real agent run, with every subsystem active, wrote and read back:

```
session 1: 'Phase 11 verification' task='Build the REST API' (BACKEND)
  vision events: 2   activity events: 2   score samples: 14
    [vision]   FACE_ABSENT      2.0s  conf 1.00
    [activity] Google Chrome    youtube.com   20.5s  NEUTRAL
    [score]    first 100.0  last 72.5

pending upload: {'vision_events': 2, 'activity_events': 2, 'focus_scores': 14}
```

Note `youtube.com` — that domain came from the **browser extension**, through the
loopback IPC server, into the database. And note `NEUTRAL`: YouTube is
`CONTEXT_DEPENDENT` and the task was `BACKEND`, which does not list it, so it was
**not** assumed to be a distraction (§22).

Everything sits at `PENDING`, waiting for the backend that Phase 12 builds.

---

## Inspecting it yourself

```bash
python -c "from focusguard_agent.local_store.database import LocalStore; s=LocalStore(); print(s.recent_sessions(5)); s.close()"
```

Or open the file with any SQLite browser — it is a plain, unencrypted database on
your own machine, which is deliberate: the user should be able to see exactly
what FocusGuard knows about them.
