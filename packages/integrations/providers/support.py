"""Support desks → internal events (Intercom, Zendesk, Slack).

Support text is where customers say what they actually want, so these connectors care
mostly about carrying the message body through intact and attaching it to the right
customer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from common.pii import redact_text
from integrations.base import NormalizedEvent, first_present, timestamp_from
from integrations.signature import verify_plain

MAX_MESSAGE_LENGTH = 4000


def _clean(text: Any) -> str:
    import re

    if not isinstance(text, str):
        return ""
    without_html = re.sub(r"<[^>]+>", " ", text)
    return " ".join(without_html.split())[:MAX_MESSAGE_LENGTH]


class IntercomIntegration:
    name = "intercom"
    signature_header = "X-Hub-Signature"

    TOPIC_MAP = {
        "conversation.user.created": "support_message",
        "conversation.user.replied": "support_message",
        "conversation.admin.replied": "support_reply_sent",
        "conversation.admin.closed": "support_ticket_closed",
        "contact.created": "profile_updated",
        "contact.tag.created": "profile_updated",
    }

    def verify(self, *, secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
        return verify_plain(
            secret=secret,
            raw_body=raw_body,
            provided=headers.get(self.signature_header.lower(), ""),
        )

    def normalize(self, payload: dict[str, Any]) -> Sequence[NormalizedEvent]:
        topic = str(payload.get("topic", ""))
        event_type = self.TOPIC_MAP.get(topic)
        if event_type is None:
            return []

        item = first_present(payload, "data.item", default={}) or {}
        contact = first_present(
            item, "user", "contacts.contacts.0", "source.author", default={}
        ) or {}
        customer_id = first_present(contact, "user_id", "external_id", "id")
        if not customer_id:
            return []

        message = _clean(
            first_present(item, "source.body", "conversation_message.body", "body", default="")
        )
        data: dict[str, Any] = {"intercom_topic": topic}
        if message:
            data["message"] = redact_text(message)
        subject = first_present(item, "source.subject", "title")
        if subject:
            data["subject"] = _clean(subject)
        if item.get("priority"):
            data["priority"] = item["priority"]
        if item.get("state"):
            data["state"] = item["state"]

        return [
            NormalizedEvent(
                customer_id=str(customer_id),
                event_type=event_type,
                data=data,
                external_event_id=str(payload.get("id") or item.get("id") or ""),
                occurred_at=timestamp_from(item.get("created_at") or payload.get("created_at")),
                customer_email=first_present(contact, "email"),
                customer_name=first_present(contact, "name"),
                source="intercom",
            )
        ]


class ZendeskIntegration:
    name = "zendesk"
    signature_header = "X-Zendesk-Webhook-Signature"

    def verify(self, *, secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
        return verify_plain(
            secret=secret,
            raw_body=raw_body,
            provided=headers.get(self.signature_header.lower(), ""),
            encoding="base64",
        )

    def normalize(self, payload: dict[str, Any]) -> Sequence[NormalizedEvent]:
        ticket = first_present(payload, "ticket", "detail", default={}) or {}
        requester = first_present(ticket, "requester", "via.source.from", default={}) or {}
        customer_id = first_present(
            requester, "external_id", "id", "email"
        ) or first_present(payload, "requester_id")
        if not customer_id:
            return []

        status = str(ticket.get("status", "")).lower()
        event_type = {
            "new": "support_ticket_created",
            "open": "support_message",
            "pending": "support_message",
            "solved": "support_ticket_resolved",
            "closed": "support_ticket_closed",
        }.get(status, "support_message")

        data: dict[str, Any] = {"zendesk_status": status or "unknown"}
        description = _clean(first_present(ticket, "description", "latest_comment.body", default=""))
        if description:
            data["message"] = redact_text(description)
        if ticket.get("subject"):
            data["subject"] = _clean(ticket["subject"])
        if ticket.get("priority"):
            data["priority"] = ticket["priority"]
        if ticket.get("id"):
            data["ticket_id"] = str(ticket["id"])

        return [
            NormalizedEvent(
                customer_id=str(customer_id),
                event_type=event_type,
                data=data,
                external_event_id=f"zendesk-{ticket.get('id')}-{ticket.get('updated_at', '')}".strip("-"),
                occurred_at=timestamp_from(ticket.get("updated_at") or ticket.get("created_at")),
                customer_email=first_present(requester, "email"),
                customer_name=first_present(requester, "name"),
                source="zendesk",
            )
        ]


class SlackIntegration:
    name = "slack"
    signature_header = "X-Slack-Signature"

    def verify(self, *, secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
        timestamp = headers.get("x-slack-request-timestamp", "")
        provided = headers.get(self.signature_header.lower(), "")
        if not timestamp or not provided:
            return False
        from integrations.signature import compare, hmac_sha256_hex

        expected = "v0=" + hmac_sha256_hex(secret, f"v0:{timestamp}:".encode() + raw_body)
        return compare(expected, provided)

    def normalize(self, payload: dict[str, Any]) -> Sequence[NormalizedEvent]:
        event = payload.get("event") or {}
        if event.get("type") != "message" or event.get("bot_id"):
            return []
        customer_id = first_present(event, "user", "channel")
        text = _clean(event.get("text", ""))
        if not customer_id or not text:
            return []
        return [
            NormalizedEvent(
                customer_id=str(customer_id),
                event_type="support_message",
                data={"message": redact_text(text), "channel": event.get("channel")},
                external_event_id=str(event.get("client_msg_id") or event.get("ts") or ""),
                occurred_at=timestamp_from(event.get("ts")),
                source="slack",
            )
        ]
