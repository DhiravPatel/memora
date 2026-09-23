"""Outbound email: a provider-agnostic SMTP transport and the messages sent over it."""

from integrations.email import templates
from integrations.email.mailer import (
    ConsoleMailer,
    EmailError,
    Mailer,
    Message,
    NullMailer,
    SmtpMailer,
    build_mailer,
)

__all__ = [
    "ConsoleMailer",
    "EmailError",
    "Mailer",
    "Message",
    "NullMailer",
    "SmtpMailer",
    "build_mailer",
    "templates",
]
