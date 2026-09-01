# Screen and Activity Monitoring

How FocusGuard knows *what* you were doing, and — just as importantly — what it
deliberately cannot know.

---

## What is collected

| Collected | Example |
|---|---|
| Foreground process name | `Code.exe` |
| Normalised application name | `Visual Studio Code` |
| Category | `DEVELOPMENT` |
| Duration | 3h 10m |
| Idle time | "away from the desk since 14:20" |
| Window title | **opt-in, off by default** |
| Domain (Phase 10) | `github.com` |

## What is **not** collected

Window contents, keystrokes, clipboard, screenshots, screen recordings, file
contents, messages, passwords.

This is not a policy claim. The Windows calls the agent uses —
`GetForegroundWindow`, `GetWindowThreadProcessId`, `GetWindowTextW`,
`GetLastInputInfo` — **cannot return those things**, and
[`tests/test_privacy_imports.py`](../tests/test_privacy_imports.py) fails the
build if any library capable of them is ever imported into the agent.

### On `GetLastInputInfo`

The one call that might look like input monitoring. It returns **a single
integer**: the system tick count at which input last occurred. No key, no button,
no coordinate, no content — only *how long since something happened*.

Without it, walking away from the desk with VS Code open would be recorded as
three hours of development.

### Why window titles are opt-in

An application name says "Excel". A title says
`salary_review_2026.xlsx - Excel`, or `Re: your diagnosis - Outlook`. Section 35
wants durations and categories, not documents, so `collect_window_titles`
defaults to `False`. When enabled, titles are truncated to 120 characters and the
logging redactor strips them from logs.

---

## The pipeline (spec §58)

```
Code.exe                raw process name
  -> Visual Studio Code  ActivityNormalizer   what a person calls it
  -> DEVELOPMENT         ActivityClassifier   what it IS
  -> PRODUCTIVE          TaskContextEngine    what it is WORTH   (Phase 10)
  -> 3h 10m              ActivityAggregator   how long
```

### Fact versus judgement

A **category** is task-independent: Photoshop is `DESIGN` whoever runs it.

A **classification** is a judgement: Photoshop is productive for *"Design the
landing page"* and a distraction during *"Fix the login bug"*.

Keeping them apart is what makes §22 work. Precedence:

```
task rule  >  user/org override  >  category default  >  unclassified
```

Every record stores its `rule_source`, so the UI can answer *"why was this called
distracting?"* without re-running a classifier whose rules may since have changed
(§9, §41).

### YouTube is `CONTEXT_DEPENDENT`, never `ENTERTAINMENT`

Spec §22 is explicit that YouTube must not be assumed unproductive — it hosts
lectures and entertainment alike. It, Google and the AI chat sites map to
`CONTEXT_DEPENDENT`, which defaults to **NEUTRAL** until a task says otherwise.

### Unknown means neutral, never distracting

An application nobody catalogued is `OTHER` / `UNCLASSIFIED` / **NEUTRAL**.
Guessing that unfamiliar software is a distraction would penalise users for niche
tools — a designer's plugin, a university's exam client.

---

## Aggregation

**Spans, not samples.** Polling once a second for eight hours is 28,800 rows,
nearly all identical. Consecutive samples of one application collapse into a
single span with a start, an end and a duration (§45).

**Short spans are discarded.** Alt-tabbing through six windows produces six spans
under a second. None is usage, and keeping them would bury real entries and make
"most-used application" meaningless. Default minimum is 3 seconds.

**Idle time is not usage.** Past the idle threshold (default 120 s) the current
span closes and none opens until input resumes.

**Idle is measured between idle samples only.** The first implementation measured
from the previous sample of any kind, which charged the active seconds *before*
the user stopped as idle time — caught by a test, fixed, and pinned.

**Transient shell windows are ignored.** `SearchHost.exe` and friends hold the
foreground for a moment during a window switch and are not usage of anything.

---

## Trying it

```bash
python -m focusguard_agent.tools.activity_test --seconds 60
python -m focusguard_agent.tools.activity_test --titles    # opt in
```

Verified on the development machine: correctly identified Visual Studio Code as
`DEVELOPMENT` / `PRODUCTIVE` with an accurate duration.

---

## Still to come

- **Phase 10** — the browser extension, so `chrome.exe` becomes `github.com`, and
  the task-context engine that turns categories into task-aware verdicts.
- **Phase 11** — persistence, with the retention rules from §45.
