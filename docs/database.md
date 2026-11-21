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

---

## The backend database

SQLAlchemy models in `backend/focusguard_backend/models/`, migrated by Alembic
(`backend/focusguard_backend/database/migrations/versions/`). SQLite in development, PostgreSQL in
production via `DATABASE_URL`.

| Table | Purpose | Tenant key |
|---|---|---|
| `organizations` | A business | — |
| `users` | Students, managers, employees (one table, `role` column) | `organization_id` (NULL for students) |
| `departments` | Teams within an organisation | `organization_id` |
| `devices` | Enrolled agents; hashed enrolment token and device secret | `organization_id` |
| `privacy_consents` | Append-only record of accepted notices | via `user_id` |
| `refresh_tokens` | Hashed, rotating, family-revocable | via `user_id` |
| `projects` | Groups of tasks | `organization_id` |
| `tasks` | Assigned work, rules (JSON lists), status, rating, rework | `organization_id` |
| `focus_sessions` | One monitoring session; `task_id` makes it a task session; totals | `organization_id` |
| `vision_events` | Attention events (UNIQUE `event_uuid`) | `organization_id` |
| `activity_events` | Application / domain spans with classification and rule source | `organization_id` |
| `focus_scores` | ~1 Hz score samples | `organization_id` |
| `device_heartbeats` | Liveness pings | via `device_id` |

`organization_id` is denormalised onto every tenant row so isolation is one indexed predicate.
Why some planned tables were folded together (`task_assignments`, `task_sessions`, `employee_metrics`):
`ARCHITECTURE_PLAN.md` §14.

### Server retention

`python -m focusguard_backend.tasks.retention [--days 30] [--dry-run]` writes each old session's
productive / neutral / distracting seconds onto the session row, then deletes its raw events, activity
spans and score samples. Task scores and strength history are unchanged by it
(`tests/test_backend_retention.py`).
