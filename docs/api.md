# Backend API

FastAPI service at `/api/v1`. Interactive docs at `/docs` when running.

```bash
cd E:\focusguard
.venv\Scripts\activate
python -m uvicorn focusguard_backend.main:app --reload
```

Then open **http://127.0.0.1:8000/docs**.

---

## Why FastAPI, not Flask

Three reasons specific to this project rather than general praise:

- the Pydantic models in `api/v1/schemas.py` **are** the validation, so the wire
  contract is described once and cannot drift from what is enforced;
- async support, which the live-score WebSocket in Phase 14 needs;
- automatic OpenAPI docs, a genuinely useful artefact to demonstrate at defence.

There was no reason to fall back to Flask.

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | liveness; reports the dialect, never credentials |
| `POST` | `/api/v1/agent/events` | upload a batch of events |
| `POST` | `/api/v1/agent/heartbeat` | report device liveness |
| `GET` | `/api/v1/sessions` | list sessions |
| `GET` | `/api/v1/sessions/{id}` | one session with its full timeline |

Everything is under `/api/v1`. Agents are installed software that updates on
their own schedule, so a deployed backend will always be serving some older
version — the prefix is what makes that survivable.

---

## Retries are expected, not exceptional

An agent whose upload times out **does not know whether it landed**. Section 37
says it should simply send again.

Every event carries a client-generated `event_uuid` with a `UNIQUE` constraint
behind it, so the second attempt inserts nothing:

```json
// first upload
{"accepted": {"vision_events": 2}, "duplicates": {"vision_events": 0}}

// exact same batch, sent again
{"accepted": {"vision_events": 0}, "duplicates": {"vision_events": 2}}
```

`duplicates` is **reported, not hidden**. A healthy agent recovering from a
dropped connection sees a non-zero count, and that is success — not an error
worth alarming anyone about.

Duplicates *within* a single batch are also handled, so one malformed upload
cannot fail the whole request.

The batch carries its own session rather than referencing one created earlier, so
an agent that ran entirely offline can upload a complete finished session in one
request with no prior round trip.

---

## The schema is a privacy control

There is deliberately **no field anywhere** for image data, screen contents, URLs
or input text (§33, §34). A compromised agent could not upload a screenshot
because there is nowhere to put one.

`extra="forbid"` means an unexpected field is **rejected with 422**, not silently
dropped:

```python
batch["activity_events"][0]["url"] = "https://google.com/search?q=private"
→ 422 Unprocessable Entity
```

Silently discarding it would lose data nobody realises is missing — and would let
a regression in the extension start leaking URLs unnoticed.

---

## Tenant isolation (§51)

> *A manager from Company A must NEVER access Company B data.*

That is a security requirement, so it is **structural**, not a convention:

```python
class Scope:
    user_id: int | None
    organization_id: int | None
    # A scope with neither would match everything, so it refuses to exist.
```

Every repository read **requires** a `Scope` in its signature. Forgetting it is a
`TypeError` at import time, not a data leak in production. `organization_id` is
denormalised onto every telemetry row so the filter is one indexed predicate
rather than a join someone has to remember to write.

[`tests/test_backend_isolation.py`](../tests/test_backend_isolation.py) proves it
— 12 tests covering the repository and the HTTP layer, including that both
filters apply together, that a guessable session UUID is no way around it, and
that isolation is not achieved by returning nothing to anybody.

A session outside your scope returns **404, not 403**: confirming that a session
exists but is not yours still tells a stranger that it exists.

---

## Database

| | |
|---|---|
| Development | SQLite (WAL, foreign keys ON) |
| Production | PostgreSQL |
| Migrations | Alembic |

Nothing in the models uses a dialect-specific type, so moving to PostgreSQL is a
`DATABASE_URL` change.

SQLite needs `PRAGMA foreign_keys=ON` explicitly — it **silently ignores** foreign
keys otherwise, which would mean cascades that work in tests and quietly leave
orphans in production.

### Migrations

```bash
cd backend
set DATABASE_URL=sqlite:///E:/focusguard/backend/focusguard.db

python -m alembic upgrade head                          # apply
python -m alembic revision --autogenerate -m "message"  # create
python -m alembic downgrade -1                          # roll back one
```

The URL comes from the environment, never from `alembic.ini` — a connection
string in a committed file is a credential in version control.

`render_as_batch=True` is set for SQLite, which cannot `ALTER` most columns.
Without it the first migration that changes a column would work on PostgreSQL and
fail on the development database.

Verified: the initial migration applies to a fresh database and **downgrades back
to nothing** cleanly.

---

## Not yet done

**Authentication.** Phase 12 identifies a device by its UUID, and session routes
take `user_id` as a query parameter. That is obviously not authentication — it is
a placeholder so the routes could be written against a `Scope` from the start,
rather than having isolation retrofitted later, which is when isolation bugs get
introduced.

**Phase 13** replaces `resolve_device()` and `current_scope()` with JWT and device
credentials. Both are single functions, and every route already depends on them,
so no route changes.
