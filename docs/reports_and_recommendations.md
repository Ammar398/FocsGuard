# Reports and Recommendations

Daily and weekly reports, rule-based suggestions, and CSV export.

Spec sections: **§9** (every recommendation explains why), **§42** (reports),
**§43** (export), **§8** (no medical or diagnostic language), **§61** (no
certainty from a single weak signal).

---

## §8 is enforced in code, not in a style guide

> *"The project should NOT diagnose ADHD or any medical condition."*

A note saying "be careful with wording" is not a control. Someone adds a rule six
months from now, writes what feels like a helpful sentence, and the system starts
telling a student they may have a disorder. The harm is specific: a person
changes how they see themselves on the say-so of a webcam and a threshold.

So the vocabulary is a list in `shared/focusguard_shared/recommendations/vocabulary.py`,
and **`Recommendation.__post_init__` refuses to construct** if the text trips it:

| Group | Examples | Why |
|---|---|---|
| Conditions | ADHD, autism, insomnia, depression | naming one is a diagnosis, even hedged |
| Clinical framing | symptom, disorder, deficit, impairment | pathologises ordinary behaviour |
| Treatment | therapy, medication, prescribe | not this tool's role |
| Trait claims | "you are easily distracted", "you may have" | a claim about a person, not a measurement |

**Still allowed:** observations and session statistics. "You looked away eleven
times" is a count. "Your score was lower after 10pm" is a comparison. The
boundary is *observations about sessions, never claims about the person.*

### The acceptance test proves "cannot", not "does not"

`tests/test_recommendations.py` runs **every rule against 2000 randomised
statistics** and asserts no forbidden term reaches the title, the detail, or any
piece of evidence. Spot-checking a few strings would prove nothing about inputs
nobody thought of.

A companion test asserts **every rule fires at least once** under fuzzing —
because a rule that never fires is a rule the language test never checked. That
guard has already earned its place: it caught a rule going unexercised when a
data structure changed.

---

## §9: the evidence is not a tooltip

Every recommendation carries the **numbers that fired it**, and the dataclass
refuses to be built without them:

```python
if not self.evidence:
    raise ValueError("Section 9 requires every suggestion to show what triggered it.")
```

The portal renders them inline, always. Hiding them behind a hover technically
satisfies "explain why" and practically does not, because almost nobody expands
it.

It also makes disagreement possible. "1h 12m on youtube.com" is checkable — a
student who knows those were lecture recordings can go and reclassify the domain.
Advice without its evidence is just an instruction.

---

## The rules

All pure functions over plain data, so they run identically against the backend
database and the agent's local store and cannot disagree.

| Rule | Fires when | Kind |
|---|---|---|
| `phone_in_reach` | ≥3 phone appearances/hour, across ≥40% of sessions | environment |
| `frequent_interruptions` | ≥15% of monitored time away from the desk | environment |
| `signs_of_tiredness` | ≥8% of time showing tiredness, across ≥40% of sessions | rest |
| `dominant_distraction` | one label ≥15% of monitored time, classified distracting | digital habits |
| `sessions_without_breaks` | long sessions never holding an 8-minute focus stretch | breaks |
| `better_time_of_day` | late sessions ≥8 points below earlier ones | schedule |
| `going_well` | weighted average ≥78 | encouragement |
| `not_enough_data` | fewer than 3 sessions | — |

### Every rule refuses to guess

Below **three sessions** nothing is offered but a plain statement that there is
not enough to go on. §61's restraint about single weak signals applies to advice
as much as to scoring: one bad Tuesday is not a pattern.

Two rules go further. `phone_in_reach` requires the habit to appear across ≥40%
of sessions, so forty phone glances in one afternoon is an afternoon, not a
habit. `better_time_of_day` requires **two sessions in each group** and an
**8-point gap**, so it never reports noise as insight.

### `going_well` is not decoration

A tool that only ever reports problems teaches the user that opening it feels
bad, and they stop. It gives its numbers like every other rule, so it is a
finding rather than flattery.

---

## A bug worth recording

The first version of `dominant_distraction` attributed distraction to whichever
label had the most time in sessions that contained *any* distracting time.

Run against real data, it confidently reported:

> **Most off-task time went to one place: Visual Studio Code**

The editor was open all day and dominated total time in every session. The actual
distracting span was a fifth of the size. The rule named the tool the person was
*working in*.

The fix: attribution follows the **classification**, not the clock.
`SessionFacts.distracting_seconds_by_label` carries the per-label breakdown of
distracting time specifically. The same data now correctly reports `youtube.com`,
at 1h 12m — exactly matching the total off-task time.

The randomised language fuzzing could never have caught this. It is a
*correctness* bug, not a *vocabulary* one, and only running the thing against
realistic data surfaced it.

---

## Reports (§42)

```
GET /api/v1/reports/summary?days=7&offset_minutes=300
```

One endpoint, not separate daily and weekly routes — they differ only in the
window, and two endpoints would be two places to fix a bug.

**Empty days are returned explicitly.** A chart that silently skips them draws a
flat line across a week off and makes it look like continuous work.

**Averages are weighted by time.** A flat mean would let a two-minute session
count as much as a two-hour one, which is how one short bad session drags a good
week down.

### Time zones

Everything is stored in UTC, and the browser sends `offset_minutes`. Without it,
"today" and "sessions after 10pm" land on the wrong day for anyone outside UTC —
which is almost everyone. The grouping happens in Python rather than SQL to keep
the query dialect-neutral between SQLite and PostgreSQL.

---

## Export (§43)

```
GET /api/v1/reports/export.csv?days=30
```

Framed as a right rather than a feature. A person being measured should be able
to take the measurements with them, check them, and keep them if they stop using
the tool.

* **One row per session**, not per event. A student wants something they can open
  in a spreadsheet, not forty thousand rows — and sessions are the level that
  survives the §45 retention prune, so an export of an old month is not silently
  empty.
* **UTF-8 with a BOM**, or Excel on Windows renders any non-ASCII session name as
  mojibake. Tested with an Urdu session name.
* **No URLs, no frames, no window titles.** Not filtered out — never recorded, so
  there is nothing to filter. A test asserts it on the file itself.

The portal fetches it as a blob rather than using a plain link, because an
`<a href>` sends no `Authorization` header and putting a token in a query string
would land it in server logs and browser history.

---

## Tenant isolation on aggregating routes (§51)

Aggregation is the **easiest place for isolation to fail**, because a missing
filter produces a plausible-looking number rather than an error. So
`AnalyticsRepository` takes a `Scope` its signatures require, exactly like the
telemetry repository, and `tests/test_backend_reports.py` proves per route that a
Company A manager's summary, recommendations and export contain nothing of
Company B's.

The Phase 13 guard test — which fails when a new organisation-scoped route
appears with no cross-tenant coverage — fired on these three routes the moment
they were added. That is the test working.

---

## Verified

Against a live backend, six sessions over a week:

| Check | Result |
|---|---|
| summary totals | 6 sessions, 7.4h monitored, 4.1h focused, avg 68.7 |
| empty days | 2 of 7 returned with zero, not skipped |
| recommendations | phone habit (51 appearances, 6.9/hr), youtube.com (1h 12m), short focus stretches |
| §8 check on the raw response | clean |
| CSV | 6 rows, BOM present, no URLs or frames |

---

## Not yet done

**A weekly email or scheduled digest.** Needs mail delivery, which nothing in the
project has yet.

**PDF export.** CSV first, as `ARCHITECTURE_PLAN.md` §12 said: a PDF layout engine
is a disproportionate time sink for a format a spreadsheet already opens.

**Recommendations from the agent with no backend.** The rules are pure and could
run against the local store today; only the wiring is missing.
