# Business Mode

Spec sections **§24–§31**, **§62**. The manager's question (§69): *how was work time used, how did
employees perform on assigned task categories, and who has historically performed strongest for
similar work?*

---

## The flow

```
Manager registers an organisation  (POST /auth/register-organization)
  → departments (four starter ones), employees, projects
  → creates a device enrolment token for an employee        (single use, 1 hour)
Employee runs   tools.enroll --token …                       consent shown → recorded
  → manager creates a task: category, priority, deadline, estimate,
    productive / neutral / distracting apps and sites, assignee (with suggestions)
Employee runs   tools.tasks                                  sees the task AND its rules
                agent --assigned-task 12                     a TASK SESSION opens
  → the agent classifies activity under the task's rules, scores attention, uploads
  → task moves TODO → IN_PROGRESS on first upload
Manager sees    task focus, task productivity, events, apps, domains
Manager marks   COMPLETED with a 1–5 quality rating and rework count
  → after ≥3 completed tasks in a category, the employee's history in it gets a score
  → the next task in that category shows ranked suggestions, each with reasons
```

## Accounts and roles

| Role | Signs in to | Sees |
|---|---|---|
| Manager | Manager portal | Their organisation only |
| Employee with a password | Student-style portal | Their **own** sessions and reports |
| Employee without a password | Nothing (`!no-login` sentinel hash) | — the account exists for the agent |
| Device | — | Writes its own events; reads only its user's open tasks |

Organisation sign-up is safe where manager self-registration is not: it creates a *new, empty* tenant.
Nobody can attach themselves to an existing organisation.

## Task sessions and task scores (§29)

A task session is a focus session with `task_id`. The server only accepts the link if the task belongs
to the device's organisation **and** is assigned to the device's user; otherwise the session is kept
without the link (refusing would jam the agent's upload queue over one field).

| Score | Formula | Source |
|---|---|---|
| Task focus | duration-weighted mean of the sessions' combined focus | `shared/business/task_scoring.py` |
| Task productivity | `100 × (productive + 0.5 × neutral) / classified time` | same formula as the live DPS |
| Duration ratio | monitored minutes ÷ expected minutes | |

No sessions → `None`, displayed as "—", never 0. Every task carries a generated explanation listing the
minutes behind each number.

## Strength analytics (§30)

Inputs are **completed tasks only**. Each becomes six 0–100 components:

| Component | From | Weight |
|---|---|---|
| quality | manager rating ÷ 5 × 100 | 30% |
| on_time | share of deadlines met | 15% |
| estimate | 100 at/under estimate, −1 per % over, 0 at 2× | 10% |
| rework | 100 − 25 per rework round | 5% |
| focus | task focus score | 20% |
| productivity | task productivity score | 20% |

Outcome measures carry 60%; monitoring 40%. Missing components are dropped and the weights
renormalised — a category with no deadlines is not scored as "0% on time".

**Safeguards**

* Fewer than `MIN_TASKS_FOR_STRENGTH_CLAIM = 3` completed tasks in a category → *insufficient data*, no
  score, never ranked.
* Statements are comparative and attributed to data: *"Historical task data suggests Ayesha has
  performed more strongly on backend assignments (92 across 3 tasks) than on database assignments
  (87 across 4 tasks)."* Within 5 points, the categories are called *similar*.
* `assert_comparative_language` rejects "bad", "weak", "poor", "lazy", "not suited", clinical terms and
  more. `tests/test_business_logic.py` fuzzes 1,500 random histories to show no generated sentence ever
  contains them. The employee's name is substituted *after* the check, so a surname like "Bad" cannot
  crash a profile.

## Assignee suggestions (§31)

Choosing a category on the new-task form calls `GET /manager/tasks/suggestions?category=…`. Candidates
with enough history are ranked by historical score (ties: more evidence, then lighter current
workload) and every one lists its reasons. Others appear under *not enough history*. Clicking a
candidate only fills the assignee box; the manager submits.

## Manager dashboard (§26) and employee page (§27)

* **Working / offline** — a device seen within 2 minutes. The agent heartbeats after each upload pass.
* Productive / neutral / distracting split, average focus, visual attention, digital productivity.
* Top applications and domains, coloured by the classification that accounted for most of their time.
* Per-department totals; per-day focus bars.
* Employee page: phone, looking-away, away-from-desk time and tiredness events; devices; consent
  records; tasks with scores; task-category history; recent sessions (click through to the full
  timeline).

## Reports (§52)

| Report | Where |
|---|---|
| Employee productivity / team weekly (CSV) | Dashboard → *Export CSV* (`/manager/reports/employees.csv`) |
| Task report (CSV) | Tasks → *Export CSV* (`/manager/reports/tasks.csv`) |
| Any page as PDF | *Print / Save as PDF* — a print stylesheet hides controls and forces light colours |

## What a manager cannot see

Window titles, URLs beyond the domain, page contents, screenshots, webcam images, keystrokes, the
clipboard. None of these exist anywhere in the system — see [privacy.md](privacy.md).

## Files

```
shared/focusguard_shared/business/        task_scoring.py · strengths.py · vocabulary.py
backend/focusguard_backend/models/business.py
backend/focusguard_backend/repositories/business.py
backend/focusguard_backend/api/v1/manager.py · organizations.py · business_schemas.py
desktop_agent/focusguard_agent/tools/tasks.py ; agent.py --assigned-task
frontend/src/pages/manager/               dashboard, employees, employee detail, tasks, task detail, team
tests/test_business_logic.py · test_backend_business.py · test_agent_tasks.py
```
