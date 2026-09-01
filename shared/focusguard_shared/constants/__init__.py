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
    EVENT_SUPERSEDES,
    PENALISING_VISION_EVENTS,
    EventState,
    GazeDirection,
    HeadPose,
    VisionEventType,
    resolve_superseded,
)

__all__ = [
    "EVENT_SUPERSEDES",
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
    "resolve_superseded",
]
