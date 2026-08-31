"""Enumerate display adapters through DXGI, using ctypes only.

WHY THIS RATHER THAN WMI
------------------------
The obvious way to identify a GPU on Windows is WMI's ``Win32_VideoController``.
It has two problems that matter here:

1. ``AdapterRAM`` is a 32-bit field, so it saturates at 4 GB. On this machine it
   reports 4 GB for a card that actually has 8 GB.
2. Reading it means launching a PowerShell or WMI subprocess, costing roughly a
   second of start-up time.

DXGI is the interface Direct3D and DirectML themselves use. It enumerates only
adapters that are *currently present*, reports true dedicated video memory as a
64-bit value, and returns the PCI vendor ID directly. It costs a few
milliseconds and needs no third-party dependency.

That last point is the important one. This machine still has ``nvcuda.dll`` and
registry entries from a GeForce GTX 750 Ti and a Radeon RX 570 that are no longer
installed. Anything that infers the GPU from files or registry leftovers will get
the wrong answer. DXGI reports the adapter that is actually in the box.

WHAT IT DOES
------------
Calls ``CreateDXGIFactory1``, walks ``IDXGIFactory1::EnumAdapters1`` until it
returns ``DXGI_ERROR_NOT_FOUND``, and reads ``DXGI_ADAPTER_DESC1`` from each
adapter. COM is invoked by indexing each object's vtable, because the interfaces
are not exposed through any Python binding we want to depend on.

Every failure path returns an empty list rather than raising: a GPU probe must
never prevent the application from starting (spec section 46).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, byref, c_int32, c_size_t, c_uint32, c_void_p

from focusguard_shared.constants import GPU_VENDOR_IDS, GpuVendor
from focusguard_shared.schemas import GpuInfo
from focusguard_shared.utilities import get_logger

logger = get_logger("hardware.dxgi")

# --- COM / DXGI constants ---------------------------------------------------

_S_OK = 0
_DXGI_ERROR_NOT_FOUND = -2005270526  # 0x887A0002 as a signed 32-bit value
_DXGI_ADAPTER_FLAG_SOFTWARE = 2

# vtable slot numbers. Each interface inherits its parent's slots, so the counts
# accumulate: IUnknown occupies 0-2, IDXGIObject 3-6, and so on.
_VT_RELEASE = 2  # IUnknown::Release
_VT_ENUM_ADAPTERS1 = 12  # IDXGIFactory1::EnumAdapters1
_VT_GET_DESC1 = 10  # IDXGIAdapter1::GetDesc1

#: Guard against a malformed enumeration looping forever.
_MAX_ADAPTERS = 16


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# IID_IDXGIFactory1 = {770aae78-f26f-4dba-a829-253c83d1b387}
_IID_IDXGIFactory1 = _GUID(
    0x770AAE78,
    0xF26F,
    0x4DBA,
    (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87),
)


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _DXGI_ADAPTER_DESC1(ctypes.Structure):
    """Layout of DXGI_ADAPTER_DESC1 (dxgi.h)."""

    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint32),
        ("DeviceId", ctypes.c_uint32),
        ("SubSysId", ctypes.c_uint32),
        ("Revision", ctypes.c_uint32),
        ("DedicatedVideoMemory", c_size_t),
        ("DedicatedSystemMemory", c_size_t),
        ("SharedSystemMemory", c_size_t),
        ("AdapterLuid", _LUID),
        ("Flags", ctypes.c_uint32),
    ]


def _com_call(interface: c_void_p, slot: int, *argtypes_and_args: object) -> int:
    """Invoke a COM method by vtable slot.

    An interface pointer points at a pointer to the vtable, so two
    dereferences reach the array of function addresses. ``argtypes_and_args`` is
    a flat sequence of (type, value) pairs following the implicit ``this``.
    """
    vtable = ctypes.cast(interface, POINTER(POINTER(c_void_p)))[0]
    address = vtable[slot]

    argtypes = list(argtypes_and_args[0::2])
    args = list(argtypes_and_args[1::2])

    prototype = ctypes.WINFUNCTYPE(c_int32, c_void_p, *argtypes)  # type: ignore[arg-type]
    method = prototype(address)
    return int(method(interface, *args))


def _release(interface: c_void_p | None) -> None:
    if interface:
        try:
            _com_call(interface, _VT_RELEASE)
        except Exception:  # noqa: BLE001 - releasing must never propagate
            logger.debug("COM Release failed", exc_info=True)


def enumerate_adapters() -> list[GpuInfo]:
    """Return every present display adapter, best-effort.

    Returns an empty list on any non-Windows platform or on any failure, so the
    caller can fall back to another detection method.
    """
    if sys.platform != "win32":
        return []

    try:
        dxgi = ctypes.WinDLL("dxgi.dll")
    except OSError:
        logger.warning("dxgi.dll could not be loaded; GPU detection unavailable")
        return []

    factory = c_void_p()
    try:
        dxgi.CreateDXGIFactory1.argtypes = [POINTER(_GUID), POINTER(c_void_p)]
        dxgi.CreateDXGIFactory1.restype = c_int32
        hresult = dxgi.CreateDXGIFactory1(byref(_IID_IDXGIFactory1), byref(factory))
    except Exception:  # noqa: BLE001
        logger.warning("CreateDXGIFactory1 raised; GPU detection unavailable", exc_info=True)
        return []

    if hresult != _S_OK or not factory:
        logger.warning("CreateDXGIFactory1 failed with HRESULT 0x%08X", hresult & 0xFFFFFFFF)
        return []

    adapters: list[GpuInfo] = []
    try:
        for index in range(_MAX_ADAPTERS):
            adapter = c_void_p()
            hresult = _com_call(
                factory,
                _VT_ENUM_ADAPTERS1,
                c_uint32,
                index,
                POINTER(c_void_p),
                byref(adapter),
            )

            if hresult == _DXGI_ERROR_NOT_FOUND:
                break
            if hresult != _S_OK or not adapter:
                logger.debug("EnumAdapters1(%d) returned 0x%08X", index, hresult & 0xFFFFFFFF)
                break

            try:
                desc = _DXGI_ADAPTER_DESC1()
                hresult = _com_call(
                    adapter,
                    _VT_GET_DESC1,
                    POINTER(_DXGI_ADAPTER_DESC1),
                    byref(desc),
                )
                if hresult != _S_OK:
                    logger.debug("GetDesc1(%d) returned 0x%08X", index, hresult & 0xFFFFFFFF)
                    continue

                adapters.append(
                    GpuInfo(
                        name=desc.Description.strip(),
                        vendor=GPU_VENDOR_IDS.get(desc.VendorId, GpuVendor.UNKNOWN),
                        vendor_id=desc.VendorId,
                        device_id=desc.DeviceId,
                        dedicated_memory_bytes=int(desc.DedicatedVideoMemory),
                        is_software_adapter=bool(desc.Flags & _DXGI_ADAPTER_FLAG_SOFTWARE),
                    )
                )
            finally:
                _release(adapter)
    except Exception:  # noqa: BLE001 - never let hardware probing stop start-up
        logger.warning("DXGI adapter enumeration failed", exc_info=True)
    finally:
        _release(factory)

    return adapters


def has_vendor(adapters: list[GpuInfo], vendor: GpuVendor) -> bool:
    """True if a non-software adapter from ``vendor`` is physically present.

    This is the check that keeps CUDA detection honest: leftover NVIDIA driver
    DLLs do not make an NVIDIA GPU present.
    """
    return any(gpu.vendor is vendor and not gpu.is_software_adapter for gpu in adapters)
