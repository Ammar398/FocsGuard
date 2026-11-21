"""SQLAlchemy models.

Importing this package registers every model with the metadata, which Alembic's
autogenerate and `create_all` both rely on. A model that is never imported is
invisible to both, and its table silently never gets created.
"""

from focusguard_backend.models.auth import RefreshToken
from focusguard_backend.models.base import Base, TimestampMixin, utc_now
from focusguard_backend.models.business import Project, Task
from focusguard_backend.models.identity import (
    Department,
    Device,
    Organization,
    PrivacyConsent,
    User,
)
from focusguard_backend.models.telemetry import (
    ActivityEvent,
    DeviceHeartbeat,
    FocusScore,
    FocusSession,
    VisionEvent,
)

__all__ = [
    "ActivityEvent",
    "Base",
    "Department",
    "Device",
    "DeviceHeartbeat",
    "FocusScore",
    "FocusSession",
    "Organization",
    "RefreshToken",
    "PrivacyConsent",
    "Project",
    "Task",
    "TimestampMixin",
    "User",
    "VisionEvent",
    "utc_now",
]
