#!/usr/bin/env python
"""Seed the demo data: John's Shopify story plus a small portfolio of other customers.

    python scripts/seed_demo.py

Creates an organization, a project (printing its API key once), the six events from the
README, and four more customers with different health profiles, processes them through the
memory engine, tracks their goals, records a signal snapshot and plays back one agent
conversation, then asks the questions the dashboard playground asks.

Safe to re-run: events already seeded are skipped by their external id.

Requires a migrated database and Redis is *not* needed: events are processed inline.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.core.security import api_key_prefix, generate_api_key, hash_api_key, hash_password
from common.enums import MemorySource, MemoryType, TurnRole
from common.logging import configure_logging
from common.settings import get_settings
from common.time import utcnow
from database.repositories import (
    AgentSessionRepository,
    AgentTurnRepository,
    CustomerRepository,
    EventRepository,
    MemoryRepository,
    OrganizationRepository,
    ProjectRepository,
    UserRepository,
)
from database.session import dispose_engine, transactional_session
from memory_engine import MemoryEngine
from nlp import LocalEmbedder

STORY = [
    (11, "subscription_upgraded", {"plan": "Pro", "previous_plan": "Starter"}),
    (7, "integration_connected", {"integration": "Shopify"}),
    (4, "integration_failed", {"integration": "Shopify", "error": "OAuth handshake timed out"}),
    (3, "support_message", {"message": "My Shopify sync stopped working after I upgraded."}),
    (1, "support_message", {"message": "I've tried connecting Shopify three times but it still doesn't work."}),
    (0, "subscription_downgraded", {"plan": "Starter", "previous_plan": "Pro",
                                    "reason": "Integration never worked reliably."}),
]

# A few more customers so the dashboard has a portfolio rather than a single row:
# one thriving, one quietly churning, one brand new, one with a resolved problem.
PORTFOLIO: list[tuple[str, str, str, list[tuple[int, str, dict]]]] = [
    (
        "cus_ada", "Ada Okonkwo", "ada@northwind.example",
        [
            (40, "profile_updated", {"company": "Northwind Traders", "role": "Head of Growth",
                                     "industry": "retail"}),
            (35, "integration_connected", {"integration": "Stripe"}),
            (30, "feature_used", {"feature": "campaign_builder"}),
            (21, "goal_created", {"goal": "launch weekly campaigns to 50k subscribers",
                                  "due_date": "2026-12-01"}),
            (12, "support_message", {"message": "We are setting up the weekly campaign schedule "
                                                "for our 50k subscribers now."}),
            (6, "support_message", {"message": "Weekly campaigns to all 50k subscribers are now "
                                               "live, it went out this morning."}),
            (14, "feature_used", {"feature": "report_builder"}),
            (9, "feedback_submitted", {"feedback": "The new reporting is excellent and the team "
                                                   "loves how fast exports are now."}),
            (5, "subscription_upgraded", {"plan": "Enterprise", "previous_plan": "Pro"}),
            (1, "feature_used", {"feature": "audience_segments"}),
        ],
    ),
    (
        "cus_marco", "Marco Bianchi", "marco@vela.example",
        [
            (60, "subscription_upgraded", {"plan": "Pro", "previous_plan": "Starter"}),
            (52, "integration_connected", {"integration": "HubSpot"}),
            (47, "support_message", {"message": "The HubSpot sync is painfully slow and we are "
                                                "considering switching to a competitor."}),
            (44, "nps_submitted", {"score": 4}),
            (40, "support_message", {"message": "Please contact me on WhatsApp instead of email."}),
        ],
    ),
    (
        "cus_priya", "Priya Raman", "priya@lumen.example",
        [
            (6, "signup_completed", {"company": "Lumen Labs", "role": "Founder"}),
            (5, "integration_failed", {"integration": "QuickBooks",
                                       "error": "Invalid credentials"}),
            (4, "support_message", {"message": "QuickBooks will not connect, it keeps rejecting "
                                               "our credentials."}),
            (2, "support_message", {"message": "It works now, thanks for fixing it so quickly."}),
            (1, "feature_used", {"feature": "invoice_import"}),
        ],
    ),
    (
        "cus_dana", "Dana Ellis", "dana@brightfold.example",
        [
            (3, "signup_completed", {"company": "Brightfold", "role": "Operations Manager"}),
            (2, "feature_used", {"feature": "campaign_builder"}),
            (1, "support_message", {"message": "We want to migrate our whole contact list from "
                                               "Mailchimp before the end of the month."}),
        ],
    ),
]

# One replayed conversation, so the Agent Sessions page has a transcript to open and the
# next session for this customer starts with continuity rather than a blank slate.
AGENT_CONVERSATION: tuple[str, ...] = (
    "The Shopify sync stopped again this morning and orders are not coming through.",
    "This is the third time this month and it is blocking our fulfilment team.",
    "If it is not fixed before the weekend we will have to look at alternatives.",
)


async def seed_agent_session(session, project, customer) -> None:
    """Replay one closed conversation, summary memory and all."""
    sessions = AgentSessionRepository(session)
    if await sessions.get_by_external_id("demo-conv-1", project.id) is not None:
        print("  agent session      already seeded")
        return

    now = utcnow()
    agent_session = await sessions.create(
        project_id=project.id,
        customer_id=customer.id,
        agent="support-bot",
        external_id="demo-conv-1",
        channel="chat",
    )
    turns = AgentTurnRepository(session)
    for index, content in enumerate(AGENT_CONVERSATION):
        await turns.add(
            session_id=agent_session.id,
            project_id=project.id,
            role=TurnRole.USER,
            content=content,
            occurred_at=now - timedelta(days=1, minutes=10 - index * 3),
        )
        await sessions.touch(agent_session)
        await turns.add(
            session_id=agent_session.id,
            project_id=project.id,
            role=TurnRole.AGENT,
            content="Thanks — I can see the failed syncs. Raising this with engineering now.",
            occurred_at=now - timedelta(days=1, minutes=9 - index * 3),
        )
        await sessions.touch(agent_session)

    from app.services.agent_service import compose_summary

    transcript = await turns.list(session_id=agent_session.id, project_id=project.id)
    summary = compose_summary(transcript, outcome="Escalated to engineering")
    memory = await MemoryRepository(session).create(
        project_id=project.id,
        customer_id=customer.id,
        type=MemoryType.SUMMARY,
        content=summary or "",
        importance=0.55,
        confidence=0.6,
        source=MemorySource.AGENT,
        metadata={"agent_session_id": agent_session.id, "agent": "support-bot"},
    )
    await sessions.close(agent_session, summary=summary, summary_memory_id=memory.id)
    print(f"  agent session      closed with a {len(transcript)}-turn transcript")


async def main() -> None:
    settings = get_settings()
    configure_logging(level="INFO", json_output=False)

    embedder = LocalEmbedder(settings=settings)

    async with transactional_session() as session:
        organizations = OrganizationRepository(session)
        organization = await organizations.get_by_slug("demo")
        if organization is None:
            organization = await organizations.create(name="Demo Co", slug="demo")
            await UserRepository(session).create(
                organization_id=organization.id,
                email="demo@example.com",
                password_hash=hash_password("demo-password"),
                name="Demo User",
            )
            print("Dashboard login: demo@example.com / demo-password")

        projects = ProjectRepository(session)
        existing = await projects.list_for_organization(organization.id)
        # Prefer the project this script created before; the organization may well have
        # others, and seeding into an unrelated one is confusing rather than harmless.
        project = next((item for item in existing if item.name == "Demo"), None)
        if project is not None:
            print(f"Reusing project {project.name} ({project.id})")
        else:
            api_key = generate_api_key("test")
            project = await projects.create(
                organization_id=organization.id,
                name="Demo",
                api_key_hash=hash_api_key(api_key),
                api_key_prefix=api_key_prefix(api_key),
            )
            print(f"API key (shown once): {api_key}")

        customer = await CustomerRepository(session).upsert(
            project_id=project.id, external_id="cus_123", name="John", email="john@demo.test"
        )

        engine = MemoryEngine(session=session, embedder=embedder, settings=settings)
        events = EventRepository(session)
        now = utcnow()

        for days_ago, event_type, data in STORY:
            external_id = f"demo-{event_type}-{days_ago}"
            if await events.get_by_external_id(external_id, project.id) is not None:
                print(f"  {event_type:<26} already seeded")
                continue
            event = await events.create(
                project_id=project.id,
                customer_id=customer.id,
                event_type=event_type,
                data=data,
                occurred_at=now - timedelta(days=days_ago),
                external_event_id=external_id,
                importance=0.0,
            )
            result = await engine.process_event(event=event, project=project)
            status = "processed" if result.processed else f"skipped ({result.skipped_reason})"
            print(f"  {event_type:<26} {status}")

        # Portfolio customers, so Health, Entities and Customers have something to show.
        for external_id, name, email, story in PORTFOLIO:
            other = await CustomerRepository(session).upsert(
                project_id=project.id, external_id=external_id, name=name, email=email
            )
            processed = 0
            skipped = 0
            for days_ago, event_type, data in story:
                external_event_id = f"demo-{external_id}-{event_type}-{days_ago}"
                if await events.get_by_external_id(external_event_id, project.id) is not None:
                    skipped += 1
                    continue
                event = await events.create(
                    project_id=project.id,
                    customer_id=other.id,
                    event_type=event_type,
                    data=data,
                    occurred_at=now - timedelta(days=days_ago),
                    external_event_id=external_event_id,
                    importance=0.0,
                )
                outcome = await engine.process_event(event=event, project=project)
                processed += 1 if outcome.processed else 0
            suffix = f" ({skipped} already seeded)" if skipped else ""
            print(f"  {name:<16} {processed}/{len(story)} events produced memories{suffix}")

        # Goals and forecasts for everyone, exactly as the worker does after an event.
        all_customers, _ = await CustomerRepository(session).list(project_id=project.id, limit=50)
        for record in all_customers:
            await engine.refresh_health(project=project, customer=record)
            refresh = await engine.refresh_goals(project=project, customer=record)
            report = await engine.signals_for(project=project, customer=record)
            await engine.record_signals(project=project, customer=record, report=report)
            if refresh.all:
                moved = ", ".join(f"{change.status}" for change in refresh.all)
                print(f"  {record.name or record.external_id:<16} goals: {moved}")
            print(
                f"  {record.name or record.external_id:<16} "
                f"{report.trajectory}, churn risk {report.churn_risk:.2f}"
            )

        await seed_agent_session(session, project, customer)

        for question in (
            "Why did this customer downgrade?",
            "What problems has this customer experienced recently?",
            "Are they at risk of churning?",
        ):
            answer = await engine.answer(project=project, customer=customer, query=question)
            print(f"\nQ: {question}")
            print(f"A: {answer.answer}")
            print(f"   (strategy {answer.trace['answer_strategy']}, confidence {answer.confidence:.2f})")

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
