"""Pydantic contracts shared between the desktop agent and the backend."""

from focusguard_shared.schemas.events import (
    ActivityEvent,
    EventBatch,
    FocusScoreSample,
    TimeSpan,
    VisionEvent,
    VisionFrameObservation,
    WebsiteUsage,
)
from focusguard_shared.schemas.hardware import CpuInfo, GpuInfo, HardwareReport

__all__ = [
    "ActivityEvent",
    "CpuInfo",
    "EventBatch",
    "FocusScoreSample",
    "GpuInfo",
    "HardwareReport",
    "TimeSpan",
    "VisionEvent",
    "VisionFrameObservation",
    "WebsiteUsage",
]
