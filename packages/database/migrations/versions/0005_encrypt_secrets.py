"""Encrypt webhook and integration secrets at rest.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-21

Widens ``webhook_endpoints.secret`` to hold ciphertext and rewrites existing secrets — the
outbound signing secrets and the inbound ``integrations.<provider>.signing_secret`` values
inside ``projects.settings`` — under ``SECRETS_ENCRYPTION_KEY``.

Safe either way. With no key configured the schema change still applies and the values are
left as they were, because the column type treats unrecognised values as plaintext, and
each row becomes ciphertext the next time it is written. To encrypt the rest after the
fact — Alembic will not run an applied migration again — use
``python scripts/encrypt_secrets.py --apply``, which is also how a key is rotated.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _key():
    """The active encryption key, or ``None`` when encryption is not configured."""
    from common.settings import get_settings

    keys = get_settings().encryption_keys
    return keys[0] if keys else None


def upgrade() -> None:
    op.alter_column(
        "webhook_endpoints",
        "secret",
        existing_type=sa.String(128),
        type_=sa.String(512),
        existing_nullable=False,
    )

    key = _key()
    if key is None:
        print(
            "0005: SECRETS_ENCRYPTION_KEY is not set — secrets left as they are. "
            "Set the key, then run: python scripts/encrypt_secrets.py --apply"
        )
        return

    from common.crypto import encrypt, is_encrypted

    connection = op.get_bind()

    rows = connection.execute(
        sa.text("SELECT id, secret FROM webhook_endpoints WHERE secret IS NOT NULL")
    ).fetchall()
    encrypted = 0
    for row_id, secret in rows:
        if is_encrypted(secret):
            continue
        connection.execute(
            sa.text("UPDATE webhook_endpoints SET secret = :secret WHERE id = :id"),
            {"secret": encrypt(secret, key), "id": row_id},
        )
        encrypted += 1

    # Inbound provider secrets live inside the project's settings JSON.
    projects = connection.execute(
        sa.text("SELECT id, settings FROM projects WHERE settings ? 'integrations'")
    ).fetchall()
    providers = 0
    for project_id, settings in projects:
        settings = settings if isinstance(settings, dict) else json.loads(settings or "{}")
        integrations = settings.get("integrations") or {}
        changed = False
        for provider, config in integrations.items():
            if not isinstance(config, dict):
                continue
            secret = config.get("signing_secret")
            if not secret or is_encrypted(secret):
                continue
            config["signing_secret"] = encrypt(str(secret), key)
            integrations[provider] = config
            changed = True
            providers += 1
        if changed:
            settings["integrations"] = integrations
            connection.execute(
                sa.text("UPDATE projects SET settings = :settings WHERE id = :id"),
                {"settings": json.dumps(settings), "id": project_id},
            )

    print(f"0005: encrypted {encrypted} webhook secret(s) and {providers} provider secret(s)")


def downgrade() -> None:
    """Decrypt in place, then narrow the column back.

    Without this a downgrade would leave the previous version reading ciphertext as if it
    were a signing secret, and every delivery would be signed with garbage.
    """
    key = _key()
    if key is not None:
        from common.crypto import decrypt, is_encrypted

        connection = op.get_bind()
        rows = connection.execute(
            sa.text("SELECT id, secret FROM webhook_endpoints WHERE secret IS NOT NULL")
        ).fetchall()
        for row_id, secret in rows:
            if not is_encrypted(secret):
                continue
            connection.execute(
                sa.text("UPDATE webhook_endpoints SET secret = :secret WHERE id = :id"),
                {"secret": decrypt(secret, [key]), "id": row_id},
            )

        projects = connection.execute(
            sa.text("SELECT id, settings FROM projects WHERE settings ? 'integrations'")
        ).fetchall()
        for project_id, settings in projects:
            settings = settings if isinstance(settings, dict) else json.loads(settings or "{}")
            integrations = settings.get("integrations") or {}
            changed = False
            for provider, config in integrations.items():
                if not isinstance(config, dict):
                    continue
                secret = config.get("signing_secret")
                if not secret or not is_encrypted(secret):
                    continue
                config["signing_secret"] = decrypt(str(secret), [key])
                integrations[provider] = config
                changed = True
            if changed:
                settings["integrations"] = integrations
                connection.execute(
                    sa.text("UPDATE projects SET settings = :settings WHERE id = :id"),
                    {"settings": json.dumps(settings), "id": project_id},
                )

    op.alter_column(
        "webhook_endpoints",
        "secret",
        existing_type=sa.String(512),
        type_=sa.String(128),
        existing_nullable=False,
    )
