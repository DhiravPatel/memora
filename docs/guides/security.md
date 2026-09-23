# Security and privacy

## Tenancy

A project is the isolation boundary. An API key resolves to exactly one project and every
repository method takes `project_id`, so a cross-tenant read has to be a deliberate code
change rather than a forgotten filter. Dashboard users are scoped to their organization and
can only reach projects inside it.

## Credentials

* Passwords: bcrypt (cost 12) over a SHA-256 pre-hash, so long passwords are not truncated.
* API keys: HMAC-SHA256 with `API_KEY_SECRET`; only the digest and a display prefix are
  stored. A database leak yields no usable keys.
* Sessions: short-lived access JWTs plus refresh tokens; both are typed, so a refresh token
  cannot be used as an access token.
* Rotation invalidates the previous key immediately and writes an audit entry.

## Secrets that cannot be hashed

Passwords and API keys are stored as one-way hashes. Two things cannot be, because signing
and verification both need the value back: the outbound webhook secret and an inbound
integration's signing secret. Both are encrypted at rest with AES-256-GCM under
`SECRETS_ENCRYPTION_KEY`, which production refuses to start without. Hold it in a KMS and
inject it; a database dump is then not enough to forge a delivery or replay a provider's
webhook.

Generate one with:

```bash
python -c "from common.crypto import generate_key; print(generate_key())"
```

To rotate, move the current key into `PREVIOUS_ENCRYPTION_KEYS`, set the new one, and run
`python scripts/encrypt_secrets.py --apply`. It reads each value with whichever key wrote
it and rewrites it under the active one; once it reports nothing left to do, the old key
can be retired. The same command is how a deployment that adopted encryption late seals
the values it already had.

**Run the rewrite before retiring the old key.** A rotation is two steps, and a deployment
that does the first and forgets the second looks perfectly healthy — everything still
decrypts, because the old key is still listed. It stays healthy right up until somebody
removes it, at which point the secrets sealed under it are unrecoverable. The nightly
`check_key_rotation` job exists to catch exactly that: it reports both an overdue key
(`KEY_ROTATION_MAX_AGE_DAYS`, default 90) and a rotation that was started and abandoned,
and emails `OPS_ALERT_EMAIL` when either is true.

## Restricted memory

PII redaction removes what should never be stored. Restriction policies handle the other
problem: text that is legitimately stored and still must not go to everyone.

A project writes rules in **Settings → Engine → privacy**, or through
`PUT /v1/projects/{id}/settings`:

```json
{"settings": {"restriction_policies": [
  {"kind": "term", "value": ["salary", "bonus"], "label": "compensation"},
  {"kind": "type", "value": "feedback"},
  {"kind": "pattern", "value": "case\\s+no\\.?\\s*\\d+", "label": "legal matters"}
]}}
```

A matching memory is marked `restricted` as it is written — on every write path, including
manual writes, human corrections and agent session summaries — and changing the rules
re-classifies the project's history in the background.

Reading one needs **clearance**, which is granted and never inherited:

* an API key needs the `memory:restricted` scope, explicitly. `admin` does **not** confer
  it — the key a project is created with is an admin key, and it is the credential that
  ends up in every backend config;
* a dashboard user needs the `admin` role;
* the legacy `projects.api_key_hash` credential is authenticated but not cleared.

Without clearance a restricted memory is invisible everywhere — lists, `GET` by id,
answers, context blocks, search and exports — because the filter lives in the repository
rather than at the call sites. List endpoints return a `withheld` count so an incomplete
list never looks complete.

Restriction is lexical, not semantic: a term rule catches every inflection of a word, but
not a paraphrase of it. Where that matters, restrict the memory *type*.

## PII

`common.pii` detects and redacts emails, phone numbers, card-like numbers, SSNs, IPs and
provider secrets, and blanks known sensitive keys (`password`, `api_key`, `access_token`…).
Redaction happens on the way *into* the memory engine: the immutable event keeps what the
customer sent, while the text used for extraction and stored in memories is scrubbed.

Because no model provider is involved, customer text is never transmitted to a third
party at all: the only copies are the ones in your own database.
Disable per project with `pii_redaction_enabled` only if you have a reason to.

## Deletion

`DELETE /v1/customers/{id}` is a hard delete. Events, memories, versions, embeddings, graph
links, tracked goals, agent sessions and their transcripts, and signal snapshots all cascade
from the customer row, and the counts are recorded in the audit log.

Short of deletion, retention ages out the raw text on its own: finished agent conversations
are removed on the conversation window, while the summary memory the conversation produced
is kept under the memory window. An open session is never swept out from under an agent.

## Email

Invitations are sent over SMTP — no provider SDK, so switching from SES to Postmark is four
environment variables. Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` and
either `SMTP_STARTTLS` (port 587) or `SMTP_SSL` (port 465).

With no `SMTP_HOST`, messages are written to the log instead — including the invitation
link, which is all you need locally. Production refuses to start in that state, because a
deployment that silently swallows invitations is worse than one that will not boot. If you
genuinely want no mail, say so with `EMAIL_TRANSPORT=null`.

Mail is sent from a worker job, so a slow relay cannot slow down the dashboard and a dead
one cannot fail an invitation that is already committed. If the queue itself is
unreachable, the invite response says `email_queued: false` and the dashboard tells the
admin to send the link themselves.

The link points at `/accept-invitation` on the dashboard, so `EMAIL_LINK_BASE_URL` (or
`APP_URL`) has to be the address people can actually reach — not an internal hostname.

## Team and sessions

Roles are hierarchical (owner > admin > member > viewer). Invitations are single-use,
hashed and expire in 7 days. Removing a member deactivates them and bumps `token_version`,
which invalidates every token they hold immediately; the same mechanism backs password
changes and "sign out everywhere". Nobody can grant a role above their own, and an
organization can never lose its last owner.

## Outbound webhooks

Payloads are signed with a per-endpoint secret using `t=<unix>,v1=<hmac-sha256>` over
`"<timestamp>.<body>"`, with a five-minute tolerance window that makes replay useless. Both
SDKs ship verification helpers.

Webhook URLs are validated against SSRF: in production the URL must be `https`, must
resolve, and must not resolve to a private, loopback, link-local or reserved address.
Redirects are never followed. An endpoint that fails 20 times consecutively is deactivated
rather than retried forever.

Secrets are stored in plaintext because signing requires the shared value; rotate them from
the dashboard whenever a receiver changes hands.

## Audit log

Authentication, configuration changes, API key rotation, memory changes and every deletion
are recorded with actor, resource and IP. Read it at
`GET /v1/projects/{project_id}/audit-logs` (admin role required).

## Transport

Set `CORS_ORIGINS` to your dashboard origin — a wildcard is rejected in production.
`Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options` and
`Referrer-Policy` are set on every response. Terminate TLS at your proxy.
