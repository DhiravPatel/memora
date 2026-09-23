"""The messages this product sends, as plain functions.

No template engine: there are three of them, they change rarely, and an f-string that
cannot fail to render is worth more here than a templating system that can. Every template
returns ``(subject, text, html)`` and the text version is always complete on its own — it
is what lands in a terminal client, a screen reader, and a spam-filter score.

Anything interpolated into the HTML goes through :func:`_escape` first. The values are
operator-supplied (an organization name, an inviter's name), which is exactly the kind of
input that is trusted right up until somebody names their organization ``<script>``.
"""

from __future__ import annotations

from html import escape as _html_escape

# Kept inline: a mail client that strips <style> is the rule, not the exception.
_WRAP = "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.6"
_MUTED = "color:#6b6b6b;font-size:13px"
_BUTTON = (
    "display:inline-block;padding:10px 18px;background:#c8102e;color:#ffffff;"
    "text-decoration:none;font-weight:600;border-radius:2px"
)


def _escape(value: str) -> str:
    return _html_escape(value, quote=True)


def invitation(
    *,
    organization: str,
    inviter: str,
    role: str,
    accept_url: str,
    expires_in_days: int,
) -> tuple[str, str, str]:
    """Somebody has been invited to an organization."""
    subject = f"{inviter} invited you to {organization}"
    text = f"""{inviter} has invited you to join {organization} as a {role}.

Accept the invitation:
{accept_url}

The link works once and expires in {expires_in_days} days. If you were not expecting this,
you can ignore this message — nothing happens until the link is opened.
"""
    html = f"""<div style="{_WRAP}">
  <p>{_escape(inviter)} has invited you to join <strong>{_escape(organization)}</strong>
     as a {_escape(role)}.</p>
  <p style="margin:24px 0"><a href="{_escape(accept_url)}" style="{_BUTTON}">Accept invitation</a></p>
  <p style="{_MUTED}">The link works once and expires in {expires_in_days} days.
     If you were not expecting this you can ignore this message — nothing happens until the
     link is opened.</p>
  <p style="{_MUTED}">{_escape(accept_url)}</p>
</div>"""
    return subject, text, html


def invitation_revoked(*, organization: str) -> tuple[str, str, str]:
    """An invitation was withdrawn before it was accepted."""
    subject = f"Your invitation to {organization} was withdrawn"
    text = f"""The invitation to join {organization} has been withdrawn, and the link in the
earlier message no longer works.

If you think this is a mistake, ask whoever invited you to send a new one.
"""
    html = f"""<div style="{_WRAP}">
  <p>The invitation to join <strong>{_escape(organization)}</strong> has been withdrawn, and
     the link in the earlier message no longer works.</p>
  <p style="{_MUTED}">If you think this is a mistake, ask whoever invited you to send a new
     one.</p>
</div>"""
    return subject, text, html


def key_rotation_due(*, environment: str, age_days: int, threshold_days: int) -> tuple[str, str, str]:
    """The secrets encryption key is older than the deployment's rotation interval."""
    subject = f"[{environment}] Secrets encryption key is {age_days} days old"
    text = f"""The secrets encryption key for the {environment} deployment was last rotated
{age_days} days ago, past the {threshold_days}-day interval this deployment is configured for.

Rotate it with:

    python scripts/encrypt_secrets.py rotate --new-key <new key>

The script is idempotent and safe to run against a live system: it re-encrypts each stored
secret in place, and a run that is interrupted can simply be run again.
"""
    html = f"""<div style="{_WRAP}">
  <p>The secrets encryption key for the <strong>{_escape(environment)}</strong> deployment was
     last rotated <strong>{age_days} days</strong> ago, past the {threshold_days}-day interval
     this deployment is configured for.</p>
  <p>Rotate it with:</p>
  <pre style="background:#f4f4f4;padding:12px;font-size:12px;overflow:auto">python scripts/encrypt_secrets.py rotate --new-key &lt;new key&gt;</pre>
  <p style="{_MUTED}">The script is idempotent and safe to run against a live system: it
     re-encrypts each stored secret in place, and an interrupted run can simply be run again.</p>
</div>"""
    return subject, text, html
