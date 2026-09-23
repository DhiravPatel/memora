"""SQLAlchemy models. Importing this module registers every table on ``Base.metadata``."""

from database.models.agent import AgentSession, AgentTurn
from database.models.api_key import ApiKey
from database.models.customer import Customer
from database.models.embedding import EMBEDDING_DIMENSIONS, Embedding
from database.models.encryption import EncryptionKeyUse
from database.models.entity import Entity, Relationship
from database.models.event import Event
from database.models.goal import CustomerGoal
from database.models.invitation import UserInvitation
from database.models.link import MemoryLink
from database.models.memory import Memory, MemoryEntity, MemoryVersion
from database.models.observability import AuditLog, QueryLog, UsageRecord
from database.models.organization import Organization, User
from database.models.project import DEFAULT_PROJECT_SETTINGS, Project
from database.models.signal import SignalSnapshot
from database.models.vocabulary import LearnedTerm
from database.models.webhook import (
    MAX_CONSECUTIVE_FAILURES,
    WebhookDelivery,
    WebhookEndpoint,
)

__all__ = [
    "DEFAULT_PROJECT_SETTINGS",
    "EMBEDDING_DIMENSIONS",
    "MAX_CONSECUTIVE_FAILURES",
    "AgentSession",
    "AgentTurn",
    "ApiKey",
    "AuditLog",
    "Customer",
    "CustomerGoal",
    "Embedding",
    "EncryptionKeyUse",
    "Entity",
    "Event",
    "LearnedTerm",
    "Memory",
    "MemoryEntity",
    "MemoryLink",
    "MemoryVersion",
    "Organization",
    "Project",
    "QueryLog",
    "Relationship",
    "SignalSnapshot",
    "UsageRecord",
    "User",
    "UserInvitation",
    "WebhookDelivery",
    "WebhookEndpoint",
]
