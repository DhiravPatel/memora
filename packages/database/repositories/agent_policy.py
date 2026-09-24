"""Agent profiles, guardrail checks and approvals."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, text, update

from common.ids import new_id
from common.time import utcnow
from database.models import AgentAction, AgentApproval, AgentCheck, AgentProfile, ApiKey
from database.repositories.base import BaseRepository

PENDING, APPROVED, REJECTED, EXPIRED, USED = "pending", "approved", "rejected", "expired", "used"
OPEN_STATUSES = (PENDING, APPROVED)

# An action's life (§26 4.5).
ACTION_ALLOWED, ACTION_PENDING, ACTION_DENIED = "allowed", "pending_approval", "denied"
ACTION_DONE, ACTION_FAILED, ACTION_CANCELLED, ACTION_EXPIRED = "done", "failed", "cancelled", "expired"
# What counts as the customer's action history: cleared to go ahead, or reported done. An
# allowed action nobody reported back on counts — for a limit, assuming it happened is the
# safe side.
ACTION_TAKEN = (ACTION_ALLOWED, ACTION_DONE)


class AgentProfileRepository(BaseRepository):
    async def create(
        self,
        *,
        project_id: str,
        name: str,
        description: str | None,
        readable_types: Sequence[str],
        can_read_restricted: bool,
        allowed_actions: Sequence[str],
        denied_actions: Sequence[str],
    ) -> AgentProfile:
        profile = AgentProfile(
            id=new_id("agp"),
            project_id=project_id,
            name=name,
            description=description,
            readable_types=list(readable_types),
            can_read_restricted=can_read_restricted,
            allowed_actions=list(allowed_actions),
            denied_actions=list(denied_actions),
        )
        self.session.add(profile)
        await self.session.flush()
        return profile

    async def get(self, profile_id: str, project_id: str) -> AgentProfile | None:
        result = await self.session.execute(
            select(AgentProfile).where(
                AgentProfile.id == profile_id, AgentProfile.project_id == project_id
            )
        )
        return result.scalar_one_or_none()

    async def get_any(self, profile_id: str) -> AgentProfile | None:
        """By id alone — for authentication, where the key has already fixed the project."""
        return await self.session.get(AgentProfile, profile_id)

    async def get_by_name(self, project_id: str, name: str) -> AgentProfile | None:
        result = await self.session.execute(
            select(AgentProfile).where(
                AgentProfile.project_id == project_id, AgentProfile.name == name
            )
        )
        return result.scalar_one_or_none()

    async def list(self, project_id: str) -> list[AgentProfile]:
        result = await self.session.execute(
            select(AgentProfile)
            .where(AgentProfile.project_id == project_id)
            .order_by(AgentProfile.name)
        )
        return list(result.scalars())

    async def update(self, profile: AgentProfile, **fields: Any) -> AgentProfile:
        for name, value in fields.items():
            setattr(profile, name, value)
        profile.updated_at = utcnow()
        await self.session.flush()
        return profile

    async def delete(self, profile: AgentProfile) -> None:
        await self.session.delete(profile)
        await self.session.flush()

    async def key_counts(self, project_id: str) -> dict[str, int]:
        """How many live keys act as each profile."""
        result = await self.session.execute(
            select(ApiKey.agent_profile_id, func.count())
            .where(
                ApiKey.project_id == project_id,
                ApiKey.agent_profile_id.is_not(None),
                ApiKey.revoked_at.is_(None),
                or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > utcnow()),
            )
            .group_by(ApiKey.agent_profile_id)
        )
        return {profile_id: int(count) for profile_id, count in result}


class AgentCheckRepository(BaseRepository):
    async def record(
        self,
        *,
        project_id: str,
        customer_id: str,
        action: str,
        request: dict[str, Any],
        decision: str,
        reasons: list[dict[str, Any]],
        evidence: list[str],
        agent: str | None = None,
        api_key_id: str | None = None,
        approval_id: str | None = None,
        snapshot_id: str | None = None,
        session_id: str | None = None,
    ) -> AgentCheck:
        check = AgentCheck(
            id=new_id("chk"),
            project_id=project_id,
            customer_id=customer_id,
            agent=agent,
            api_key_id=api_key_id,
            action=action,
            request=request,
            decision=decision,
            reasons=reasons,
            evidence=evidence,
            approval_id=approval_id,
            snapshot_id=snapshot_id,
            session_id=session_id,
            created_at=utcnow(),
        )
        self.session.add(check)
        await self.session.flush()
        return check

    async def get(self, check_id: str, project_id: str) -> AgentCheck | None:
        result = await self.session.execute(
            select(AgentCheck).where(AgentCheck.id == check_id, AgentCheck.project_id == project_id)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        project_id: str,
        customer_id: str | None = None,
        decision: str | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AgentCheck], int]:
        conditions = [AgentCheck.project_id == project_id]
        if customer_id:
            conditions.append(AgentCheck.customer_id == customer_id)
        if decision:
            conditions.append(AgentCheck.decision == decision)
        if agent:
            conditions.append(AgentCheck.agent == agent)
        if session_id:
            conditions.append(AgentCheck.session_id == session_id)
        if since:
            conditions.append(AgentCheck.created_at >= since)
        if until:
            conditions.append(AgentCheck.created_at <= until)
        total = await self.session.scalar(select(func.count()).select_from(AgentCheck).where(*conditions))
        result = await self.session.execute(
            select(AgentCheck)
            .where(*conditions)
            .order_by(AgentCheck.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def counts_by_decision(self, project_id: str, *, since: datetime) -> dict[str, int]:
        result = await self.session.execute(
            select(AgentCheck.decision, func.count())
            .where(AgentCheck.project_id == project_id, AgentCheck.created_at >= since)
            .group_by(AgentCheck.decision)
        )
        return {str(decision): int(count) for decision, count in result}

    async def top_rules(self, project_id: str, *, since: datetime, limit: int = 8) -> list[tuple[str, int]]:
        """The rules that objected most often — what agents keep running into."""
        rows = await self.session.execute(
            text(
                """
                SELECT r->>'rule' AS rule, count(*) AS n
                FROM agent_checks c, jsonb_array_elements(c.reasons) AS r
                WHERE c.project_id = :project AND c.created_at >= :since
                GROUP BY 1 ORDER BY n DESC LIMIT :limit
                """
            ),
            {"project": project_id, "since": since, "limit": limit},
        )
        return [(str(rule), int(count)) for rule, count in rows]


class AgentApprovalRepository(BaseRepository):
    async def create(
        self,
        *,
        project_id: str,
        customer_id: str,
        check_id: str,
        action: str,
        request: dict[str, Any],
        reasons: list[dict[str, Any]],
        expires_at: datetime,
        agent: str | None = None,
    ) -> AgentApproval:
        approval = AgentApproval(
            id=new_id("apr"),
            project_id=project_id,
            customer_id=customer_id,
            check_id=check_id,
            agent=agent,
            action=action,
            request=request,
            reasons=reasons,
            status=PENDING,
            expires_at=expires_at,
            created_at=utcnow(),
        )
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def get(
        self, approval_id: str, project_id: str, *, for_update: bool = False
    ) -> AgentApproval | None:
        """``for_update`` locks the row: redeeming and deciding must not race each other."""
        statement = select(AgentApproval).where(
            AgentApproval.id == approval_id, AgentApproval.project_id == project_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def open_for(
        self, *, project_id: str, customer_id: str, action: str, agent: str | None
    ) -> list[AgentApproval]:
        """Live requests for the same action on the same customer — so a retrying agent
        reuses its pending approval instead of filing a new one each time."""
        result = await self.session.execute(
            select(AgentApproval)
            .where(
                AgentApproval.project_id == project_id,
                AgentApproval.customer_id == customer_id,
                AgentApproval.action == action,
                AgentApproval.status.in_(OPEN_STATUSES),
                AgentApproval.expires_at > utcnow(),
                AgentApproval.agent.is_(None) if agent is None else AgentApproval.agent == agent,
            )
            .order_by(AgentApproval.created_at.desc())
        )
        return list(result.scalars())

    async def list(
        self,
        *,
        project_id: str,
        status: str | None = None,
        customer_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AgentApproval], int]:
        conditions = [AgentApproval.project_id == project_id]
        if status:
            conditions.append(AgentApproval.status == status)
        if customer_id:
            conditions.append(AgentApproval.customer_id == customer_id)
        total = await self.session.scalar(
            select(func.count()).select_from(AgentApproval).where(*conditions)
        )
        result = await self.session.execute(
            select(AgentApproval)
            .where(*conditions)
            .order_by(AgentApproval.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def counts_by_status(self, project_id: str) -> dict[str, int]:
        result = await self.session.execute(
            select(AgentApproval.status, func.count())
            .where(AgentApproval.project_id == project_id)
            .group_by(AgentApproval.status)
        )
        return {str(status): int(count) for status, count in result}

    async def expire_due(self, *, now: datetime | None = None, project_id: str | None = None) -> list[AgentApproval]:
        """Mark every open approval past its expiry as expired, and return them."""
        now = now or utcnow()
        conditions = [AgentApproval.status.in_(OPEN_STATUSES), AgentApproval.expires_at <= now]
        if project_id:
            conditions.append(AgentApproval.project_id == project_id)
        result = await self.session.execute(
            update(AgentApproval)
            .where(*conditions)
            .values(status=EXPIRED)
            .returning(AgentApproval)
            .execution_options(synchronize_session=False)
        )
        return list(result.scalars())


class AgentActionRepository(BaseRepository):
    async def create(
        self,
        *,
        project_id: str,
        customer_id: str,
        action: str,
        request: dict[str, Any],
        amount: float | None,
        status: str,
        decision: str,
        check_id: str | None,
        approval_id: str | None,
        agent: str | None,
        api_key_id: str | None,
        session_id: str | None,
        idempotency_key: str | None,
    ) -> AgentAction:
        now = utcnow()
        record = AgentAction(
            id=new_id("act"),
            project_id=project_id,
            customer_id=customer_id,
            action=action,
            request=request,
            amount=amount,
            status=status,
            decision=decision,
            check_id=check_id,
            approval_id=approval_id,
            agent=agent,
            api_key_id=api_key_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get(self, action_id: str, project_id: str, *, for_update: bool = False) -> AgentAction | None:
        statement = select(AgentAction).where(AgentAction.id == action_id, AgentAction.project_id == project_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def by_idempotency_key(self, project_id: str, key: str) -> AgentAction | None:
        result = await self.session.execute(
            select(AgentAction).where(AgentAction.project_id == project_id, AgentAction.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def set_status(self, record: AgentAction, status: str, **fields: Any) -> AgentAction:
        record.status = status
        for name, value in fields.items():
            setattr(record, name, value)
        record.updated_at = utcnow()
        await self.session.flush()
        return record

    async def list(
        self,
        *,
        project_id: str,
        customer_id: str | None = None,
        action: str | None = None,
        status: str | None = None,
        agent: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AgentAction], int]:
        conditions = [AgentAction.project_id == project_id]
        if customer_id:
            conditions.append(AgentAction.customer_id == customer_id)
        if action:
            conditions.append(AgentAction.action == action)
        if status:
            conditions.append(AgentAction.status == status)
        if agent:
            conditions.append(AgentAction.agent == agent)
        if since:
            conditions.append(AgentAction.created_at >= since)
        total = await self.session.scalar(select(func.count()).select_from(AgentAction).where(*conditions))
        result = await self.session.execute(
            select(AgentAction).where(*conditions).order_by(AgentAction.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def history(
        self, *, project_id: str, customer_id: str, since: datetime
    ) -> list[tuple[str, datetime, float | None]]:
        """(action, when, amount) for everything taken since ``since`` — the raw material
        of the customer's ``actions.*`` facts."""
        result = await self.session.execute(
            select(AgentAction.action, AgentAction.created_at, AgentAction.amount).where(
                AgentAction.project_id == project_id,
                AgentAction.customer_id == customer_id,
                AgentAction.status.in_(ACTION_TAKEN),
                AgentAction.created_at >= since,
            )
        )
        return [(str(action), created_at, amount) for action, created_at, amount in result]

    async def pending_for_approvals(self, project_id: str, approval_ids: Sequence[str]) -> list[AgentAction]:
        if not approval_ids:
            return []
        result = await self.session.execute(
            select(AgentAction).where(
                AgentAction.project_id == project_id,
                AgentAction.approval_id.in_(list(approval_ids)),
                AgentAction.status == ACTION_PENDING,
            )
        )
        return list(result.scalars())

    async def counts_by_status(self, project_id: str, *, since: datetime) -> dict[str, int]:
        result = await self.session.execute(
            select(AgentAction.status, func.count())
            .where(AgentAction.project_id == project_id, AgentAction.created_at >= since)
            .group_by(AgentAction.status)
        )
        return {str(status): int(count) for status, count in result}
