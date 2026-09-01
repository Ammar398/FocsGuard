"""Enumerate video capture devices by name, using DirectShow through ctypes.

WHY THIS MODULE EXISTS
----------------------
OpenCV opens cameras by integer index: ``cv2.VideoCapture(0)``. It offers no way
to ask what device 0 actually *is*.

That is a real problem here. This machine has no built-in webcam - every camera
is virtual: Camo, DroidCam and the Windows Connected Camera for a Pixel 7. Virtual
cameras register with the system when their host application launches and
de-register when it closes, so index 0 is Camo today and DroidCam tomorrow,
depending only on what happened to be running. Hardcoding an index means the
agent silently opens the wrong camera, or opens nothing.

The fix is to enumerate devices, match the one the user configured by name, and
pass its position to OpenCV.

WHY DIRECTSHOW SPECIFICALLY
---------------------------
The order matters as much as the names. OpenCV's ``CAP_DSHOW`` backend indexes
cameras in DirectShow enumeration order, so enumerating through DirectShow gives
positions that map directly onto the index OpenCV expects. Other sources of
camera names - PowerShell's ``Get-PnpDevice``, Windows Media Foundation, the
registry - list the same devices in a different order, so their positions cannot
be used as OpenCV indices.

We call the COM interfaces through ctypes rather than adding a dependency, the
same approach used in ``hardware/dxgi.py``. Every failure path degrades to
index-probing rather than raising: an unusual system must still be able to open
a camera (spec section 46).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, byref, c_int32, c_uint32, c_void_p
from dataclasses import dataclass

from focusguard_shared.utilities import get_logger

logger = get_logger("capture.devices")

_S_OK = 0
_S_FALSE = 1
_CLSCTX_INPROC_SERVER = 1
_COINIT_APARTMENTTHREADED = 2
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106
_VT_BSTR = 8

#: Guard against a broken enumerator looping forever.
_MAX_DEVICES = 32


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(d1: int, d2: int, d3: int, *rest: int) -> _GUID:
    return _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*rest))


# CLSID_SystemDeviceEnum        {62BE5D10-60EB-11d0-BD3B-00A0C911CE86}
_CLSID_SystemDeviceEnum = _guid(
    0x62BE5D10, 0x60EB, 0x11D0, 0xBD, 0x3B, 0x00, 0xA0, 0xC9, 0x11, 0xCE, 0x86
)
# IID_ICreateDevEnum            {29840822-5B84-11D0-BD3B-00A0C911CE86}
_IID_ICreateDevEnum = _guid(
    0x29840822, 0x5B84, 0x11D0, 0xBD, 0x3B, 0x00, 0xA0, 0xC9, 0x11, 0xCE, 0x86
)
# CLSID_VideoInputDeviceCategory {860BB310-5D01-11d0-BD3B-00A0C911CE86}
_CLSID_VideoInputDeviceCategory = _guid(
    0x860BB310, 0x5D01, 0x11D0, 0xBD, 0x3B, 0x00, 0xA0, 0xC9, 0x11, 0xCE, 0x86
)
# IID_IPropertyBag              {55272A00-42CB-11CE-8135-00AA004BB851}
_IID_IPropertyBag = _guid(
    0x55272A00, 0x42CB, 0x11CE, 0x81, 0x35, 0x00, 0xAA, 0x00, 0x4B, 0xB8, 0x51
)

# vtable slots (each interface inherits its parents' slots)
_VT_RELEASE = 2  # IUnknown::Release
_VT_CREATE_CLASS_ENUMERATOR = 3  # ICreateDevEnum::CreateClassEnumerator
_VT_ENUM_NEXT = 3  # IEnumMoniker::Next
_VT_BIND_TO_STORAGE = 9  # IMoniker::BindToStorage (after IPersistStream)
_VT_PROPBAG_READ = 3  # IPropertyBag::Read


class _VARIANT(ctypes.Structure):
    """Minimal VARIANT. 24 bytes on x64, which the two trailing pointers ensure."""

    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("value", c_void_p),
        ("_padding", c_void_p),
    ]


@dataclass(frozen=True)
class CaptureDevice:
    """One video capture device.

    ``index`` is the DirectShow position, which is what ``cv2.VideoCapture``
    expects when used with the ``CAP_DSHOW`` backend.
    """

    index: int
    name: str

    def __str__(self) -> str:
        return f"[{self.index}] {self.name}"


def _com_call(interface: c_void_p, slot: int, *argtypes_and_args: object) -> int:
    """Invoke a COM method by vtable slot (see hardware/dxgi.py for the pattern)."""
    vtable = ctypes.cast(interface, POINTER(POINTER(c_void_p)))[0]
    argtypes = list(argtypes_and_args[0::2])
    args = list(argtypes_and_args[1::2])
    prototype = ctypes.WINFUNCTYPE(c_int32, c_void_p, *argtypes)  # type: ignore[arg-type]
    return int(prototype(vtable[slot])(interface, *args))


def _release(interface: c_void_p | None) -> None:
    if interface:
        try:
            _com_call(interface, _VT_RELEASE)
        except Exception:  # noqa: BLE001 - releasing must never propagate
            logger.debug("COM Release failed", exc_info=True)


def _read_friendly_name(moniker: c_void_p, oleaut32: ctypes.WinDLL) -> str | None:
    """Read the "FriendlyName" property from a device moniker."""
    prop_bag = c_void_p()
    hresult = _com_call(
        moniker,
        _VT_BIND_TO_STORAGE,
        c_void_p,
        None,
        c_void_p,
        None,
        POINTER(_GUID),
        byref(_IID_IPropertyBag),
        POINTER(c_void_p),
        byref(prop_bag),
    )
    if hresult != _S_OK or not prop_bag:
        return None

    try:
        variant = _VARIANT()
        hresult = _com_call(
            prop_bag,
            _VT_PROPBAG_READ,
            ctypes.c_wchar_p,
            "FriendlyName",
            POINTER(_VARIANT),
            byref(variant),
            c_void_p,
            None,
        )
        if hresult != _S_OK or variant.vt != _VT_BSTR or not variant.value:
            return None

        name = ctypes.cast(variant.value, ctypes.c_wchar_p).value
        oleaut32.VariantClear(byref(variant))
        return name.strip() if name else None
    finally:
        _release(prop_bag)


def enumerate_devices() -> list[CaptureDevice]:
    """Return every video capture device in DirectShow order.

    Returns an empty list on non-Windows platforms or on any failure, leaving the
    caller to fall back to :func:`probe_devices`.
    """
    if sys.platform != "win32":
        return []

    try:
        ole32 = ctypes.WinDLL("ole32.dll")
        oleaut32 = ctypes.WinDLL("oleaut32.dll")
    except OSError:
        logger.warning("COM libraries unavailable; cannot enumerate cameras by name")
        return []

    # A thread may already have COM initialised in a different mode; that is not
    # an error and must not stop us, but we then must not uninitialise it either.
    hresult = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    should_uninitialise = hresult in (_S_OK, _S_FALSE)
    if hresult == _RPC_E_CHANGED_MODE:
        logger.debug("COM already initialised in a different threading mode")

    devices: list[CaptureDevice] = []
    dev_enum = c_void_p()
    enum_moniker = c_void_p()

    try:
        ole32.CoCreateInstance.argtypes = [
            POINTER(_GUID),
            c_void_p,
            c_uint32,
            POINTER(_GUID),
            POINTER(c_void_p),
        ]
        ole32.CoCreateInstance.restype = c_int32
        hresult = ole32.CoCreateInstance(
            byref(_CLSID_SystemDeviceEnum),
            None,
            _CLSCTX_INPROC_SERVER,
            byref(_IID_ICreateDevEnum),
            byref(dev_enum),
        )
        if hresult != _S_OK or not dev_enum:
            logger.warning(
                "CoCreateInstance(SystemDeviceEnum) failed: 0x%08X", hresult & 0xFFFFFFFF
            )
            return []

        hresult = _com_call(
            dev_enum,
            _VT_CREATE_CLASS_ENUMERATOR,
            POINTER(_GUID),
            byref(_CLSID_VideoInputDeviceCategory),
            POINTER(c_void_p),
            byref(enum_moniker),
            c_uint32,
            0,
        )
        # S_FALSE means the category exists but holds no devices - i.e. no cameras
        # are connected. That is a normal state, not a failure.
        if hresult != _S_OK or not enum_moniker:
            logger.info("No video capture devices are registered with DirectShow")
            return []

        for index in range(_MAX_DEVICES):
            moniker = c_void_p()
            fetched = c_uint32(0)
            hresult = _com_call(
                enum_moniker,
                _VT_ENUM_NEXT,
                c_uint32,
                1,
                POINTER(c_void_p),
                byref(moniker),
                POINTER(c_uint32),
                byref(fetched),
            )
            if hresult != _S_OK or not fetched.value or not moniker:
                break

            try:
                name = _read_friendly_name(moniker, oleaut32)
                devices.append(CaptureDevice(index=index, name=name or f"Camera {index}"))
            finally:
                _release(moniker)

    except Exception:  # noqa: BLE001 - never let enumeration stop the agent
        logger.warning("DirectShow device enumeration failed", exc_info=True)
    finally:
        _release(enum_moniker)
        _release(dev_enum)
        if should_uninitialise:
            ole32.CoUninitialize()

    return devices


def probe_devices(max_index: int = 5) -> list[CaptureDevice]:
    """Fallback: open indices in turn and report which ones respond.

    Slower and nameless, but it still finds a usable camera when DirectShow
    enumeration is unavailable.
    """
    try:
        import cv2
    except ImportError:
        return []

    found: list[CaptureDevice] = []
    for index in range(max_index):
        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        try:
            if capture.isOpened():
                found.append(CaptureDevice(index=index, name=f"Camera {index}"))
        finally:
            capture.release()
    return found


def find_device(name_fragment: str | None, fallback_index: int = 0) -> CaptureDevice | None:
    """Resolve the configured camera to a concrete device.

    Matching is a case-insensitive substring test, so "camo" finds "Camo" and
    "droid" finds "DroidCam Video" without the user needing the exact string.

    Falls back to ``fallback_index`` when the named device is not present, so a
    closed Camo Studio degrades to *some* camera rather than to nothing - and
    logs a warning, because silently using a different camera than the one
    configured would be confusing.
    """
    devices = enumerate_devices() or probe_devices()
    if not devices:
        logger.error("No video capture devices found")
        return None

    if name_fragment:
        needle = name_fragment.strip().lower()
        for device in devices:
            if needle in device.name.lower():
                return device

        available = ", ".join(device.name for device in devices)
        logger.warning(
            "Camera %r not found; falling back to index %d. Available: %s",
            name_fragment,
            fallback_index,
            available,
        )

    for device in devices:
        if device.index == fallback_index:
            return device

    return devices[0]
