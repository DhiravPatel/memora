"""Invitation email and the key-rotation check, against a real database.

The unit tests cover what a message looks like; these cover the two things that need rows:
that an invitation renders the link the invitee will actually use, and that the rotation
check can tell a finished rotation from an abandoned one.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.conftest import database_required

from common.crypto import derive_key, encrypt
from common.enums import UserRole
from common.ids import new_id
from common.settings import get_settings
from common.time import utcnow
from database import models  # noqa: F401  (registers tables)
from database.base import Base
from database.models import Project
from database.repositories import (
    InvitationRepository,
    OrganizationRepository,
    ProjectRepository,
    UserRepository,
    WebhookEndpointRepository,
)
from database.rotation import count_stale_secrets, record_key_use, rotation_status
from worker.tasks.email import render_invitation

pytestmark = [pytest.mark.e2e, database_required]

KEY = derive_key("k" * 48)
RETIRED = derive_key("r" * 48)


@pytest.fixture(scope="module")
def schema():
    settings = get_settings()
    engine = create_engine(settings.sync_database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)
    yield
    with engine.begin() as connection:
        Base.metadata.drop_all(connection)
    engine.dispose()


@pytest.fixture
async def session(schema):
    engine = create_async_engine(get_settings().database_url, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as active:
        yield active
        await active.rollback()
    await engine.dispose()


async def _organization(session, name: str = "Acme"):
    organizations = OrganizationRepository(session)
    organization = await organizations.create(name=name, slug=f"{name.lower()}-{new_id('x')[-8:]}")
    user = await UserRepository(session).create(
        organization_id=organization.id,
        email=f"dana-{new_id('x')[-8:]}@example.com",
        password_hash="hash",
        name="Dana",
        role=UserRole.OWNER,
    )
    await session.flush()
    return organization, user


async def _sealed_webhook(session, organization_id: str, *, key) -> str:
    """A project with one webhook whose secret is sealed under ``key``.

    The secret is written with raw SQL because the column type encrypts on the way in —
    writing through the ORM would seal it under whatever key this process holds, which is
    the one thing the test must control.
    """
    project = await ProjectRepository(session).create(
        organization_id=organization_id,
        name="P",
        api_key_hash=new_id("hash"),
        api_key_prefix="mk_test",
    )
    endpoint = await WebhookEndpointRepository(session).create(
        project_id=project.id, url="https://x.test/hook", secret="placeholder"
    )
    await session.execute(
        text("UPDATE webhook_endpoints SET secret = :secret WHERE id = :id"),
        {"secret": encrypt("s3cret", key), "id": endpoint.id},
    )
    await session.flush()
    return endpoint.id


# ------------------------------------------------------------------- invitations


async def test_an_invitation_renders_the_link_the_invitee_will_use(session):
    organization, inviter = await _organization(session)
    invitation = await InvitationRepository(session).create(
        organization_id=organization.id,
        email="new@example.com",
        role=UserRole.MEMBER,
        token_hash="hashed",
        invited_by=inviter.id,
    )

    message = await render_invitation(
        session, invitation.id, "raw-token", base_url="https://app.example.com/"
    )

    assert message is not None
    assert message.to == "new@example.com"
    # The link is the entire point of the mail: it has to be exact, and in both bodies.
    assert "https://app.example.com/accept-invitation?token=raw-token" in message.text
    assert "https://app.example.com/accept-invitation?token=raw-token" in message.html
    assert "Dana" in message.subject
    assert organization.name in message.subject


async def test_a_revoked_invitation_is_not_sent(session):
    """Between the enqueue and the send, an admin can change their mind."""
    organization, inviter = await _organization(session)
    invitations = InvitationRepository(session)
    invitation = await invitations.create(
        organization_id=organization.id,
        email="new@example.com",
        role=UserRole.MEMBER,
        token_hash="hashed",
        invited_by=inviter.id,
    )
    await invitations.revoke(invitation)

    assert await render_invitation(session, invitation.id, "t", base_url="https://x") is None


async def test_an_invitation_that_no_longer_exists_is_not_an_error(session):
    assert await render_invitation(session, "inv_missing", "t", base_url="https://x") is None


async def test_an_invitation_without_an_inviter_still_sends(session):
    """``invited_by`` is nullable, and a message with a dangling name is worse than none."""
    organization, _ = await _organization(session)
    invitation = await InvitationRepository(session).create(
        organization_id=organization.id,
        email="new@example.com",
        role=UserRole.VIEWER,
        token_hash="hashed",
    )
    message = await render_invitation(session, invitation.id, "t", base_url="https://x")
    assert message is not None
    assert "An administrator" in message.subject


# ---------------------------------------------------------------- key rotation


async def test_a_key_is_dated_from_the_first_time_it_is_seen(session):
    first = await record_key_use(session, KEY.id)
    original = first.first_seen_at

    again = await record_key_use(session, KEY.id)

    assert again.first_seen_at == original  # not moved by a later check
    assert again.last_seen_at >= original


async def test_the_age_is_measured_from_that_date(session):
    record = await record_key_use(session, KEY.id)
    record.first_seen_at = utcnow() - timedelta(days=200)
    await session.flush()

    status = await rotation_status(session, active=KEY, max_age_days=90)

    assert status.age_days == 200
    assert status.overdue


async def test_a_half_finished_rotation_is_visible(session):
    """New key in place, rewrite never run: the state that loses secrets on cleanup."""
    organization, _ = await _organization(session)
    await _sealed_webhook(session, organization.id, key=RETIRED)

    assert await count_stale_secrets(session, KEY.id) == 1

    status = await rotation_status(session, active=KEY, max_age_days=90)
    assert status.unfinished
    assert status.needs_attention
    # The key itself is brand new here — an age check alone would have said nothing.
    assert not status.overdue


async def test_a_finished_rotation_is_quiet(session):
    organization, _ = await _organization(session)
    await _sealed_webhook(session, organization.id, key=KEY)

    status = await rotation_status(session, active=KEY, max_age_days=90)
    assert not status.needs_attention


async def test_a_provider_signing_secret_counts_too(session):
    """Webhook secrets are not the only thing sealed; the inbound ones rotate as well."""
    organization, _ = await _organization(session)
    project = await ProjectRepository(session).create(
        organization_id=organization.id,
        name="P",
        api_key_hash=new_id("hash"),
        api_key_prefix="mk_test",
    )
    await session.execute(
        update(Project.__table__)
        .where(Project.__table__.c.id == project.id)
        .values(settings={"integrations": {"stripe": {"signing_secret": encrypt("s", RETIRED)}}})
    )
    await session.flush()

    assert await count_stale_secrets(session, KEY.id) == 1
