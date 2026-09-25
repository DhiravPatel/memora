"""Enumerations shared by the database models, the memory engine and the API schemas."""

from __future__ import annotations

from enum import StrEnum


class MemoryType(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    PROBLEM = "problem"
    GOAL = "goal"
    BEHAVIOR = "behavior"
    RELATIONSHIP = "relationship"
    SUBSCRIPTION = "subscription"
    FEEDBACK = "feedback"
    INTENT = "intent"
    SUMMARY = "summary"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    DELETED = "deleted"


class Sensitivity(StrEnum):
    """Whether a memory may be read without clearance.

    Set by the project's own restriction policy when the memory is written, and enforced
    on every read path — including the ones that compose answers, where a leak would be
    invisible.
    """

    NORMAL = "normal"
    RESTRICTED = "restricted"


class MemorySource(StrEnum):
    EVENT = "event"
    CONSOLIDATION = "consolidation"
    MANUAL = "manual"
    IMPORT = "import"
    # Written when an agent session is closed: the continuity a later session reads.
    AGENT = "agent"


class EventStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    SKIPPED = "skipped"
    FAILED = "failed"


class EntityType(StrEnum):
    CUSTOMER = "customer"
    COMPANY = "company"
    PRODUCT = "product"
    FEATURE = "feature"
    INTEGRATION = "integration"
    SUBSCRIPTION = "subscription"
    SUPPORT_TICKET = "support_ticket"
    EMPLOYEE = "employee"
    CONVERSATION = "conversation"
    CAMPAIGN = "campaign"
    ORDER = "order"
    OTHER = "other"


class RelationshipType(StrEnum):
    USES = "uses"
    OWNS = "owns"
    HAS_PROBLEM = "has_problem"
    CONTACTED = "contacted"
    SUBSCRIBED_TO = "subscribed_to"
    WORKS_AT = "works_at"
    PURCHASED = "purchased"
    INTERESTED_IN = "interested_in"
    RELATED_TO = "related_to"


class GoalStatus(StrEnum):
    """Where a stated customer goal has got to.

    A goal is opened when the customer says what they are trying to do, and closed only by
    later evidence — never by a timer alone, which is why ``STALLED`` exists as a separate
    state from ``ABANDONED``.
    """

    OPEN = "open"
    PROGRESSING = "progressing"
    ACHIEVED = "achieved"
    STALLED = "stalled"
    ABANDONED = "abandoned"

    @property
    def is_closed(self) -> bool:
        return self in (GoalStatus.ACHIEVED, GoalStatus.ABANDONED)


class SignalDirection(StrEnum):
    RISK = "risk"
    OPPORTUNITY = "opportunity"


class Trajectory(StrEnum):
    """Which way a customer is moving, as distinct from where they are now."""

    IMPROVING = "improving"
    STEADY = "steady"
    DECLINING = "declining"


class AgentSessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    EXPIRED = "expired"


class TurnRole(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class VocabularySource(StrEnum):
    """Where a vocabulary pair came from, which decides who may overwrite it."""

    MINED = "mined"
    CURATED = "curated"


class VocabularyStatus(StrEnum):
    ACTIVE = "active"
    # A mined pair a person threw out. Kept as a row so mining cannot relearn it.
    REJECTED = "rejected"


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return {"viewer": 0, "member": 1, "admin": 2, "owner": 3}[self.value]

    def can(self, required: UserRole) -> bool:
        return self.rank >= required.rank


class ConsolidationAction(StrEnum):
    CREATE = "create"
    MERGE = "merge"
    UPDATE = "update"
    CONFLICT = "conflict"
    IGNORE = "ignore"


class ApiKeyScope(StrEnum):
    """What an API key is allowed to do.

    Keys are least-privilege by default: an ingestion key on a web server should not be
    able to read a customer's memories, and an agent key should not be able to delete them.
    """

    EVENTS_WRITE = "events:write"
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    # Clearance to read memories the project's policy marked restricted. Deliberately
    # absent from ``defaults()``: a key gets it only when somebody decides it should.
    MEMORY_RESTRICTED = "memory:restricted"
    CUSTOMERS_READ = "customers:read"
    CUSTOMERS_WRITE = "customers:write"
    # Approve or reject an action an agent asked permission for (§26 3.3). Like clearance
    # it is a grant, not a consequence of admin — see ``not_implied_by_admin``.
    APPROVALS_DECIDE = "approvals:decide"
    ADMIN = "admin"

    @classmethod
    def defaults(cls) -> list[ApiKeyScope]:
        return [cls.EVENTS_WRITE, cls.MEMORY_READ, cls.CUSTOMERS_READ]

    @classmethod
    def not_implied_by_admin(cls) -> frozenset[ApiKeyScope]:
        """Scopes the ``admin`` wildcard does not confer.

        Clearance to read restricted memory is a grant, not a consequence of power. The
        key that a project is created with is an admin key, and it is the one that ends up
        pasted into backend configuration everywhere; if admin implied clearance, the
        single most widely-copied credential in a deployment would be able to read exactly
        the content a restriction policy exists to protect. So it has to be asked for.

        Deciding approvals is the same kind of grant for the same reason: the admin key is
        exactly the key an agent's backend is most likely to hold, and an agent that could
        approve its own requests would make the approval a formality.
        """
        return frozenset({cls.MEMORY_RESTRICTED, cls.APPROVALS_DECIDE})

    @classmethod
    def all(cls) -> list[ApiKeyScope]:
        return list(cls)


class WebhookEvent(StrEnum):
    """Outbound notifications a customer application can subscribe to."""

    MEMORY_CREATED = "memory.created"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_CONFLICT = "memory.conflict"
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_AT_RISK = "customer.at_risk"
    CUSTOMER_RECOVERED = "customer.recovered"
    CUSTOMER_HEALTH_CHANGED = "customer.health_changed"
    EVENT_FAILED = "event.failed"
    CUSTOMER_DELETED = "customer.deleted"
    GOAL_ACHIEVED = "goal.achieved"
    GOAL_ABANDONED = "goal.abandoned"
    SIGNAL_RAISED = "signal.raised"
    CUSTOMER_STATE_CHANGED = "customer.state_changed"
    AGENT_ACTION_DENIED = "agent.action_denied"
    AGENT_APPROVAL_REQUESTED = "agent.approval_requested"
    AGENT_APPROVAL_DECIDED = "agent.approval_decided"
    AGENT_ACTION_COMPLETED = "agent.action_completed"
    MEMORY_DRIFT_DETECTED = "memory.drift_detected"
    MEMORY_DRIFT_RESOLVED = "memory.drift_resolved"

    @classmethod
    def all(cls) -> list[WebhookEvent]:
        return list(cls)


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DISABLED = "disabled"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AuditAction(StrEnum):
    API_ACCESS = "api_access"
    AUTHENTICATION = "authentication"
    MEMORY_CHANGE = "memory_change"
    CONFIGURATION_CHANGE = "configuration_change"
    DATA_DELETION = "data_deletion"
    MEMBER_CHANGE = "member_change"
    KEY_CHANGE = "key_change"
    WEBHOOK_CHANGE = "webhook_change"
    GOAL_CHANGE = "goal_change"
    AGENT_SESSION = "agent_session"
    AGENT_POLICY_CHANGE = "agent_policy_change"
    AGENT_APPROVAL = "agent_approval"
