# The FocusGuard Helper (one-click connect and start)

A web page cannot run a program or switch on a webcam, and must not be able to. The helper is the
small piece on the computer that lets the portal do those things **after the person at the computer
says yes**.

```
Portal (browser)                FocusGuard Helper (127.0.0.1:48214)        Windows
─────────────────               ───────────────────────────────────        ───────
"Connect this computer"  ──►    POST /pair {token}             ──►  native dialog: full consent notice
                                                                         Yes ─► enrols, saves credential
"Start session"          ──►    POST /sessions/start {name}    ──►  native dialog: "webcam will be analysed"
                                                                         Yes ─► agent opens in its own window
"Stop session"           ──►    POST /sessions/stop                 agent closes cleanly and uploads
```

## Setting it up (once per computer)

```powershell
python -m focusguard_agent.helper --install-startup
```

That starts it now and every time you sign in to Windows (a one-line script in your Startup folder;
it shows in Task Manager → Startup apps). Remove it with `--remove-startup`. The helper itself has no
window, never touches the camera, and uses almost no CPU.

After that, everything happens in the portal:

| Who | Sees in the portal | Clicks | Approves on the computer |
|---|---|---|---|
| Student | *This computer* panel | **Connect this computer** | the consent notice |
| Student | session name and kind of work | **Start session** | "start monitoring?" |
| Employee | their assigned tasks, with each task's rules | **Start** | "start monitoring?" |
| Anyone | *Monitoring: …* | **Stop session** | — |

## Why it is safe to have a port a browser can reach

| Threat | Defence |
|---|---|
| Another computer on the network | Bound to `127.0.0.1` only |
| Some other website the user has open | `Origin` must be in `FOCUSGUARD_HELPER__ALLOWED_ORIGINS`; a page cannot forge `Origin` |
| DNS rebinding (`evil.example` re-pointed at 127.0.0.1) | `Host` header must be `127.0.0.1` or `localhost` on the helper's port |
| A compromised portal, or a manager, trying to enrol or record someone | Pairing and every session start need **Yes** in a native dialog on that computer; the default button is **No** |
| Command injection through a session name | The agent is launched with an argument list, never a shell string |
| Hidden monitoring | The agent always runs in its own visible window; the portal shows *Monitoring: …* |
| Two dialogs stacked so one is clicked by mistake | Only one question at a time |

Chrome's *Private Network Access* checks are answered (`Access-Control-Allow-Private-Network`), so a
portal served from a real domain can reach the helper once its origin is in the allow-list.

## Testing

`tests/test_helper.py` runs the real HTTP server with a scripted "Yes/No" in place of the dialog and a
recorder in place of the agent launcher. It proves foreign origins, missing origins and rebound hosts
are refused before any dialog appears; that "No" changes nothing; that a hostile session name is
passed as inert text; and that only one session can run.

## Without the helper

Everything still works from a terminal: `tools.enroll --token`, `agent --name …`,
`agent --assigned-task N`, `tools.stop`. The student dashboard keeps these under *Prefer the command
line?*, and managers can still create an enrolment token on an employee's page.
