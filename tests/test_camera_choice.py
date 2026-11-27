"""Which camera FocusGuard uses: a real webcam first, a phone app only without one.

The rule lives in one place (capture/camera_apps.choose_camera) and is used by
both the readiness check and the camera itself, so the camera that is checked
before a session is the camera the session opens.
"""

from __future__ import annotations

from focusguard_agent.capture.camera_apps import check_camera, choose_camera, is_virtual_camera
from focusguard_agent.config import CameraSettings

THIS_PC = ["Camo", "DroidCam Video", "Pixel 7 (Windows Virtual Camera)"]


def test_a_laptop_with_a_webcam_and_camo_uses_the_webcam():
    devices = ["Camo", "Integrated Camera", "DroidCam Video"]
    assert choose_camera(devices) == "Integrated Camera"


def test_a_usb_webcam_counts_as_a_real_webcam():
    assert choose_camera(["Camo", "Logitech C920 HD Pro Webcam"]) == "Logitech C920 HD Pro Webcam"


def test_a_computer_without_a_webcam_falls_back_to_camo():
    """This development PC: three virtual cameras, no webcam."""
    assert choose_camera(THIS_PC) == "Camo"


def test_the_phone_app_that_is_open_wins_among_phone_apps():
    assert choose_camera(THIS_PC, running={"droidcamapp"}) == "DroidCam Video"


def test_an_explicit_choice_in_settings_always_wins():
    devices = ["Integrated Camera", "Camo"]
    assert choose_camera(devices, configured="camo") == "Camo"


def test_a_configured_camera_that_is_unplugged_falls_back_to_the_rule():
    chosen = choose_camera(["Integrated Camera", "Camo"], configured="Logitech")
    assert chosen == "Integrated Camera"


def test_no_cameras_means_none():
    assert choose_camera([]) is None


def test_virtual_cameras_are_recognised():
    for name in THIS_PC + ["OBS Virtual Camera", "Iriun Webcam"]:
        assert is_virtual_camera(name), name
    for name in ["Integrated Camera", "HD Webcam", "Logitech C920 HD Pro Webcam"]:
        assert not is_virtual_camera(name), name


def test_the_default_setting_is_automatic():
    assert CameraSettings().device_name == ""


def test_the_readiness_check_follows_the_same_rule():
    readiness = check_camera("", devices=["Camo", "Integrated Camera"], processes=set())
    assert readiness.ready and readiness.device == "Integrated Camera" and readiness.app is None
