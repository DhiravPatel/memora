"""Sending mail, without taking on a mail provider.

The dashboard's invitations were the last thing that needed a human to copy a link out of
an API response and paste it into their own mail client. This module closes that, and does
it with the standard library: SMTP is the one interface every provider — SES, Postmark,
Resend, Mailgun, a corporate Exchange relay — exposes, so speaking SMTP means the choice of
provider is four environment variables rather than a dependency and an integration.

Three transports:

* :class:`SmtpMailer` — a real relay, used whenever ``SMTP_HOST`` is set.
* :class:`ConsoleMailer` — logs the message instead of sending it. The default in
  development, so a fresh clone can exercise the whole invitation flow with no account
  anywhere, and the link is in the log where it is needed.
* :class:`NullMailer` — accepts and discards. For tests that care about the caller.

``smtplib`` is synchronous, so the send runs in a worker thread. It happens inside a worker
job rather than a request, so the latency is nobody's problem; what matters is that it
cannot block the event loop of the process it happens to run in.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol

from common.logging import get_logger
from common.settings import Settings, get_settings

logger = get_logger(__name__)

# A relay that has not answered in this long is down, not slow.
DEFAULT_TIMEOUT_SECONDS = 15.0


class EmailError(RuntimeError):
    """The message could not be handed to the relay."""


@dataclass(slots=True)
class Message:
    """One email. Plain text is required; HTML is an alternative, never the only copy."""

    to: str
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def as_mime(self, *, sender: str, sender_name: str | None = None) -> EmailMessage:
        message = EmailMessage()
        message["From"] = formataddr((sender_name, sender)) if sender_name else sender
        message["To"] = self.to
        message["Subject"] = self.subject
        message["Message-ID"] = make_msgid()
        if self.reply_to:
            message["Reply-To"] = self.reply_to
        for name, value in self.headers.items():
            message[name] = value
        # Text first: an HTML-only invitation is unreadable in a terminal client, and is
        # what a spam filter scores worst.
        message.set_content(self.text)
        if self.html:
            message.add_alternative(self.html, subtype="html")
        return message


class Mailer(Protocol):
    """Anything that can deliver a :class:`Message`."""

    name: str

    async def send(self, message: Message) -> None: ...


@dataclass(slots=True)
class ConsoleMailer:
    """Logs the message. The development default, and a usable one.

    The body is logged in full, including any link it carries, because the entire reason
    somebody is running this transport is that they need that link.
    """

    name: str = "console"

    async def send(self, message: Message) -> None:
        logger.info(
            "email.console",
            to=message.to,
            subject=message.subject,
            body=message.text,
        )


@dataclass(slots=True)
class NullMailer:
    """Accepts and discards. For tests, and for a deployment that wants no mail at all."""

    name: str = "null"
    sent: list[Message] = field(default_factory=list)

    async def send(self, message: Message) -> None:
        self.sent.append(message)


@dataclass(slots=True)
class SmtpMailer:
    """A real relay, over SMTP.

    ``use_ssl`` is implicit TLS (port 465); ``starttls`` upgrades a plaintext connection
    (port 587). One or the other — never neither, unless the relay is on localhost, which
    is the one case where an unencrypted hop is not a credential on the wire.
    """

    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    use_ssl: bool = False
    starttls: bool = True
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    sender: str = "no-reply@localhost"
    sender_name: str | None = None
    name: str = "smtp"

    async def send(self, message: Message) -> None:
        mime = message.as_mime(sender=self.sender, sender_name=self.sender_name)
        try:
            await asyncio.to_thread(self._deliver, mime)
        except (smtplib.SMTPException, OSError) as exc:
            # Wrapped so callers need not know which library raised: the job retries on
            # EmailError and gives up on anything it cannot classify.
            raise EmailError(f"{type(exc).__name__}: {exc}") from exc
        logger.info("email.sent", to=message.to, subject=message.subject, transport="smtp")

    def _deliver(self, mime: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self.use_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=context
            )
        else:
            client = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        try:
            client.ehlo()
            if not self.use_ssl and self.starttls:
                client.starttls(context=context)
                client.ehlo()
            if self.username:
                client.login(self.username, self.password or "")
            client.send_message(mime)
        finally:
            try:
                client.quit()
            except smtplib.SMTPException:
                # The message is already delivered at this point; a relay that hangs up
                # rudely on QUIT is not a failed send.
                client.close()


def build_mailer(settings: Settings | None = None) -> Mailer:
    """The transport this deployment is configured for.

    ``SMTP_HOST`` is the switch: set it and mail is sent, leave it unset and mail is
    logged. ``EMAIL_TRANSPORT`` overrides that either way — in particular ``null``, which
    is how a deployment says it wants no mail rather than ending up with none by accident.
    Production refuses to start in the accidental case.
    """
    settings = settings or get_settings()
    if settings.email_transport == "null":
        return NullMailer()
    if settings.email_transport == "console" or not settings.smtp_host:
        return ConsoleMailer()
    return SmtpMailer(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username or None,
        password=settings.smtp_password or None,
        use_ssl=settings.smtp_ssl,
        starttls=settings.smtp_starttls,
        timeout=settings.smtp_timeout_seconds,
        sender=settings.email_from,
        sender_name=settings.email_from_name or None,
    )
