"""Which contact channels a sentence asks for, and which it turns away from."""

from __future__ import annotations

import pytest

from nlp.entities import channel_stance, channels_mentioned


@pytest.mark.parametrize(
    ("text", "wanted", "avoided"),
    [
        ("Please contact the customer on WhatsApp instead of email.", ["WhatsApp"], ["email"]),
        ("Please do not call the customer, the customer prefers email.", ["email"], ["phone"]),
        ("We prefer WhatsApp over email", ["WhatsApp"], ["email"]),
        ("Call rather than email please", ["phone"], ["email"]),
        ("Not by email but by phone", ["phone"], ["email"]),
        ("Instead of email, use Slack", ["Slack"], ["email"]),
        ("No longer by phone; email only.", ["email"], ["phone"]),
        ("Stop emailing us.", [], ["email"]),
        ("Don't call, text messages are fine.", ["SMS"], ["phone"]),
        ("The customer prefers email or phone.", ["email", "phone"], []),
        # "teams" is a channel only as itself: no lemma turns "our team" into Microsoft Teams.
        ("We use Microsoft Teams and our team loves it", ["Microsoft Teams"], []),
        ("Nothing about contact here.", [], []),
    ],
)
def test_stance(text, wanted, avoided):
    assert channel_stance(text) == (wanted, avoided)


def test_mentioned_channels_keep_their_order():
    assert channels_mentioned("Please do not call the customer, the customer prefers email.") == ["phone", "email"]
    assert channels_mentioned("emails and calls") == ["email", "phone"]
