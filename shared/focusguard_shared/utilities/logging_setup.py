"""Structured, rotating logging with secret redaction (spec section 50).

Two deliberate choices:

* **Rotation is mandatory, not optional.** The agent runs for hours and writes
  from several threads. On a 128 GB SSD an unbounded log is a real hazard, so
  every handler is size-capped with a bounded backup count (spec section 45).

* **Redaction happens in a filter, not at call sites.** Relying on developers to
  remember not to log a token is how tokens end up in logs. The filter runs over
  every record regardless of which module emitted it.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

#: Patterns whose *values* are replaced before a record is written. Covers
#: key=value, JSON and bare-token shapes, since log messages use all three.
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api_key|apikey|authorization|refresh_token"
            # The optional quote after the key name is what lets this match the
            # JSON form {"token": "..."} as well as key=value and key: value.
            r"|access_token|enrollment_token)\b(\"?\s*[=:]\s*)(\"?)([^\s,;\"'}]+)(\"?)"
        ),
        r"\1\2\3***REDACTED***\5",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer ***REDACTED***"),
)

#: Query strings are stripped from any URL that reaches the log. They carry
#: search terms and identifiers that section 50 says must not be recorded.
_URL_QUERY_PATTERN = re.compile(r"(https?://[^\s\"']+?)\?[^\s\"']*")


class RedactingFilter(logging.Filter):
    """Removes credentials and URL query strings from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Render args into the message now, so redaction also covers values that
        # arrive through %-style lazy formatting.
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string must not kill logging
            return True

        original = message
        for pattern, replacement in _REDACTION_PATTERNS:
            message = pattern.sub(replacement, message)
        message = _URL_QUERY_PATTERN.sub(r"\1?***", message)

        if message != original:
            record.msg = message
            record.args = ()
        return True


def configure_logging(
    *,
    level: str | int = "INFO",
    log_dir: Path | None = None,
    log_file_name: str = "focusguard.log",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
    console: bool = True,
) -> logging.Logger:
    """Configure the root logger. Safe to call more than once.

    Returns the ``focusguard`` logger, which every module should obtain via
    :func:`get_logger` rather than by calling ``logging.getLogger`` directly.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove handlers we previously installed so repeated calls (tests, restarts)
    # do not duplicate every line.
    for handler in list(root.handlers):
        if getattr(handler, "_focusguard_handler", False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DATE_FORMAT)
    redactor = RedactingFilter()

    if console:
        stream = logging.StreamHandler(stream=sys.stdout)
        stream.setFormatter(formatter)
        stream.addFilter(redactor)
        stream._focusguard_handler = True  # type: ignore[attr-defined]
        root.addHandler(stream)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            log_dir / log_file_name,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        rotating.addFilter(redactor)
        rotating._focusguard_handler = True  # type: ignore[attr-defined]
        root.addHandler(rotating)

    # These libraries are extremely chatty at DEBUG and would bury our own output.
    for noisy in ("urllib3", "PIL", "matplotlib", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger("focusguard")


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child of the ``focusguard`` logger."""
    return logging.getLogger(f"focusguard.{name}")
