"""API keys, team members, invitations and webhook schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

from common.enums import ApiKeyScope, DeliveryStatus, InvitationStatus, UserRole, WebhookEvent

# ------------------------------------------------------------------- API keys


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="How you'll recognise this key")
    scopes: list[ApiKeyScope] | None = Field(
        default=None, description="Defaults to events:write, memory:read, customers:read"
    )
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyScopesUpdate(BaseModel):
    scopes: list[ApiKeyScope] = Field(min_length=1)


class ApiKeyOut(BaseModel):
    id: str
    project_id: str
    name: str
    key_prefix: str
    scopes: list[str]
    created_by: str | None = None
    last_used_at: datetime | None = None
    last_used_ip: str | None = None
    use_count: int = 0
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool = True
    created_at: datetime


class ApiKeyWithSecret(ApiKeyOut):
    """Returned once, at creation. The raw key is never stored."""

    api_key: str


class ScopeInfo(BaseModel):
    scope: str
    description: str


SCOPE_DESCRIPTIONS: dict[str, str] = {
    ApiKeyScope.EVENTS_WRITE.value: "Send events and retry failed ones",
    ApiKeyScope.MEMORY_READ.value: "Read memories, query, search and build context",
    ApiKeyScope.MEMORY_WRITE.value: "Create memories, submit feedback, delete memories",
    ApiKeyScope.CUSTOMERS_READ.value: "Read customers, timelines, health, links and exports",
    ApiKeyScope.CUSTOMERS_WRITE.value: "Create, merge and delete customers",
    ApiKeyScope.MEMORY_RESTRICTED.value: (
        "Clearance to read memories this project marked restricted. Granted on its own — "
        "admin does not confer it"
    ),
    ApiKeyScope.ADMIN.value: "Everything except clearance to read restricted memory",
}


# ---------------------------------------------------------------------- team


class MemberOut(BaseModel):
    id: str
    email: str
    name: str | None = None
    role: UserRole
    is_active: bool = True
    last_login_at: datetime | None = None
    created_at: datetime


class MemberRoleUpdate(BaseModel):
    role: UserRole


class InvitationCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.MEMBER


class InvitationOut(BaseModel):
    id: str
    email: str
    role: UserRole
    status: InvitationStatus
    invited_by: str | None = None
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime


class InvitationWithToken(InvitationOut):
    """The token is shown once. An email carries it too, unless mail could not be queued."""

    token: str
    accept_path: str
    # False means Redis was unreachable and no email will be sent, so this response holds
    # the only copy of the link. The dashboard shows that difference rather than hiding it.
    email_queued: bool = False


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=255)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# ------------------------------------------------------------------ webhooks


class WebhookEndpointCreate(BaseModel):
    url: HttpUrl
    description: str | None = Field(default=None, max_length=255)
    event_types: list[WebhookEvent] = Field(
        default_factory=list, description="Empty means every event"
    )

    @field_validator("url")
    @classmethod
    def _as_string(cls, value: HttpUrl) -> HttpUrl:
        return value


class WebhookEndpointUpdate(BaseModel):
    url: HttpUrl | None = None
    description: str | None = Field(default=None, max_length=255)
    event_types: list[WebhookEvent] | None = None
    is_active: bool | None = None


class WebhookEndpointOut(BaseModel):
    id: str
    project_id: str
    url: str
    description: str | None = None
    event_types: list[str] = Field(default_factory=list)
    is_active: bool
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime


class WebhookEndpointWithSecret(WebhookEndpointOut):
    secret: str


class WebhookDeliveryOut(BaseModel):
    id: str
    endpoint_id: str
    event_type: str
    event_id: str
    status: DeliveryStatus
    attempts: int
    response_status: int | None = None
    response_body: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    scheduled_at: datetime
    delivered_at: datetime | None = None
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class WebhookEventInfo(BaseModel):
    event: str
    description: str


WEBHOOK_EVENT_DESCRIPTIONS: dict[str, str] = {
    WebhookEvent.MEMORY_CREATED.value: "A new memory was written for a customer",
    WebhookEvent.MEMORY_UPDATED.value: "An existing memory gained evidence or was rewritten",
    WebhookEvent.MEMORY_CONFLICT.value: "A contradiction superseded an existing memory",
    WebhookEvent.CUSTOMER_CREATED.value: "A customer was seen for the first time",
    WebhookEvent.CUSTOMER_AT_RISK.value: "A customer's health dropped into at_risk or critical",
    WebhookEvent.CUSTOMER_RECOVERED.value: "A customer's health recovered out of at_risk",
    WebhookEvent.CUSTOMER_HEALTH_CHANGED.value: "A customer's health band changed",
    WebhookEvent.EVENT_FAILED.value: "An event exhausted its processing retries",
    WebhookEvent.CUSTOMER_DELETED.value: "A customer and everything derived from them was deleted",
}


# ----------------------------------------------------------------- customers


class CustomerBatchUpsert(BaseModel):
    customers: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class CustomerBatchResult(BaseModel):
    created: int
    updated: int
    customer_ids: list[str] = Field(default_factory=list)


class CustomerMergeRequest(BaseModel):
    into: str = Field(min_length=1, max_length=255, description="The customer that survives")


class CustomerMergeResult(BaseModel):
    source_customer_id: str
    target_customer_id: str
    events_moved: int
    memories_moved: int
    links_moved: int
