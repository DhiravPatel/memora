#!/usr/bin/env python
"""Encrypt (or re-encrypt) the secrets that cannot be hashed.

    python scripts/encrypt_secrets.py            # report what would change
    python scripts/encrypt_secrets.py --apply    # write it

Two jobs:

* **Turning encryption on** after the fact. Migration ``0005`` encrypts whatever it finds,
  but only on the one occasion it runs — a deployment that set ``SECRETS_ENCRYPTION_KEY``
  afterwards needs this instead, because Alembic will not run an applied migration again.
* **Rotating a key**. Put the old key in ``PREVIOUS_ENCRYPTION_KEYS`` and the new one in
  ``SECRETS_ENCRYPTION_KEY``, then run this: every value is read with whichever key wrote
  it and rewritten under the active one. Once it reports nothing left to do, the old key
  can be retired.

Idempotent, and safe to run against a live system: each row is an independent update, and
a value already sealed under the active key is left untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select, text, update

from common.crypto import decrypt, encrypt, is_encrypted, needs_rewrite
from common.logging import configure_logging
from common.settings import get_settings
from database.models import Project
from database.session import dispose_engine, transactional_session


async def main(apply: bool) -> int:
    configure_logging(level="INFO", json_output=False)
    settings = get_settings()
    keys = settings.encryption_keys
    if not keys:
        print(
            "SECRETS_ENCRYPTION_KEY is not set. Nothing can be encrypted without it.\n"
            "Generate one with:  python -c 'from common.crypto import generate_key; "
            "print(generate_key())'"
        )
        return 1

    active = keys[0]
    verb = "Rewriting" if apply else "Would rewrite"
    webhooks = providers = 0

    async with transactional_session() as session:
        # --- outbound webhook signing secrets ------------------------------------------
        # Raw SQL on purpose. The column type decrypts on the way out — through the ORM
        # *and* through Core — so a typed read would hand back plaintext and hide which key
        # each row is actually sealed under, which is the one thing this needs to know.
        rows = (await session.execute(text("SELECT id, secret FROM webhook_endpoints"))).all()
        for endpoint_id, stored in rows:
            if not needs_rewrite(stored or "", active.id):
                continue
            plaintext = decrypt(stored, keys) if is_encrypted(stored) else stored
            print(f"  {verb} webhook secret for {endpoint_id}")
            webhooks += 1
            if apply:
                # Written raw for the same reason: the value is already ciphertext.
                await session.execute(
                    text("UPDATE webhook_endpoints SET secret = :secret WHERE id = :id"),
                    {"secret": encrypt(plaintext, active), "id": endpoint_id},
                )

        # --- inbound provider signing secrets ------------------------------------------
        projects = (await session.execute(select(Project))).scalars().all()
        for project in projects:
            settings_json = dict(project.settings or {})
            integrations = dict(settings_json.get("integrations") or {})
            changed = False
            for provider, config in integrations.items():
                if not isinstance(config, dict):
                    continue
                stored = config.get("signing_secret")
                if not stored or not needs_rewrite(str(stored), active.id):
                    continue
                plaintext = decrypt(str(stored), keys) if is_encrypted(str(stored)) else str(stored)
                print(f"  {verb} {provider} signing secret for {project.id}")
                providers += 1
                changed = True
                if apply:
                    integrations[provider] = {
                        **config,
                        "signing_secret": encrypt(plaintext, active),
                    }
            if changed and apply:
                settings_json["integrations"] = integrations
                project.settings = settings_json
                # JSONB mutation is not tracked; reassigning the whole document is.
                await session.execute(
                    update(Project.__table__)
                    .where(Project.__table__.c.id == project.id)
                    .values(settings=json.loads(json.dumps(settings_json)))
                )

        if not apply:
            await session.rollback()

    total = webhooks + providers
    if not total:
        print(f"Nothing to do: every secret is already sealed under key {active.id}.")
    elif apply:
        print(f"Rewrote {webhooks} webhook secret(s) and {providers} provider secret(s).")
    else:
        print(f"{total} secret(s) would be rewritten. Re-run with --apply to do it.")

    await dispose_engine()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the changes.")
    raise SystemExit(asyncio.run(main(parser.parse_args().apply)))
