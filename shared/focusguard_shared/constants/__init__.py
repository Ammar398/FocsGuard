"""Enumerations and fixed vocabularies used across FocusGuard."""

from focusguard_shared.constants.categories import (
    MIN_TASKS_FOR_STRENGTH_CLAIM,
    ActivityCategory,
    Classification,
    RuleSource,
    TaskCategory,
)
from focusguard_shared.constants.enums import (
    GPU_VENDOR_IDS,
    PROVIDER_PREFERENCE,
    GpuVendor,
    InferenceProvider,
    MonitorProfile,
    SessionState,
    SyncStatus,
    UserRole,
)
from focusguard_shared.constants.events import (
    PENALISING_VISION_EVENTS,
    EventState,
    GazeDirection,
    HeadPose,
    VisionEventType,
)

__all__ = [
    "GPU_VENDOR_IDS",
    "MIN_TASKS_FOR_STRENGTH_CLAIM",
    "PENALISING_VISION_EVENTS",
    "PROVIDER_PREFERENCE",
    "ActivityCategory",
    "Classification",
    "EventState",
    "GazeDirection",
    "GpuVendor",
    "HeadPose",
    "InferenceProvider",
    "MonitorProfile",
    "RuleSource",
    "SessionState",
    "SyncStatus",
    "TaskCategory",
    "UserRole",
    "VisionEventType",
]
