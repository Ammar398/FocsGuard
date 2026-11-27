FocusGuard for Windows
======================

1. Extract this zip (right-click > Extract All).
2. Double-click "Install FocusGuard.cmd" in the extracted folder.
3. Wait until it says "FocusGuard is ready" (the first time takes 5-10 minutes:
   it downloads about 400 MB of Python packages).
4. Go back to the FocusGuard website and click "Connect this computer".
5. Add the browser extension: the website's "Browser extension" step opens
   the folder and your extensions page for you (Developer mode > Load
   unpacked > paste the folder path). Required for employees, recommended
   for students. It reports only the website's domain, never the full address.

What it installs, all for your Windows user only (no administrator needed):

  * Python 3.11, through winget, only if it is not installed already
  * FocusGuard, in %LOCALAPPDATA%\FocusGuard\app
  * the FocusGuard Helper, started now and whenever you sign in to Windows
    (Task Manager > Startup apps shows it; you can turn it off there)

Your camera is never turned on without your approval in a Windows dialog, and
video never leaves this computer - only scores and timings are uploaded.

To remove FocusGuard: delete %LOCALAPPDATA%\FocusGuard and the
"FocusGuard Helper" entry in your Startup folder.

The phone detector uses YOLOv8n by Ultralytics (AGPL-3.0).
