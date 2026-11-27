"""Is there a camera that will actually send video - and if not, what to do.

THE PROBLEM
-----------
Phone-as-webcam apps (Camo, DroidCam) install a *virtual* camera. Windows lists
it whether or not the app is running, but it only carries video while the app
is open and connected to the phone. So "a camera exists" is not the same as
"a session will see you", and a session started with the app closed would sit
on "waiting for camera" with no explanation.

WHAT THIS DECIDES, BEFORE THE CAMERA IS TURNED ON
-------------------------------------------------
    no camera at all              -> refuse, say how to get one (phone app links)
    a real webcam                 -> ready
    a phone-camera app, running   -> ready
    a phone-camera app, closed    -> open the app, then continue (the session
                                     shows "Waiting for Camo..." until video arrives)
    a phone-camera app, but the   -> refuse, with the app's download link
    app is not installed

Apps are opened the way the Start menu opens them (`shell:AppsFolder\\<AppID>`),
which works for Microsoft Store apps such as Camo Studio and for ordinary
programs alike.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache

from focusguard_shared.utilities import get_logger

logger = get_logger("capture.camera_apps")


@dataclass(frozen=True)
class PhoneCameraApp:
    key: str
    label: str
    #: Matched case-insensitively against the virtual camera's device name.
    device_markers: tuple[str, ...]
    #: Process names (without .exe) that mean the app is open.
    processes: tuple[str, ...]
    #: Start-menu names to find its AppID by.
    start_names: tuple[str, ...]
    download_url: str


KNOWN_APPS: tuple[PhoneCameraApp, ...] = (
    PhoneCameraApp(
        key="camo",
        label="Camo Studio",
        device_markers=("camo",),
        processes=("camostudio", "camo"),
        start_names=("camo studio", "camo"),
        download_url="https://reincubate.com/camo/",
    ),
    PhoneCameraApp(
        key="droidcam",
        label="DroidCam",
        device_markers=("droidcam",),
        processes=("droidcamapp", "droidcam"),
        start_names=("droidcam client", "droidcam"),
        download_url="https://www.dev47apps.com/",
    ),
)


@dataclass
class CameraReadiness:
    ready: bool
    #: Shown to the person, in plain words.
    message: str
    device: str | None = None
    app: str | None = None
    app_running: bool | None = None
    app_installed: bool | None = None
    opened_app: bool = False
    download_url: str | None = None
    devices: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "message": self.message,
            "device": self.device,
            "app": self.app,
            "app_running": self.app_running,
            "app_installed": self.app_installed,
            "opened_app": self.opened_app,
            "download_url": self.download_url,
            "devices": self.devices,
        }


#: Name fragments of software cameras: phone apps, streaming tools, Windows'
#: own "Connected Camera". Anything else is treated as a real webcam.
VIRTUAL_MARKERS: tuple[str, ...] = (
    "camo", "droidcam", "iriun", "epoccam", "virtual", "obs", "snap camera",
    "manycam", "xsplit",
)


def is_virtual_camera(device_name: str) -> bool:
    lowered = device_name.lower()
    return any(marker in lowered for marker in VIRTUAL_MARKERS)


def choose_camera(
    devices: list[str],
    configured: str | None = None,
    running: set[str] | None = None,
) -> str | None:
    """The one rule for which camera FocusGuard uses.

        1. a camera the person chose in settings, if it is connected;
        2. otherwise a real webcam - built in or USB - in Windows' order;
        3. otherwise a phone-camera app whose app is open;
        4. otherwise a known phone-camera app (Camo before DroidCam);
        5. otherwise whatever camera there is.

    A real webcam comes first because it needs nothing else running. A phone
    app is the fallback for computers without one.
    """
    if not devices:
        return None

    wanted = (configured or "").strip().lower()
    if wanted:
        for name in devices:
            if wanted in name.lower():
                return name

    webcams = [name for name in devices if not is_virtual_camera(name)]
    if webcams:
        return webcams[0]

    open_apps = running if running is not None else set()
    for name in devices:
        app = app_for_device(name)
        if app and any(process in open_apps for process in app.processes):
            return name

    for app in KNOWN_APPS:
        for name in devices:
            if app_for_device(name) is app:
                return name

    return devices[0]


def app_for_device(device_name: str) -> PhoneCameraApp | None:
    lowered = device_name.lower()
    for app in KNOWN_APPS:
        if any(marker in lowered for marker in app.device_markers):
            return app
    return None


def running_processes() -> set[str]:
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a declared dependency
        return set()
    names = set()
    for process in psutil.process_iter(["name"]):
        name = (process.info.get("name") or "").lower()
        names.add(name.removesuffix(".exe"))
    return names


@lru_cache(maxsize=1)
def _start_apps() -> tuple[tuple[str, str], ...]:
    """(name, AppID) for everything in the Start menu. Cached: it costs ~1 s."""
    if sys.platform != "win32":
        return ()
    try:
        output = subprocess.run(  # noqa: S603
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-StartApps | ForEach-Object { $_.Name + \"`t\" + $_.AppID }",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not list Start menu apps", exc_info=True)
        return ()
    pairs = []
    for line in output.splitlines():
        if "\t" in line:
            name, app_id = line.split("\t", 1)
            pairs.append((name.strip(), app_id.strip()))
    return tuple(pairs)


def find_app_id(app: PhoneCameraApp) -> str | None:
    for wanted in app.start_names:
        for name, app_id in _start_apps():
            if name.lower() == wanted:
                return app_id
    return None


def open_app(app_id: str) -> bool:
    """Open an app exactly as the Start menu would."""
    if sys.platform != "win32":
        return False
    try:
        os.startfile(f"shell:AppsFolder\\{app_id}")  # noqa: S606 - a fixed shell target
        return True
    except OSError:
        logger.warning("Could not open %s", app_id, exc_info=True)
        return False


def check_camera(
    configured_name: str | None,
    *,
    devices: list[str] | None = None,
    processes: set[str] | None = None,
    find_app=find_app_id,
    opener=open_app,
    allow_open: bool = True,
) -> CameraReadiness:
    """Decide whether a session can see the person, opening the phone app if needed.

    Every collaborator is a parameter so the decision is testable without a
    camera, a phone, or Windows.
    """
    if devices is None:
        from focusguard_agent.capture.devices import enumerate_devices

        devices = [device.name for device in enumerate_devices()]

    if not devices:
        return CameraReadiness(
            ready=False,
            devices=[],
            message=(
                "No camera was found on this computer. Connect a webcam, or use your phone "
                "as one with Camo (reincubate.com/camo) or DroidCam, then try again."
            ),
            download_url=KNOWN_APPS[0].download_url,
        )

    names = processes if processes is not None else running_processes()
    # The same rule the camera itself uses (capture/devices.find_device), so
    # the camera checked here is the camera the session opens.
    device = choose_camera(devices, configured_name, names) or devices[0]
    app = app_for_device(device)

    if app is None:
        return CameraReadiness(ready=True, device=device, devices=devices,
                               message=f"Using {device}.")

    if any(process in names for process in app.processes):
        return CameraReadiness(
            ready=True, device=device, devices=devices, app=app.label,
            app_running=True, app_installed=True,
            message=f"{app.label} is open. Make sure your phone is connected in it.",
        )

    app_id = find_app(app)
    if app_id is None:
        return CameraReadiness(
            ready=False, device=device, devices=devices, app=app.label,
            app_running=False, app_installed=False, download_url=app.download_url,
            message=(
                f"FocusGuard uses {app.label} as your camera, but the app isn't installed. "
                f"Download it from {app.download_url}, connect your phone, then try again."
            ),
        )

    opened = allow_open and opener(app_id)
    return CameraReadiness(
        ready=True, device=device, devices=devices, app=app.label,
        app_running=False, app_installed=True, opened_app=opened,
        message=(
            f"Opening {app.label}. Connect your phone in it - FocusGuard starts analysing "
            "as soon as video arrives."
            if opened
            else f"Open {app.label} and connect your phone - FocusGuard is waiting for video."
        ),
    )


def frames_look_live(previous, current) -> bool:
    """Two small greyscale frames: is this a real picture, not a placeholder?

    A phone-camera app with no phone connected sends black, or one fixed
    picture. Real video is never pure black and is never pixel-identical twice
    (sensor noise), so "bright enough and changing" means a phone is sending.
    """
    import numpy as np

    if previous is None or current is None:
        return False
    if float(current.mean()) < 8.0:
        return False
    return float(np.abs(current.astype("float32") - previous.astype("float32")).mean()) > 0.0


def wait_for_video(device_name: str, timeout: float = 25.0) -> bool:
    """Open the camera briefly and wait until real video arrives.

    Used before a session or camera check starts on a phone camera (Camo,
    DroidCam): the window then opens on a live picture straight away, instead
    of a black one that recovers only after the agent's reconnect cycle.
    Returns as soon as video is live; False after `timeout`. Nothing is kept.
    """
    import time

    import cv2

    from focusguard_agent.capture.devices import find_device

    device = find_device(device_name)
    if device is None:
        return False
    deadline = time.monotonic() + timeout
    capture = None
    opened_at = 0.0
    previous = None
    try:
        while time.monotonic() < deadline:
            # A virtual camera opened before its phone connected can stay on
            # the placeholder until it is reopened, so reopen every 6 seconds.
            if capture is None or time.monotonic() - opened_at > 6.0:
                if capture is not None:
                    capture.release()
                capture = cv2.VideoCapture(device.index, cv2.CAP_DSHOW)
                opened_at = time.monotonic()
                previous = None
                if not capture.isOpened():
                    time.sleep(0.5)
                    continue
            ok, image = capture.read()
            if not ok or image is None or image.size == 0:
                time.sleep(0.05)
                continue
            small = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (64, 36))
            if frames_look_live(previous, small):
                return True
            previous = small
    except cv2.error:
        logger.warning("Video check failed", exc_info=True)
        return False
    finally:
        if capture is not None:
            capture.release()
    return False
