"""Sending mail, off the request path.

An invitation must not be able to fail because a relay is slow, and an admin clicking
"Invite" must not wait on SMTP. So the API commits the invitation and enqueues this; the
job builds the message from ids rather than carrying a rendered body through Redis, which
keeps the queue payload small and means a template fix applies to mail that is still
queued.

Retries are arq's: a relay refusing a message right now usually accepts it in a minute.
A permanently bad address burns its three tries and is then logged and dropped, because
there is nobody to tell — the address is the thing that is wrong.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import InvitationStatus, UserRole
from common.logging import get_logger
from common.settings import get_settings
from database.repositories import InvitationRepository, OrganizationRepository, UserRepository
from integrations.email import EmailError, Message, build_mailer, templates
from worker.tasks.context import worker_session

logger = get_logger(__name__)


def _mailer(ctx: dict[str, Any]):
    """One mailer per worker process; it holds no connection between sends."""
    mailer = ctx.get("mailer")
    if mailer is None:
        mailer = ctx["mailer"] = build_mailer()
    return mailer


async def send_email(
    ctx: dict[str, Any], to: str, subject: str, text: str, html: str | None = None
) -> dict[str, Any]:
    """Deliver one already-rendered message. The bottom of every mail path."""
    mailer = _mailer(ctx)
    try:
        await mailer.send(Message(to=to, subject=subject, text=text, html=html))
    except EmailError as exc:
        logger.warning("email.failed", to=to, subject=subject, error=str(exc))
        raise
    return {"to": to, "transport": mailer.name}


async def render_invitation(
    session: AsyncSession, invitation_id: str, token: str, *, base_url: str
) -> Message | None:
    """The invitation as a message, or ``None`` if it should no longer be sent.

    Split from the job so the rendering — which is where the link, the expiry and the
    already-revoked check live — can be tested on a caller's session.

    The token is passed in because only the caller ever holds it; the row stores a hash,
    which is the point. That does mean the raw token sits in Redis until the job runs, so
    it is never logged, and the invitation expires on its own schedule regardless.
    """
    invitation = await InvitationRepository(session).get_any(invitation_id)
    if invitation is None:
        return None
    # ``==`` and not ``is``: the status column is a String, so a loaded row gives a plain
    # ``str`` and identity against the enum member is always false.
    if InvitationStatus(invitation.status) != InvitationStatus.PENDING:
        # Revoked or accepted between the enqueue and now. Sending would hand out a link
        # that no longer works, which reads as a broken product.
        return None

    organization = await OrganizationRepository(session).get(invitation.organization_id)
    inviter = (
        await UserRepository(session).get(invitation.invited_by) if invitation.invited_by else None
    )
    subject, text, html = templates.invitation(
        organization=organization.name if organization else "your team",
        inviter=(inviter.name or inviter.email) if inviter else "An administrator",
        role=UserRole(invitation.role).value,
        accept_url=f"{base_url.rstrip('/')}/accept-invitation?token={token}",
        expires_in_days=max(1, (invitation.expires_at - invitation.created_at).days),
    )
    return Message(to=invitation.email, subject=subject, text=text, html=html)


async def send_invitation_email(
    ctx: dict[str, Any], invitation_id: str, token: str
) -> dict[str, Any]:
    """Render and send an invitation."""
    settings = get_settings()
    async with worker_session() as session:
        message = await render_invitation(
            session, invitation_id, token, base_url=settings.link_base_url
        )
    if message is None:
        return {"invitation_id": invitation_id, "status": "skipped"}
    return await send_email(ctx, message.to, message.subject, message.text, message.html)
