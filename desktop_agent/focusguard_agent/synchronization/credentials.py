"""The device credential this installation holds (spec section 25).

WHAT IS STORED
--------------
Three things, in one small JSON file under `%LOCALAPPDATA%\\FocusGuard`:

* `device_uuid`  - generated once, on first run, and stable for the life of the
  installation;
* `device_secret` - issued by the backend at enrolment, and the only proof the
  agent has that it is allowed to upload;
* `backend_url`  - so the agent talks to the server it enrolled with, rather
  than whatever a config file happens to say later.

WHY A FILE AND NOT THE REGISTRY OR A KEYRING
--------------------------------------------
The Windows Credential Manager would be a better vault. It is also unavailable
when the agent runs as a scheduled task under a different account, needs an extra
dependency, and cannot be inspected when something goes wrong at three in the
morning before a demo. A file the user owns is the honest choice at this scale,
and the threat it defends against is stated plainly rather than overclaimed:

    An attacker who can already read this user's LOCALAPPDATA can read the
    credential. What the credential gets them is the ability to upload junk
    telemetry for one user - it cannot read anybody's data (see the backend's
    `auth/dependencies.py`). That is a deliberately small blast radius.

The file is written with an explicit owner-only ACL where the platform supports
it. On Windows that is best-effort: it is a real improvement when it works and
never a reason to fail a working install.

WHY THE UUID IS SEPARATE FROM THE SECRET
----------------------------------------
The uuid is generated **before** enrolment and survives it. That means an agent
that is revoked and re-enrolled keeps its identity, so the two enrolments read as
one machine's history rather than two. The secret is the part that rotates.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import uuid as uuid_module
from dataclasses import dataclass
from pathlib import Path

from focusguard_shared.utilities import get_logger
from focusguard_shared.utilities.paths import app_data_dir

logger = get_logger("agent.sync.credentials")

CREDENTIAL_FILE_NAME = "device.json"


class NotEnrolled(Exception):
    """The agent has no device secret, so it cannot upload."""


@dataclass(frozen=True)
class DeviceCredential:
    """What this installation needs in order to upload."""

    device_uuid: str
    device_secret: str
    backend_url: str
    user_id: int | None = None
    organization_id: int | None = None

    @property
    def headers(self) -> dict[str, str]:
        """The header the backend authenticates on."""
        return {"X-Device-Secret": self.device_secret}


def credential_path(directory: Path | None = None) -> Path:
    return (directory or app_data_dir()) / CREDENTIAL_FILE_NAME


def read_device_uuid(directory: Path | None = None) -> str:
    """This machine's stable identifier, generating it on first call.

    Written even before enrolment so the value a user reads out of the file is
    the same one that ends up on the server.
    """
    path = credential_path(directory)
    data = _read_json(path)

    existing = data.get("device_uuid")
    if isinstance(existing, str) and existing:
        return existing

    generated = str(uuid_module.uuid4())
    data["device_uuid"] = generated
    _write_json(path, data)
    logger.info("Generated a device identifier for this installation")
    return generated


def load_credential(directory: Path | None = None) -> DeviceCredential:
    """Load the stored credential, or raise `NotEnrolled`."""
    data = _read_json(credential_path(directory))

    secret = data.get("device_secret")
    device_uuid = data.get("device_uuid")
    backend_url = data.get("backend_url")

    if not (secret and device_uuid and backend_url):
        raise NotEnrolled(
            "This machine is not enrolled. Run:\n"
            "    python -m focusguard_agent.tools.enroll --token <enrolment token>"
        )

    return DeviceCredential(
        device_uuid=device_uuid,
        device_secret=secret,
        backend_url=backend_url,
        user_id=data.get("user_id"),
        organization_id=data.get("organization_id"),
    )


def is_enrolled(directory: Path | None = None) -> bool:
    try:
        load_credential(directory)
    except NotEnrolled:
        return False
    return True


def save_credential(credential: DeviceCredential, directory: Path | None = None) -> Path:
    """Store the credential, replacing any previous one."""
    path = credential_path(directory)
    _write_json(
        path,
        {
            "device_uuid": credential.device_uuid,
            "device_secret": credential.device_secret,
            "backend_url": credential.backend_url,
            "user_id": credential.user_id,
            "organization_id": credential.organization_id,
        },
    )
    logger.info("Stored the device credential at %s", path)
    return path


def forget_credential(directory: Path | None = None) -> bool:
    """Remove the stored secret, keeping the device identity.

    What "unenrol this machine" does locally. The uuid stays so that if the same
    machine is enrolled again it is recognisable as the same machine.
    """
    path = credential_path(directory)
    data = _read_json(path)
    if "device_secret" not in data:
        return False

    for key in ("device_secret", "user_id", "organization_id"):
        data.pop(key, None)
    _write_json(path, data)
    logger.info("Removed the stored device credential")
    return True


# --- file handling ----------------------------------------------------------


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        # A corrupt file must not crash the agent on start-up. Treating it as
        # absent means the worst case is "please enrol again", not a crash loop.
        logger.warning("Could not read %s (%s); treating it as unenrolled", path, error)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # Written to a temporary file and moved into place, so a crash mid-write
    # cannot leave a half-written credential that reads as corrupt.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)

    _restrict_to_owner(path)


def _restrict_to_owner(path: Path) -> None:
    """Best-effort owner-only permissions.

    Never raises. A failure here means the file keeps the directory's inherited
    permissions, which is what it would have had anyway - so refusing to run
    would trade a working install for no improvement.
    """
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    if os.name != "nt":
        return

    try:
        # icacls is the only way to actually remove inherited access on Windows;
        # chmod alone sets the read-only attribute and nothing more.
        subprocess.run(  # noqa: S603
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{os.environ.get('USERNAME', '')}:F",
            ],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.debug("Could not restrict permissions on %s: %s", path, error)
