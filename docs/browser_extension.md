# Browser Extension

How FocusGuard knows you were on `github.com` rather than just "Chrome", and why
the design keeps your search terms out of it entirely.

---

## Installing it

```bash
python browser_extension/build.py
python -m focusguard_agent.tools.show_token
```

| Browser | Steps |
|---|---|
| **Chrome** | `chrome://extensions` → Developer mode → Load unpacked → `browser_extension/dist/chrome` |
| **Edge** | `edge://extensions` → Developer mode → Load unpacked → `browser_extension/dist/edge` |

Then open the FocusGuard popup and paste the pairing token.

### One codebase, not two

The original plan called for separate `chrome/` and `edge/` folders. Edge is
Chromium-based and loads the **same** Manifest V3 extension with the same APIs, so
two folders would mean applying every fix twice and watching them drift — a bug
source and a §56 "copy-pasted logic" violation. One source tree, one build script,
two output folders.

---

## What is sent

```
https://www.youtube.com/watch?v=ABC123&list=PRIVATE#t=42
        ↓
{ "domain": "youtube.com", "path_prefix": "/watch" }
```

| Sent | Never sent |
|---|---|
| Domain of the **active tab** | Full URLs |
| One coarse path segment | Query strings, search terms |
| Whether the browser is focused | Page contents, form data, cookies |
| | Background or inactive tabs |
| | Browsing history |

Only the active tab of the **focused window** is reported. A video playing in a
background window is not what the user is doing.

Browser-internal pages (`chrome://`, `edge://`, `file://`, `about:`) are never
tracked — a user's local file paths are not our business.

---

## Why minimisation happens in the extension

It would be simpler to send the URL and strip it agent-side. That would be wrong.

"The agent strips it" still means the full URL **crossed a process boundary**,
existed in another program's memory, and could reach its logs or a crash dump.
Minimising in the extension means the query string never leaves the browser at
all.

Query strings routinely carry search terms, document IDs, email addresses and
session tokens. A manager does not need any of that to see that 45 minutes went
to YouTube (§20, §35).

### Two implementations, one rule

Because minimisation must happen in JavaScript *and* the agent must validate what
arrives, the rule exists twice — exactly the situation that silently drifts apart.

[`test_extension_parity.py`](../tests/test_extension_parity.py) runs the
JavaScript against the **same 14 cases** as the Python suite on every test run.
Verified: 14/14 agree, and no query string survives.

The agent also independently **rejects** anything that still looks like a full
URL, so a regression in the extension fails loudly rather than leaking quietly.

---

## Security of the local endpoint

Opening a port on a user's machine deserves care.

| Control | Why |
|---|---|
| Bound to `127.0.0.1` only | Nothing on the network can reach it. Binding elsewhere raises `ValueError`. |
| **Shared token required** | Without it, any web page running JavaScript could POST fabricated activity and distort someone's productivity record. |
| `secrets.compare_digest` | A wrong token takes the same time to reject as a right one. |
| 8 KB body cap | A domain and a timestamp need a few hundred bytes. |
| Three endpoints, nothing else | No filesystem, no shell, no other routes. |

`/status` is deliberately unauthenticated and contentless — it exists so the
popup can show whether the agent is running, and reveals nothing about the user.

The token lives at `%LOCALAPPDATA%\FocusGuard\ipc_token.txt`. Treat it like a
password.

---

## The visible indicator (§34, §62)

Monitoring must never be hidden. The popup always shows:

- whether the agent is **connected**
- whether reporting is **active or paused**
- **when** the last report was sent
- a plain-language list of what is and is not collected
- a **pause button**, right there — no hunting through settings

The dot is green **only** when data is genuinely flowing. Paused, no token, or
agent offline all look visibly different from normal operation.

---

## Task-aware productivity (§22)

The whole point of knowing the domain:

```
task rule  →  task category affinity  →  user override  →  category default  →  unclassified
```

**YouTube during "Watch Python Lecture" is productive. YouTube during "Implement
Authentication" is not.** Same site, different verdict, because the task differs.

**Category affinity** means a manager need not enumerate every tool: saying a task
is `BACKEND` makes IDEs, terminals and documentation productive automatically.
Design tools are not — the affinity discriminates rather than blessing everything.

### What is deliberately not inferred

An unlisted `CONTEXT_DEPENDENT` site — YouTube, Google, an AI chat — stays
**NEUTRAL**. FocusGuard does not guess whether a YouTube page is a lecture or
entertainment: the domain cannot tell us and the title is not collected by
default.

That is a real limitation. The honest answer to it is the recommendation engine
(Phase 15): *"18 minutes went to youtube.com — if that was entertainment, consider
adding it to your distraction list."* The user decides; the system does not
pretend to know.

---

## Degrading without the extension

If it is not installed, disabled, or Chrome's service worker is asleep, **the
agent carries on**. The browser is still tracked as an application; it just loses
domain detail. Resolution is lost, not function.

Reports also **expire after 30 seconds**. Closing the browser sends no goodbye
message, so without expiry the last domain would appear open for the rest of the
day. A domain is also only applied while a browser is actually in the foreground —
a YouTube tab behind Photoshop is not what the user is doing.
