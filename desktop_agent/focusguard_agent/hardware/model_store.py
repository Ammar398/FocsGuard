"""Download and cache model bundles (spec sections 44, 45).

Models are cached under ``%LOCALAPPDATA%\\FocusGuard\\models`` rather than inside
the repository. The project stays small enough to submit, weights never end up in
git history, and a re-clone does not re-download them.

Integrity is checked with SHA-256 on every load, not only after downloading. A
truncated file from an interrupted download otherwise fails much later, deep
inside MediaPipe, with an error that says nothing useful.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from focusguard_shared.utilities import get_logger
from focusguard_shared.utilities.paths import model_cache_dir

logger = get_logger("hardware.model_store")

_CHUNK_SIZE = 1024 * 64


@dataclass(frozen=True)
class ModelSpec:
    """A downloadable model bundle."""

    key: str
    filename: str
    url: str
    sha256: str
    size_bytes: int
    description: str


#: MediaPipe Tasks bundles.
#:
#: MediaPipe 1.x removed the legacy ``mp.solutions`` API, so FaceLandmarker must
#: be constructed from one of these ``.task`` files - there is no longer a
#: built-in model. The float16 variant is used because it is half the size at
#: no measurable accuracy cost for our thresholds, and this machine's CPU budget
#: matters more than the last decimal place of landmark precision.
FACE_LANDMARKER = ModelSpec(
    key="face_landmarker",
    filename="face_landmarker.task",
    url=(
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
        "face_landmarker/float16/1/face_landmarker.task"
    ),
    # Recorded from the verified download on 2026-08-31.
    sha256="64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
    size_bytes=3758596,
    description="MediaPipe FaceLandmarker: 478 landmarks, blendshapes, head-pose matrix",
)

MODELS: dict[str, ModelSpec] = {FACE_LANDMARKER.key: FACE_LANDMARKER}


class ModelDownloadError(RuntimeError):
    """Raised when a model cannot be fetched or fails verification."""


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_cached(spec: ModelSpec) -> bool:
    """True if the model is present and its checksum matches."""
    path = model_cache_dir() / spec.filename
    if not path.exists():
        return False
    if not spec.sha256:
        return True
    return _sha256_of(path) == spec.sha256


def download_model(spec: ModelSpec, force: bool = False) -> Path:
    """Ensure ``spec`` is cached locally and return its path."""
    target = model_cache_dir() / spec.filename
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not force:
        if is_cached(spec):
            logger.debug("Model %s already cached", spec.key)
            return target
        logger.warning("Cached %s failed checksum verification; re-downloading", spec.filename)

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests is a declared dependency
        raise ModelDownloadError("The 'requests' package is required to download models") from exc

    logger.info("Downloading %s (%.1f MB)", spec.filename, spec.size_bytes / 1e6)

    # Download to a temporary name and move into place only after verification,
    # so an interrupted run can never leave a half-written file that looks valid.
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        with requests.get(spec.url, stream=True, timeout=60) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        handle.write(chunk)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise ModelDownloadError(f"Failed to download {spec.filename}: {exc}") from exc

    if spec.sha256:
        actual = _sha256_of(temporary)
        if actual != spec.sha256:
            temporary.unlink(missing_ok=True)
            raise ModelDownloadError(
                f"Checksum mismatch for {spec.filename}: "
                f"expected {spec.sha256}, got {actual}"
            )

    shutil.move(str(temporary), str(target))
    logger.info("Cached %s at %s", spec.key, target)
    return target


def get_model_path(key: str, auto_download: bool = True) -> Path:
    """Return the local path for a model, downloading it if needed."""
    spec = MODELS.get(key)
    if spec is None:
        raise KeyError(f"Unknown model {key!r}. Known models: {sorted(MODELS)}")

    path = model_cache_dir() / spec.filename
    if is_cached(spec):
        return path

    if not auto_download:
        raise ModelDownloadError(
            f"Model {key!r} is not cached. Run: python -m focusguard_agent.hardware.model_store"
        )

    return download_model(spec)


def main() -> int:
    """Console entry point: ``focusguard-fetch-models``."""
    from focusguard_shared.utilities import configure_logging

    configure_logging(level="INFO")

    print(f"Model cache: {model_cache_dir()}")
    failures = 0

    for spec in MODELS.values():
        if is_cached(spec):
            print(f"  [cached] {spec.filename} - {spec.description}")
            continue
        try:
            download_model(spec)
            print(f"  [ok]     {spec.filename} - {spec.description}")
        except ModelDownloadError as exc:
            failures += 1
            print(f"  [FAILED] {spec.filename}: {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
