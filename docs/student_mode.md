# Student Mode

Spec sections **§7–§9, §40–§43**. The student's question: *how focused was I, what distracted me, and
how can I improve?*

## The workflow

```
Once per computer:  python -m focusguard_agent.helper --install-startup   (see helper.md)
Register in the portal (or sign in)
  → This computer → "Connect this computer"  → approve the consent notice in Windows
  → (optional, once) python -m focusguard_agent.tools.calibrate
  → Type what you are working on, pick the kind of work → "Start session" → approve in Windows
  → Work. A FocusGuard window shows what is being monitored; the portal shows "Monitoring: …"
  → "Stop session" in the portal (or Ctrl+C in the FocusGuard window)
  → The session uploads and appears in the portal within seconds
  → Portal: session timeline (why the score moved), reports, recommendations, CSV / PDF
```

The portal never starts the agent by itself: it asks the FocusGuard Helper, and the helper asks the
person at the computer. A dashboard that could switch on someone's webcam without them would be a
different, worse product. For per-site rules (`--productive youtube.com --distracting instagram.com`)
the command line remains available.

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
