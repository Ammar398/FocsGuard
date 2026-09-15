# Student Portal

The screens a student actually uses. Spec sections: **§41** (understand why the
score changed), **§8** (no medical or diagnostic language), **§61** (never claim
certainty from one weak signal).

Stack: React 19, TypeScript, Vite 8, Tailwind 4, Recharts 3 — the stack chosen in
`ARCHITECTURE_PLAN.md` §3, Option A.

---

## The three screens

| Screen | Answers |
|---|---|
| **Login** | sign in, or create a student account |
| **Dashboard** | how is this session going, and what came before it |
| **Session** | *why* did the score move, and where did the time go |

---

## §41 is the whole point of the timeline

A bare line chart answers *what* happened and leaves *why* a mystery. So every
attention event is drawn as a shaded band across the moment it covered, and the
dip in the line sits **inside the band that explains it**.

```
 100 ┤     ╭──╮        ╭───╮
     │  ╭──╯  ╰─╮   ╭──╯   ╰──╮
  50 ┤──╯       ╰───╯          ╰──╮        ╭────
     │        ▓▓▓▓▓▓            ░░░░░░░░░░░╯
   0 ┤        phone              away from desk
     └────────────────────────────────────────
```

That pairing is the feature. Without the overlay it is a pretty graph; with it,
it is an explanation a student can act on.

The legend lists **only the event types actually present**. A fixed legend showing
seven types when the session had one is noise, and it invites a student to wonder
what they did wrong to trigger the other six.

---

## Language: observations, never diagnoses

Section 8 is explicit that this project diagnoses nothing. The vocabulary is
enforced in one place, `src/api/types.ts`:

| Event | Shown as |
|---|---|
| `FACE_ABSENT` | Away from the desk |
| `EYES_CLOSED` | Eyes closed |
| `DROWSINESS` | Signs of tiredness |
| `GAZE_AWAY` | Looking away |
| `PHONE_VISIBLE` | Phone in view |

Every one of those states what the camera **saw**. None names a condition, and
none is a claim about the person.

The same rule governs the score description: "Frequently interrupted" describes
**a session**; "you are easily distracted" would be a claim about someone's
character that this project has no business making.

### Confidence is shown, because §61 says certainty must be earned

An event row reads *clear signal*, *probable*, or *weak signal* depending on the
detector's confidence. A 0.55 and a 0.95 must not look identical to the reader —
that is precisely the "one weak signal" section 61 forbids treating as certain.

---

## What the dashboard shows first

If a session is running, that one — ring, timers, and a LIVE marker. Otherwise
the last one that finished. A dashboard that opens on a list makes the student do
a lookup before learning anything.

A **running** session shows no totals, deliberately. The agent computes them when
the session ends, because a number that is wrong now and corrected later is worse
than no number at all.

### There is no "Start session" button

A web page cannot start a process on your machine, and a button that quietly does
nothing is worse than no button. The dashboard shows the commands instead.

That is also the right boundary for a monitoring tool. Monitoring is started by
the person being monitored, on their own machine, deliberately. A dashboard that
could switch someone's webcam on remotely is a different product, and not one
this specification asks for.

---

## "Not scored" is shown, not hidden

The session page has a fourth stat beside focused and interrupted: time the
camera was unavailable or monitoring was paused.

Folding that into "focused" would quietly inflate the number a student is judged
by. Dropping it entirely would leave the three figures not adding up. So it is
named, with its reason.

---

## Classification is shown, not asserted

Each row in *Where the time went* carries the rule that classified it — your task
settings, your own rule, the default catalogue.

"YouTube was distracting" is a judgement a student is entitled to disagree with,
and YouTube genuinely **is** work for some tasks (§21). Showing the source turns
an accusation into a statement about a rule the user can change.

Domains only. Never a full address, never a page title, never a search term — the
extension minimises a URL before it leaves the browser (§20, §35), so nothing
downstream ever had more than that.

---

## Two implementation decisions worth defending

### Refreshing is single-flight, and it is required, not an optimisation

Refresh tokens rotate, and a token used twice is treated as **stolen**: the whole
family is revoked (see `api/v1/auth.py`). If the dashboard fires three requests,
all three get a 401, and all three refresh with the same stored token, the second
and third look exactly like a replay — and the backend would correctly sign the
user out.

So a refresh in progress is shared: the first caller performs it, everyone else
waits on the same promise.

**Our own security design creates this bug if the client is naive about it.**

### Polling, not a WebSocket

The agent uploads in batches roughly every thirty seconds. A WebSocket would
deliver that data no sooner — it would hold a connection open waiting for it. The
latency floor is set by the **agent's upload interval**, not by the transport.

A socket would add a connection lifecycle, reconnection logic and a second server
code path to gain nothing a student could perceive. `lib/usePolling.ts` is the
single place the dashboard subscribes to data, so replacing it later is contained.

Polling **stops while the tab is hidden**, and refreshes immediately when it comes
back. A dashboard left open for eight hours would otherwise make a thousand
pointless requests on a laptop the user is trying to work on.

---

## Where the tokens live, and the honest trade

| | Stored where | Survives reload | Readable by injected script |
|---|---|---|---|
| access token (15 min) | memory only | no | no |
| refresh token (30 days) | `localStorage` | yes | **yes** |

That second row is a real weakness, written down rather than glossed over. The
better design is an httpOnly, SameSite cookie, which script cannot read at all.
It is not done here because the backend returns a JSON token pair, and moving to
cookies also means adding CSRF protection — a change to the Phase 13 contract
rather than a frontend detail. It belongs in the Phase 23 hardening pass.

---

## Accessibility

* The focus ring is `role="img"` with a label stating the number and the plain
  description, because a shape and a colour read as nothing to a screen reader.
* **Colour is never the only carrier of meaning.** The score is always printed in
  the middle of the ring and beside every session row. This matters for the
  roughly one man in twelve with a red-green colour deficiency, for whom the
  "good" and "bad" ends of this palette are the hardest pair to tell apart.
* Both light and dark themes are defined; the viewer's system setting decides.

---

## Bundle

```
index.js         275 kB   87 kB gzipped    login + dashboard
SessionPage.js   363 kB  106 kB gzipped    loaded on demand
```

Recharts is most of the weight and only the session page uses it, so that route
is lazy-loaded. Someone signing in should not download a charting library before
choosing a session to look at.

---

## Verified

Against a live backend, through the dev server proxy on one origin:

| Step | Result |
|---|---|
| `npm run build` | type-checks and bundles clean, no warnings |
| `npm run lint` | clean |
| register → login → `/auth/me` | 201, token pair, correct user |
| a 52-minute session with 6 events, 6 apps, 1040 score samples | rendered end to end |
| dashboard totals | 52 min, 45.5 min focused (87%), 6.5 min interrupted, avg 79.9 |

---

## Not yet done

**Reports and recommendations** — Phase 15. Daily and weekly views, and the
rule-based recommendations with their evidence attached.

**httpOnly cookie auth** — Phase 23, as above.

**A live score stream** — only worth adding if the agent gains a push channel;
until then it would not reduce latency.
