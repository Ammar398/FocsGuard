"""Outgoing email: verification and password-reset codes.

WHERE MAIL GOES
---------------
* **SMTP**, when `FOCUSGUARD_SMTP_HOST` is set - a real mail server, e.g. Gmail
  with an app password, Outlook, SendGrid or Mailgun SMTP.
* **A local outbox folder** otherwise (`FOCUSGUARD_DEV_OUTBOX`, default
  `dev_outbox/`). Each message is written as a `.eml` file you can double-click
  to open in Outlook or Mail. Nothing leaves the machine, so a development
  install can never email a real person by accident.
* **Memory**, in tests (`set_mailer(MemoryMailer())`).

The code itself is never written to the log - only that a message was sent
and where to. A code in a log file is a password reset in every backup of it.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Protocol

from focusguard_shared.utilities import get_logger

logger = get_logger("backend.services.email")


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str


class Mailer(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


def _sender() -> str:
    return os.environ.get("FOCUSGUARD_MAIL_FROM", "FocusGuard <no-reply@focusguard.local>")


def _mime(message: OutgoingEmail) -> MimeMessage:
    mime = MimeMessage()
    mime["From"] = _sender()
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime["Date"] = formatdate(localtime=True)
    mime["Message-ID"] = make_msgid(domain="focusguard.local")
    mime.set_content(message.text)
    mime.add_alternative(message.html, subtype="html")
    return mime


class SmtpMailer:
    """Sends through an SMTP server. STARTTLS on 587 by default, SSL on 465."""

    def __init__(self) -> None:
        self.host = os.environ["FOCUSGUARD_SMTP_HOST"]
        self.port = int(os.environ.get("FOCUSGUARD_SMTP_PORT", "587"))
        self.username = os.environ.get("FOCUSGUARD_SMTP_USERNAME")
        self.password = os.environ.get("FOCUSGUARD_SMTP_PASSWORD")
        self.use_ssl = self.port == 465 or os.environ.get("FOCUSGUARD_SMTP_SSL") == "1"

    def send(self, message: OutgoingEmail) -> None:
        context = ssl.create_default_context()
        if self.use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self.host, self.port, context=context, timeout=20
            )
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=20)
        with server:
            if not self.use_ssl:
                server.starttls(context=context)
            if self.username:
                server.login(self.username, self.password or "")
            server.send_message(_mime(message))
        logger.info("Email sent via SMTP to %s", message.to)


class OutboxMailer:
    """Writes each message to a folder instead of sending it (development)."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(os.environ.get("FOCUSGUARD_DEV_OUTBOX", "dev_outbox"))

    def send(self, message: OutgoingEmail) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        recipient = "".join(ch if ch.isalnum() else "_" for ch in message.to)[:60]
        path = self.directory / f"{stamp}-{recipient}.eml"
        path.write_bytes(bytes(_mime(message)))
        logger.info("No SMTP configured; email to %s saved to %s", message.to, path.resolve())


@dataclass
class MemoryMailer:
    """Keeps messages in a list. For tests."""

    messages: list[OutgoingEmail] = field(default_factory=list)

    def send(self, message: OutgoingEmail) -> None:
        self.messages.append(message)


_mailer: Mailer | None = None


def get_mailer() -> Mailer:
    global _mailer
    if _mailer is None:
        _mailer = SmtpMailer() if os.environ.get("FOCUSGUARD_SMTP_HOST") else OutboxMailer()
    return _mailer


def set_mailer(mailer: Mailer | None) -> None:
    """Replace the mailer (tests), or None to re-read the environment."""
    global _mailer
    _mailer = mailer


# --- templates ---------------------------------------------------------------


def code_email(to: str, code: str, purpose: str, minutes: int) -> OutgoingEmail:
    """A branded message carrying a six-digit code."""
    if purpose == "RESET_PASSWORD":
        heading = "Reset your password"
        lead = "Use this code to choose a new FocusGuard password."
        ignore = "If you did not ask to reset your password, ignore this email - nothing changes."
        subject = f"{code} is your FocusGuard password reset code"
    else:
        heading = "Confirm your email address"
        lead = "Use this code to finish creating your FocusGuard account."
        ignore = "If you did not create a FocusGuard account, ignore this email."
        subject = f"{code} is your FocusGuard verification code"

    spaced = f"{code[:3]} {code[3:]}"
    text = (
        f"{heading}\n\n{lead}\n\n    {spaced}\n\n"
        f"The code expires in {minutes} minutes and works once.\n\n{ignore}\n\n"
        "FocusGuard never asks for your password by email.\n"
    )
    html = f"""<!doctype html>
<html><body style="margin:0;background:#f5f6fa;
  font-family:Segoe UI,system-ui,sans-serif;color:#0f172a">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 12px">
    <tr><td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0"
             style="max-width:480px;background:#ffffff;border-radius:16px;overflow:hidden;
                    border:1px solid #e4e7ee">
        <tr><td style="background:linear-gradient(135deg,#6250d6,#2a78d6);padding:22px 28px;
                       color:#ffffff;font-size:18px;font-weight:600">FocusGuard</td></tr>
        <tr><td style="padding:28px">
          <h1 style="margin:0 0 8px;font-size:20px">{heading}</h1>
          <p style="margin:0 0 22px;color:#475569;font-size:14px">{lead}</p>
          <div style="font-size:32px;font-weight:700;letter-spacing:8px;text-align:center;
                      background:#efedfc;color:#4b3cc4;border-radius:12px;padding:16px 0">
            {spaced}
          </div>
          <p style="margin:22px 0 0;color:#475569;font-size:13px">
            The code expires in {minutes} minutes and works once.</p>
          <p style="margin:8px 0 0;color:#64748b;font-size:13px">{ignore}</p>
        </td></tr>
        <tr><td style="padding:16px 28px;border-top:1px solid #e4e7ee;color:#64748b;font-size:12px">
          FocusGuard never asks for your password by email.</td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return OutgoingEmail(to=to, subject=subject, text=text, html=html)
