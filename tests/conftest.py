"""Suite-wide test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_email():
    """Keep every test's email in memory.

    Without this, a test that registers an account would use the real mailer:
    SMTP if the developer's .env configures one - emailing whatever address the
    test made up - or the dev outbox folder otherwise. Tests that inspect the
    mail install their own MemoryMailer on top of this one.
    """
    from focusguard_backend.services.email import MemoryMailer, set_mailer

    set_mailer(MemoryMailer())
    yield
    set_mailer(None)
