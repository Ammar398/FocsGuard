# FYP Demonstration Script

Spec section **§53**. About 15 minutes. Practise once with the camera you will use on the day.

## Before the panel arrives (5 minutes)

```powershell
cd E:\focusguard
.venv\Scripts\activate
python scripts/seed_demo.py --reset          # fresh demo organisation, team, history, student
python -m focusguard_backend.main            # terminal 1
cd frontend; npm run dev                     # terminal 2  (or ..\.tools\node\npm.cmd run dev)
python -m focusguard_agent.hardware.detector # terminal 3 - leave the output on screen
```

The seed script's docstring lists the demo accounts; they share the password in `DEMO_PASSWORD`.

Install the helper once (`python -m focusguard_agent.helper --install-startup`), sign in as the
student, click **Connect this computer** and approve it. Load the browser extension, and open a YouTube
entertainment video and a GitHub tab.

## Part 1 — Hardware and privacy (1 minute)

Show the detector output: *no CUDA on this AMD card, DirectML selected, the leftover NVIDIA DLLs were
correctly ignored*. Say: frames never leave the machine; only events do.

## Part 2 — Student demo (6 minutes)

| Step | Do | Point out |
|---|---|---|
| 1 | Sign in as the student | Week of history, focus ring, recent sessions |
| 2 | `python -m focusguard_agent.tools.vision_test` | Live EAR, yaw/pitch/roll, gaze with confidence, FPS, CPU, provider |
| 3 | Dashboard → type "Study Data Structures", distracting sites `youtube.com` → **Start session** → **Yes** in the Windows dialog | Permission is asked on the computer; the agent window lists exactly what is monitored; the portal shows *Monitoring: …* |
| 4 | Look at the screen for 20 s, then away for ~6 s | A short glance is ignored (grace period); the long one becomes GAZE_AWAY and the score eases down, not crashes |
| 5 | Hold your phone up for ~10 s | One PHONE_VISIBLE event with a confidence, not one per frame |
| 6 | Switch to the YouTube tab for ~20 s | Distracting under *this task's* rule — YouTube is not bad in general |
| 7 | **Stop session** in the portal | The agent closes cleanly and the session uploads |
| 8 | Portal → the new session | Timeline with event bands explaining each dip; apps and domains with the rule that classified them |
| 9 | Reports → This week | Daily bars, recommendations **with their evidence**, *Download CSV*, *Print / Save as PDF* |

Say: *the wording is enforced by a test — no medical or diagnostic language can be generated.*

## Part 3 — Business demo (6 minutes)

| Step | Do | Point out |
|---|---|---|
| 1 | Sign in as the manager | Employees, working now, productive vs distracting split, top apps and domains, teams |
| 2 | Employees → Ayesha Khan | Work time, visual attention vs computer productivity, phone / looking-away time, consent record |
| 3 | Scroll to *Task-category history* | Comparative statement; score only after 3 completed tasks; the weights are printed |
| 4 | Tasks → New task, category **Backend** | Suggestions appear instantly, ranked, every reason shown; Usman listed as *not enough history* |
| 5 | Pick a candidate, add `netflix.com` as distracting, create | The manager decides; nothing is auto-assigned |
| 6 | Sign in as Ayesha (employee) → **Connect this computer** → Yes | Consent shown in Windows and recorded; her open tasks appear **with their rules** |
| 7 | **Start** next to the new task → Yes; work ~1 minute; **Stop session** | Task session; the task moves to *in progress* |
| 8 | Task page | Task focus vs task productivity, with the explanation of each number |
| 9 | *Complete and rate* | Completed, rated tasks are what feed strength analytics |

Steps 6–7 connect the demo laptop to Ayesha's account, replacing the student connection. Before
repeating the student demo, sign in as the student and click **Connect to my account instead**.

Highlight `Create Landing Page UI` in the seed data: focus 82 but productivity 60 — attention and
productivity are different things (§59).

## Questions the panel is likely to ask

| Question | Answer, and where it is shown |
|---|---|
| *What if the camera disconnects?* | CAMERA_UNAVAILABLE, scoring pauses, no FACE_ABSENT penalty — `docs/computer_vision.md` |
| *Two monitors?* | `MONITOR_PROFILE=DUAL/MULTI` scales gaze-away penalties; productivity is measured separately |
| *How accurate is gaze?* | Approximate, reported with a confidence; never a lone basis for "distracted" (§61) |
| *Can a manager see my screen?* | No — there is no field for it anywhere; `tests/test_privacy_imports.py` |
| *Can Company A see Company B?* | No — organisation from the signed token; every route probed cross-tenant in tests |
| *Isn't ranking employees unfair?* | Completed-task outcomes weigh 60%; minimum sample; comparative wording; manager decides |
| *Does it need a GPU?* | No — CPU fallback; DirectML used when available |
| *Storage?* | No video or screenshots; raw events pruned after 30 days, totals kept |
