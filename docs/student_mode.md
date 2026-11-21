# Student Mode

Spec sections **§7–§9, §40–§43**. The student's question: *how focused was I, what distracted me, and
how can I improve?*

## The workflow

```
Register in the portal (or sign in)
  → Connect a computer: the dashboard issues a one-time token
      python -m focusguard_agent.tools.enroll --token …        (optional - works offline without it)
  → Calibrate once
      python -m focusguard_agent.tools.calibrate
  → Start a session, naming it and optionally its task and rules
      python -m focusguard_agent.agent --name "Study Data Structures" --task "Trees" \
          --task-category STUDY --productive youtube.com docs.python.org --distracting instagram.com
  → Work. The agent prints what is being monitored and a live status line.
  → Stop with Ctrl+C or  python -m focusguard_agent.tools.stop
  → A summary prints immediately; the session uploads and appears in the portal
  → Portal: session timeline (why the score moved), reports, recommendations, CSV / PDF
```

A web page cannot start the agent, deliberately: a dashboard that could switch on someone's webcam
remotely would be a different, worse product.

## What a session measures

| Measure | Source |
|---|---|
| Focus score (combined, visual attention, digital productivity) | `scoring/engine.py`, `scoring/productivity.py` |
| Focused / distracted time, longest unbroken focus block | scoring engine |
| Phone in view, eyes closed, tiredness, looking away, away from desk | event engines |
| Current, most-used application and domain; productive / neutral / distracting time | activity monitor + extension |

YouTube is *context dependent*: neutral by default, productive when the task lists it (a lecture),
distracting when the task says so.

## Recommendations

Rule-based and explainable (§9). Each one carries the evidence that triggered it, and a test fuzzes
every rule across 2,000 random inputs to prove no medical or diagnostic wording can appear (§8).
Examples the rules produce: phone out of reach, shorter focus blocks, a named distraction to block,
time of day that suits you. Details: [reports_and_recommendations.md](reports_and_recommendations.md).

## Related docs

[student_portal.md](student_portal.md) · [focus_scoring.md](focus_scoring.md) ·
[computer_vision.md](computer_vision.md) · [screen_monitoring.md](screen_monitoring.md) ·
[browser_extension.md](browser_extension.md) · [privacy.md](privacy.md)
