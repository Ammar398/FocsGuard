"""General-purpose enumerations: roles, hardware providers, sync and monitors."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """Authorisation roles (spec sections 6, 51)."""

    STUDENT = "STUDENT"
    MANAGER = "MANAGER"
    EMPLOYEE = "EMPLOYEE"

    #: An enrolled agent installation. A device credential may WRITE its own
    #: events and read nothing about any other employee. Keeping this a distinct
    #: role means a stolen device token cannot be used to browse an organisation.
    DEVICE = "DEVICE"


class InferenceProvider(StrEnum):
    """Execution provider selected for neural-network inference.

    Ordered by preference in `PROVIDER_PREFERENCE`. On the development machine
    (AMD Radeon RX 580) only DIRECTML and CPU are ever reachable; CUDA remains
    supported so the project still accelerates on an NVIDIA machine.
    """

    CUDA = "CUDAExecutionProvider"
    DIRECTML = "DmlExecutionProvider"
    CPU = "CPUExecutionProvider"


#: Preference order used by the hardware detector. CUDA is only ever chosen when
#: an NVIDIA adapter is actually present - see hardware/detector.py, which
#: deliberately checks the adapter vendor before trusting any CUDA library,
#: because this machine carries leftover nvcuda.dll from a removed GPU.
PROVIDER_PREFERENCE: tuple[InferenceProvider, ...] = (
    InferenceProvider.CUDA,
    InferenceProvider.DIRECTML,
    InferenceProvider.CPU,
)


class GpuVendor(StrEnum):
    """GPU vendor resolved from the PCI vendor ID of the active adapter."""

    NVIDIA = "NVIDIA"
    AMD = "AMD"
    INTEL = "INTEL"
    MICROSOFT = "MICROSOFT"  # software / basic render adapter
    UNKNOWN = "UNKNOWN"


#: PCI vendor IDs, as reported by DXGI.
GPU_VENDOR_IDS: dict[int, GpuVendor] = {
    0x10DE: GpuVendor.NVIDIA,
    0x1002: GpuVendor.AMD,
    0x1022: GpuVendor.AMD,
    0x8086: GpuVendor.INTEL,
    0x1414: GpuVendor.MICROSOFT,
}


class SyncStatus(StrEnum):
    """Upload state of a locally stored record (spec section 37)."""

    PENDING = "PENDING"
    SYNCING = "SYNCING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


class MonitorProfile(StrEnum):
    """Display setup, which changes how gaze-away is penalised (spec section 60).

    A user with two screens legitimately looks away from the webcam while
    working. Penalising that identically to a single-monitor user would be wrong,
    so the gaze-away weight is scaled by this profile.
    """

    SINGLE = "SINGLE"
    DUAL = "DUAL"
    MULTI = "MULTI"


class SessionState(StrEnum):
    """Lifecycle of a focus session."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ENDED = "ENDED"
