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

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | none | liveness; reports the dialect, never credentials |
| `POST` | `/api/v1/auth/register` | none | create a student account |
| `POST` | `/api/v1/auth/login` | none | exchange a password for a token pair |
| `POST` | `/api/v1/auth/refresh` | refresh token | rotate, and get a new access token |
| `POST` | `/api/v1/auth/logout` | refresh token | end one session |
| `POST` | `/api/v1/auth/logout-all` | user | end every session |
| `GET` | `/api/v1/auth/me` | user | who the caller is |
| `POST` | `/api/v1/devices` | user | prepare a device, get a one-time enrolment token |
| `POST` | `/api/v1/devices/enroll` | enrolment token | redeem it for a device credential |
| `GET` | `/api/v1/devices` | user | list devices in scope |
| `DELETE` | `/api/v1/devices/{id}` | user | revoke a device; its history is kept |
| `POST` | `/api/v1/agent/events` | **device** | upload a batch of events |
| `POST` | `/api/v1/agent/heartbeat` | **device** | report device liveness |
| `GET` | `/api/v1/sessions` | user | list sessions |
| `GET` | `/api/v1/sessions/{id}` | user | one session with its full timeline |
| `GET` | `/api/v1/reports/summary` | user | daily totals over a window |
| `GET` | `/api/v1/reports/recommendations` | user | rule output, each with evidence |
| `GET` | `/api/v1/reports/export.csv` | user | the caller's sessions as CSV |
| `POST` | `/api/v1/auth/register-organization` | none | create an organisation and its first manager |
| `GET` | `/api/v1/agent/tasks` | **device** | the open tasks assigned to this device's user |
| `GET` | `/api/v1/manager/organization` | manager | the caller's organisation |
| `GET` | `/api/v1/manager/dashboard?days=` | manager | §26 overview: online, tasks, time split, top apps/domains, teams |
| `GET` `POST` | `/api/v1/manager/departments` | manager | list / create |
| `PATCH` `DELETE` | `/api/v1/manager/departments/{id}` | manager | rename / delete (people kept) |
| `GET` `POST` | `/api/v1/manager/employees` | manager | list with today's stats / create (password optional) |
| `GET` `PATCH` | `/api/v1/manager/employees/{id}` | manager | §27 detail / update, deactivate, set password |
| `GET` | `/api/v1/manager/employees/{id}/strengths` | manager | §30 task-category history |
| `GET` `POST` | `/api/v1/manager/projects` | manager | list / create |
| `PATCH` | `/api/v1/manager/projects/{id}` | manager | rename, archive |
| `GET` `POST` | `/api/v1/manager/tasks` | manager | list (filters) / create with rules |
| `GET` | `/api/v1/manager/tasks/suggestions?category=` | manager | §31 ranked candidates with reasons |
| `GET` `PATCH` | `/api/v1/manager/tasks/{id}` | manager | §29 detail with scores / assign, status, rating, rework, rules |
| `GET` | `/api/v1/manager/reports/employees.csv` | manager | per-employee totals |
| `GET` | `/api/v1/manager/reports/tasks.csv` | manager | per-task outcomes and scores |

**user** means a bearer access token; **device** means an `X-Device-Secret`
header. They are not interchangeable — see *Authentication* below.

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

## Authentication (Phase 13)

### Two token types, and why

| | Access token | Refresh token |
|---|---|---|
| Form | JWT, HS256 | 48 random bytes |
| Lifetime | 15 minutes | 30 days |
| Stored? | no | yes, **hashed** |
| Revocable? | no | yes |

A stateless JWT cannot be revoked — that is the point of it. Fifteen minutes is
therefore the longest a stolen access token stays useful. The long-lived half
lives in a table, so signing out, a password change, or detected theft can end it
immediately.

### The scope comes from the token, never from the request

This is what makes §51 hold. In Phase 12 `user_id` arrived as a query parameter.
Now `current_scope()` reads a **signed claim**:

```python
def current_scope(user: User = Depends(get_current_user)) -> Scope:
    if user.role == UserRole.MANAGER.value:
        return Scope.for_organization(user.organization_id)
    return Scope.for_user(user.id)
```

A caller may ask for whatever they like; the scope they get is the one their
credential asserts. Tenant isolation is not a filter someone remembered to write.

`tests/test_backend_isolation.py` proves this per route, and includes a test that
fails when a new organisation-scoped route appears with no cross-tenant coverage.

### Refresh rotation and reuse detection

Each refresh consumes the old token and issues a new one in the same **family**.
If an already-consumed token arrives again, either a client retried or a stolen
token is being replayed — and we cannot tell which, so we assume the worse and
revoke the whole family.

That trade is deliberate: an occasional forced re-login is a far smaller harm
than a silently persistent session hijack.

### Devices are not people

A device credential can **write its own events and read nothing**. A stolen agent
secret should let an attacker post nonsense telemetry, not browse an
organisation. The payload's `device_uuid` is checked against the authenticated
device, so a device also cannot file events in another device's name.

### Enrolment is three steps on purpose (§25)

1. A manager (or a student, for their own machine) creates a device and gets a
   **single-use enrolment token, valid one hour**.
2. It is typed into the agent on the machine being enrolled.
3. The agent exchanges it for a **long-lived device secret** stored locally.

The shortcut — handing out the long-lived secret directly — is the wrong shape.
The enrolment token travels through a chat message or an email, and those are not
confidential channels. So the thing that travels expires quickly and works once,
and the thing that lasts never leaves the machine it was issued to.

Every token and secret is stored as a SHA-256 hash. A leaked database yields no
usable credentials. Passwords use Argon2id instead, because they are human-chosen
and there *is* a dictionary to attack.

### Rate limiting

Failed logins are counted per source address (10 in 5 minutes) and per account
(20, looser so an attacker cannot lock a victim out of their own account). The
check runs **before** hashing: Argon2id costs 64 MB per attempt, so unthrottled
guessing is also a denial-of-service against a 16 GB machine.

The counter is an in-process dict. That is correct for one uvicorn worker and
wrong for a horizontally scaled deployment, where it must move to Redis. The
interface is narrow so that swap is one class.

### Configuration

```
JWT_SECRET_KEY=<a long random string>     # required in production
JWT_ACCESS_TOKEN_MINUTES=15
JWT_REFRESH_TOKEN_DAYS=30
```

With no key set, a random one is generated at start-up and a warning is logged;
tokens then do not survive a restart. There is deliberately **no default secret**
in source — a shipped default is exactly how real systems get compromised.

---

## Business mode

Every `/manager/*` route requires the MANAGER role at the router level and reads through
`BusinessRepository(db, organization_id)`, bound to the organisation in the caller's token. An id from
another organisation returns the same 404 as an id that does not exist. Details and formulas:
[business_mode.md](business_mode.md).

`SessionUpload.task_id` (optional) links an uploaded session to an assigned task. The server accepts the
link only for a task in the device's organisation that is assigned to the device's user; otherwise the
session is stored without it.

`EnrollDeviceRequest.consent_version` (optional) records a `privacy_consents` row at enrolment.

---

## Not yet done

**Password reset.** Needs email delivery, which nothing in the project has yet.

**httpOnly refresh-token cookie.** The portal keeps the refresh token in `localStorage`; moving it to a
`SameSite` cookie needs CSRF protection and is the next hardening step (see `frontend/src/api/client.ts`).
