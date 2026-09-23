"""Outbound email: what gets built, what gets escaped, and which transport is chosen.

Sending is not tested here — that is SMTP, and a test of ``smtplib`` tests the standard
library. What is tested is everything this project decided: the message shape, the
escaping, the transport selection, and the failure wrapping the retry logic depends on.
"""

from __future__ import annotations

import smtplib
from unittest.mock import patch

import pytest

from common.settings import Settings
from integrations.email import (
    ConsoleMailer,
    EmailError,
    Message,
    NullMailer,
    SmtpMailer,
    build_mailer,
    templates,
)

# ------------------------------------------------------------------- message shape


def test_a_message_carries_text_and_html_as_alternatives():
    """An HTML-only mail is unreadable in a terminal client and scores worst with filters."""
    mime = Message(
        to="someone@example.com", subject="Hello", text="plain body", html="<p>rich body</p>"
    ).as_mime(sender="no-reply@memora.test", sender_name="Memora")

    assert mime["From"] == "Memora <no-reply@memora.test>"
    assert mime["To"] == "someone@example.com"
    assert mime.is_multipart()
    parts = {part.get_content_type(): part.get_content() for part in mime.iter_parts()}
    assert parts["text/plain"].strip() == "plain body"
    assert "rich body" in parts["text/html"]


def test_a_text_only_message_is_not_multipart():
    mime = Message(to="a@example.com", subject="s", text="body").as_mime(sender="x@example.com")
    assert not mime.is_multipart()
    assert mime.get_content().strip() == "body"


def test_every_message_has_an_id_and_optional_reply_to():
    mime = Message(
        to="a@example.com", subject="s", text="b", reply_to="team@example.com"
    ).as_mime(sender="x@example.com")
    assert mime["Message-ID"]
    assert mime["Reply-To"] == "team@example.com"


def test_a_sender_without_a_name_is_a_bare_address():
    mime = Message(to="a@example.com", subject="s", text="b").as_mime(sender="x@example.com")
    assert mime["From"] == "x@example.com"


# ---------------------------------------------------------------------- templates


def test_an_invitation_carries_the_link_in_both_bodies():
    subject, text, html = templates.invitation(
        organization="Acme",
        inviter="Dana",
        role="member",
        accept_url="https://app.example.com/accept-invitation?token=abc",
        expires_in_days=7,
    )
    assert "Dana" in subject and "Acme" in subject
    # The plain part has to be complete on its own: it is what a terminal client shows.
    assert "https://app.example.com/accept-invitation?token=abc" in text
    assert "https://app.example.com/accept-invitation?token=abc" in html
    assert "7 days" in text


@pytest.mark.parametrize(
    "field",
    ["organization", "inviter", "role"],
)
def test_an_invitation_escapes_what_an_operator_typed(field: str):
    """An organization called ``<script>`` is a stored XSS in whoever's webmail opens it."""
    values = {
        "organization": "Acme",
        "inviter": "Dana",
        "role": "member",
        "accept_url": "https://app.example.com/a",
        "expires_in_days": 7,
    }
    values[field] = "<script>alert(1)</script>"
    _, _, html = templates.invitation(**values)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_an_invitation_url_is_escaped_in_the_href():
    _, _, html = templates.invitation(
        organization="Acme",
        inviter="Dana",
        role="member",
        accept_url='https://app.example.com/a?t=x"><script>alert(1)</script>',
        expires_in_days=7,
    )
    assert "<script>" not in html


def test_the_rotation_warning_says_what_to_run():
    subject, text, html = templates.key_rotation_due(
        environment="production", age_days=140, threshold_days=90
    )
    assert "production" in subject and "140" in subject
    assert "encrypt_secrets.py" in text
    assert "encrypt_secrets.py" in html


# --------------------------------------------------------------------- transports


def test_no_smtp_host_means_the_console_transport():
    """A fresh clone must be able to run the whole invitation flow with no mail account."""
    assert build_mailer(Settings(smtp_host="")).name == "console"


def test_a_deployment_can_say_it_wants_no_mail():
    """Distinct from having none by accident, which is what production refuses."""
    assert build_mailer(Settings(email_transport="null", smtp_host="smtp.example.com")).name == "null"


def test_production_refuses_to_start_with_mail_it_did_not_choose():
    def problems(**overrides) -> str:
        settings = Settings(
            app_env="production",
            jwt_secret="j" * 40,
            api_key_secret="a" * 40,
            secrets_encryption_key="s" * 40,
            cors_origins=["https://app.example.com"],
            **overrides,
        )
        return " ".join(settings.check_production_safety())

    assert "SMTP_HOST" in problems()
    assert "SMTP_HOST" not in problems(email_transport="null")
    assert "SMTP_HOST" not in problems(smtp_host="smtp.example.com")
    # Console in production writes single-use invitation links into the log stream.
    assert "console" in problems(email_transport="console", smtp_host="smtp.example.com")


def test_an_smtp_host_means_a_real_relay():
    mailer = build_mailer(
        Settings(
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_ssl=True,
            smtp_username="user",
            smtp_password="pass",
            email_from="hello@example.com",
            email_from_name="Memora",
        )
    )
    assert isinstance(mailer, SmtpMailer)
    assert (mailer.host, mailer.port, mailer.use_ssl) == ("smtp.example.com", 465, True)
    assert mailer.sender == "hello@example.com"


async def test_the_null_transport_records_what_it_was_given():
    mailer = NullMailer()
    await mailer.send(Message(to="a@example.com", subject="s", text="b"))
    assert [message.to for message in mailer.sent] == ["a@example.com"]


async def test_the_console_transport_logs_the_body():
    """Whoever runs this transport is running it *for* the link in the body."""
    mailer = ConsoleMailer()
    with patch("integrations.email.mailer.logger") as logger:
        await mailer.send(Message(to="a@example.com", subject="s", text="the link is here"))
    assert logger.info.call_args.kwargs["body"] == "the link is here"


async def test_an_smtp_failure_is_wrapped_so_the_job_can_retry_on_it():
    mailer = SmtpMailer(host="smtp.example.com")
    with (
        patch.object(SmtpMailer, "_deliver", side_effect=smtplib.SMTPServerDisconnected("gone")),
        pytest.raises(EmailError, match="SMTPServerDisconnected"),
    ):
        await mailer.send(Message(to="a@example.com", subject="s", text="b"))


async def test_a_connection_refused_is_also_an_email_error():
    """OSError, not SMTPException — and a dead relay is the common case."""
    mailer = SmtpMailer(host="127.0.0.1", port=1)
    with (
        patch.object(SmtpMailer, "_deliver", side_effect=ConnectionRefusedError("refused")),
        pytest.raises(EmailError),
    ):
        await mailer.send(Message(to="a@example.com", subject="s", text="b"))


# ------------------------------------------------------- the link has to go somewhere


def test_the_accept_link_points_at_a_page_that_exists():
    """An invitation link that 404s is the one bug nobody reports.

    The person who clicks it has no account yet and no way to tell anyone. So the path the
    API hands out is checked against the dashboard's routes here, where a rename shows up
    as a failing test rather than as silence.
    """
    import re
    from pathlib import Path

    from app.services.team_service import CreatedInvitation

    path = CreatedInvitation(invitation=None, token="x").accept_path  # type: ignore[arg-type]
    route = re.sub(r"\?.*$", "", path).strip("/")

    app_dir = Path(__file__).resolve().parents[2] / "apps" / "web" / "app"
    assert (app_dir / route / "page.tsx").is_file(), (
        f"The API links to /{route}, but apps/web/app/{route}/page.tsx does not exist."
    )
