# Privacy

Spec sections **§34, §35, §62**. FocusGuard is built so that invasive monitoring is *impossible*, not
merely switched off.

## What is collected

| Data | Example | Where it lives |
|---|---|---|
| Session name, start, end, totals | "Study Data Structures", 50 min, focus 82 | Agent store; server if enrolled |
| Attention events | `PHONE_VISIBLE` 10:22:15–10:22:47, confidence 0.87 | Agent store; server if enrolled |
| Application spans | Visual Studio Code, 25 min, PRODUCTIVE | Agent store; server if enrolled |
| Website **domain** and coarse path | `youtube.com`, `/watch` | Agent store; server if enrolled |
| Focus score samples (~1 per second) | visual 85, productivity 70, combined 79 | Agent store; server if enrolled |
| Calibration (EAR baseline) | 0.27 | Agent machine only |
| Business: tasks, ratings, consents | "Build REST API", 4.5/5 | Server |

## What is never collected

Webcam images or video · screenshots or screen recordings · window **contents** · keystrokes · the
clipboard · passwords · messages · audio · full URLs, query strings, search terms · files.

Window titles are **off by default** (`FOCUSGUARD_ACTIVITY__COLLECT_WINDOW_TITLES`), and even when a
student turns them on locally, no manager-facing response includes them.

## How that is enforced, not just promised

| Control | Where |
|---|---|
| The agent imports no keyboard, clipboard, screen-capture or audio library; a test parses every source file and fails if one appears | `tests/test_privacy_imports.py` |
| URLs are minimised **inside the browser** before they reach the agent | `browser_extension/src/background.js`, `tests/test_urls.py` |
| The upload schema forbids unknown fields and has no field for a frame, screenshot or URL | `api/v1/schemas.py` (`extra="forbid"`) |
| Frames are processed in memory and dropped; nothing is written unless the user opens the debug preview | `capture/camera.py` |
| Logs redact tokens and secrets | `tests/test_logging_redaction.py` |
| Medical/diagnostic and judgemental language cannot be generated | `tests/test_recommendations.py`, `tests/test_business_logic.py` |

## Transparency and consent

* **Students** run the agent themselves and stop it with Ctrl+C or `tools.stop`. It prints a banner
  showing exactly which monitors are on.
* **Employees** see the full collected / not-collected list during enrolment and must type *yes*.
  The acceptance is stored as a `privacy_consents` row with the notice version
  (`CONSENT_VERSION` in `tools/enroll.py`) and shown on their page in the manager portal.
* Before starting a task, `tools.tasks` shows the employee the task's productive / neutral /
  distracting rules — they know what will count before it counts.
* Employees given a portal password can see their own sessions and reports.
* Pausing: the agent only monitors while it runs. Organisations decide their own policy on when it
  must run; FocusGuard does not run hidden or start itself.

## Tenant isolation

A manager can read only their own organisation. The organisation comes from the signed token, every
query filters on it, and cross-tenant tests cover every route
(`tests/test_backend_isolation.py`, `tests/test_backend_business.py`).

## Retention (§45)

| Data | Agent | Server |
|---|---|---|
| Raw events, activity spans, score samples | 30 days, then rolled into daily summaries | 30 days, then deleted after totals are rolled onto the session (`python -m focusguard_backend.tasks.retention`) |
| Sessions and their totals | Kept | Kept |
| Tasks, ratings, consents | — | Kept |

Both windows are configurable (`FOCUSGUARD_PRIVACY__RAW_EVENT_RETENTION_DAYS`; `--days` on the server
job). Schedule the server job daily with Windows Task Scheduler or cron.

## Deleting data

* A student can export everything (Reports → *Download CSV*) and delete their local store
  (`%LOCALAPPDATA%\FocusGuard`, or `FOCUSGUARD_DATA_DIR`).
* Revoking a device (`DELETE /devices/{id}`) destroys its credential immediately; its uploaded history
  is kept as a record, and the employee's account can be deactivated.
