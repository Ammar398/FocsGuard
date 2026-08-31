"""Log redaction tests (spec section 50).

Secrets must be stripped by a filter that runs over every record, because
relying on developers to remember not to log a token is how tokens reach logs.
"""

from __future__ import annotations

import logging

import pytest

from focusguard_shared.utilities.logging_setup import RedactingFilter


def _redact(message: str, *args: object) -> str:
    record = logging.LogRecord(
        name="focusguard.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args or (),
        exc_info=None,
    )
    RedactingFilter().filter(record)
    return record.getMessage()


@pytest.mark.parametrize(
    "message",
    [
        "login password=hunter2 ok",
        'payload {"token": "abc123xyz"}',
        "refresh_token: eyJhbGciOiJIUzI1NiJ9",
        "enrollment_token=DEVICE-SECRET-42",
        "api_key = sk-live-9f8e7d",
    ],
)
def test_credentials_are_redacted(message: str) -> None:
    result = _redact(message)
    assert "***REDACTED***" in result
    secrets = (
        "hunter2",
        "abc123xyz",
        "eyJhbGciOiJIUzI1NiJ9",
        "DEVICE-SECRET-42",
        "sk-live-9f8e7d",
    )
    for secret in secrets:
        assert secret not in result


def test_bearer_tokens_are_redacted() -> None:
    result = _redact("Authorization header was Bearer eyJhbGciOi.J9.sig")
    assert "eyJhbGciOi.J9.sig" not in result
    assert "Bearer ***REDACTED***" in result


def test_url_query_strings_are_stripped() -> None:
    """Query strings carry search terms and must never be logged."""
    result = _redact("navigated to https://google.com/search?q=private+medical+question")
    assert "private" not in result
    assert "https://google.com/search?***" in result


def test_lazy_format_arguments_are_also_redacted() -> None:
    """Redaction must survive %-style formatting, not only literal strings."""
    result = _redact("device registered token=%s", "SUPER-SECRET")
    assert "SUPER-SECRET" not in result


def test_ordinary_messages_are_untouched() -> None:
    message = "Focus score 82.4 on session 41 with provider DmlExecutionProvider"
    assert _redact(message) == message


def test_broken_format_string_does_not_crash_logging() -> None:
    """A logging bug must never take down the agent."""
    record = logging.LogRecord(
        name="focusguard.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="bad format %d %d",
        args=("only-one",),
        exc_info=None,
    )
    assert RedactingFilter().filter(record) is True
