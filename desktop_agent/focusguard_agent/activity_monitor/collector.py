"""Foreground application monitoring (spec sections 19, 34, 35).

WHAT THIS COLLECTS, AND WHAT IT CANNOT
--------------------------------------
Three things, once a second:

* the **process name** of the foreground window (`Code.exe`);
* optionally its **window title**, off by default;
* whether the user has been **idle**.

That is the whole surface. It reads no window *contents*, no keystrokes, no
clipboard, no screen pixels. The Windows APIs used cannot return those things
even if asked - which is a stronger guarantee than a policy, and is why
`tests/test_privacy_imports.py` fails the build if a library capable of them is
ever imported into the agent.

ON IDLE DETECTION
-----------------
`GetLastInputInfo` is the one call that might look like input monitoring. It is
not. It returns a **single integer**: the system tick count at which input last
occurred. No key, no button, no coordinate, no content - only "how long since
something happened". It is the standard, documented way to detect an idle
session, and without it time spent away from the desk would be recorded as time
spent using whatever application happened to be in front.

WINDOW TITLES ARE OPT-IN
------------------------
A title can leak more than an application name: "salary_review_2026.xlsx -
Excel", "Re: your diagnosis - Outlook". Section 35 wants durations and
categories, not documents, so `collect_window_titles` defaults to False. When it
is on, titles are truncated and never logged (the logging redactor also strips
them).

WHY ctypes RATHER THAN pywin32
------------------------------
Consistency with `hardware/dxgi.py` and `capture/devices.py`, no extra
dependency, and the call surface stays visible in this file: a reader can see
exactly which four Windows functions the agent uses.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

from focusguard_shared.utilities import get_logger

logger = get_logger("activity.collector")

#: Titles longer than this are truncated. Long titles are usually full file
#: paths, which are exactly what should not be stored.
_MAX_TITLE_LENGTH = 120

#: Windows shell processes that hold the foreground briefly during a switch.
#: Recording them would fill the timeline with meaningless one-second rows.
_TRANSIENT_PROCESSES: frozenset[str] = frozenset(
    {
        "searchhost.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "textinputhost.exe",
        "applicationframehost.exe",
    }
)


@dataclass(frozen=True)
class ForegroundWindow:
    """What is in front right now."""

    process_name: str
    window_title: str | None = None
    pid: int = 0

    @property
    def is_transient(self) -> bool:
        """True for shell surfaces that only appear mid-switch."""
        return self.process_name.lower() in _TRANSIENT_PROCESSES


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class WindowsActivityCollector:
    """Reads the foreground window and idle time. Windows only."""

    def __init__(self, collect_window_titles: bool = False) -> None:
        self.collect_window_titles = collect_window_titles
        self._available = sys.platform == "win32"

        if not self._available:
            logger.info("Foreground monitoring is unavailable off Windows")
            return

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self._user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]

    @property
    def available(self) -> bool:
        return self._available

    # -- foreground --------------------------------------------------------

    def foreground(self) -> ForegroundWindow | None:
        """Return the foreground window, or None if there is none to report.

        None is normal: during a window switch, on the lock screen, or when the
        desktop has focus.
        """
        if not self._available:
            return None

        try:
            hwnd = self._user32.GetForegroundWindow()
            if not hwnd:
                return None

            pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return None

            process_name = self._process_name(pid.value)
            if process_name is None:
                return None

            title = self._window_title(hwnd) if self.collect_window_titles else None
            return ForegroundWindow(
                process_name=process_name, window_title=title, pid=pid.value
            )
        except Exception:  # noqa: BLE001 - monitoring must never break the agent
            logger.debug("Foreground window query failed", exc_info=True)
            return None

    def _window_title(self, hwnd: int) -> str | None:
        length = self._user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None

        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        return title[:_MAX_TITLE_LENGTH] if title else None

    @staticmethod
    def _process_name(pid: int) -> str | None:
        try:
            import psutil

            return psutil.Process(pid).name()
        except Exception:  # noqa: BLE001 - the process may have exited already
            return None

    # -- idle --------------------------------------------------------------

    def idle_seconds(self) -> float:
        """Seconds since the last keyboard or mouse input.

        Reads only a tick count - `GetLastInputInfo` returns no key, button,
        coordinate or content. Returns 0.0 where idle time cannot be determined,
        which errs towards treating the user as present.
        """
        if not self._available:
            return 0.0

        try:
            info = _LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
            if not self._user32.GetLastInputInfo(ctypes.byref(info)):
                return 0.0

            # GetTickCount wraps roughly every 49 days; masking to 32 bits keeps
            # the subtraction correct across the wrap instead of producing a
            # huge negative idle time.
            now = self._kernel32.GetTickCount() & 0xFFFFFFFF
            elapsed = (now - info.dwTime) & 0xFFFFFFFF
            return elapsed / 1000.0
        except Exception:  # noqa: BLE001
            logger.debug("Idle time query failed", exc_info=True)
            return 0.0

    def is_idle(self, threshold_seconds: float) -> bool:
        return self.idle_seconds() >= threshold_seconds
