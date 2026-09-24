"""FastAPI dependencies: sessions, authentication and the memory engine.

Two authentication modes exist side by side. Dashboard users carry a JWT and are scoped
to their organization; customer applications carry a project API key and are scoped to
exactly one project. Neither can reach another tenant's data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import check_rate_limit
from app.core.security import decode_token, hash_api_key
from common.enums import ApiKeyScope, UserRole
from common.errors import AuthenticationError, AuthorizationError, NotFoundError
from common.logging import project_id_var
from common.settings import Settings, get_settings
from database.access import MemoryAccess, access_for_types, set_access
from database.models import AgentProfile, ApiKey, Customer, Project, User
from database.repositories import (
    AgentProfileRepository,
    ApiKeyRepository,
    AuditRepository,
    CustomerRepository,
    ProjectRepository,
    UsageRepository,
    UserRepository,
)
from database.session import create_session_factory
from memory_engine import MemoryEngine
from memory_engine.protocols import Embedder
from nlp import LocalEmbedder


async def get_db() -> AsyncIterator[AsyncSession]:
    """One session per request, committed on success."""
    factory = create_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DBSession = Annotated[AsyncSession, Depends(get_db)]


def get_app_settings() -> Settings:
    return get_settings()


AppSettings = Annotated[Settings, Depends(get_app_settings)]


def get_embedder(request: Request) -> Embedder:
    """The embedder is stateless and deterministic, so one instance serves the process."""
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        embedder = LocalEmbedder()
        request.app.state.embedder = embedder
    return embedder


async def get_clearance(
    request: Request,
    session: DBSession,
    authorization: Annotated[str | None, Header()] = None,
) -> bool:
    """Whether this caller may read memories the project marked restricted.

    Denied by default, on every path. An API key needs ``memory:restricted`` explicitly —
    including a legacy full-scope key, which predates the idea of restricted memory and
    cannot be assumed to have been issued with it in mind — and, when it acts as an agent
    profile, a profile that allows it too: the scope says what the key may be trusted
    with, the profile what the agent's job needs. A dashboard user needs the admin role,
    because a restriction exists precisely to keep the content away from most of the team.
    """
    key: ApiKey | None = getattr(request.state, "api_key", None)
    if key is not None:
        if not key.has_scope(ApiKeyScope.MEMORY_RESTRICTED):
            return False
        profile: AgentProfile | None = getattr(request.state, "agent_profile", None)
        return profile is None or bool(profile.can_read_restricted)
    if getattr(request.state, "project_id", None) is not None:
        return False  # legacy project key: authenticated, but not cleared

    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    try:
        current = await get_current_user(session, authorization)
    except (AuthenticationError, AuthorizationError):
        return False
    return UserRole(current.user.role).can(UserRole.ADMIN)


Clearance = Annotated[bool, Depends(get_clearance)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


def get_memory_engine(
    session: DBSession,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    cleared: Clearance,
) -> MemoryEngine:
    """The engine, constrained to what this caller is allowed to read.

    Clearance is fixed at construction rather than passed to each call, so there is no
    path — retrieval, context, answering — that can forget it.
    """
    return MemoryEngine(session=session, embedder=embedder, cleared=cleared)


Engine = Annotated[MemoryEngine, Depends(get_memory_engine)]


# ------------------------------------------------------------ user (dashboard)


@dataclass(slots=True)
class CurrentUser:
    user: User
    organization_id: str

    def require(self, role: UserRole) -> None:
        if not UserRole(self.user.role).can(role):
            raise AuthorizationError(f"This action requires the {role.value} role.")


async def get_current_user(
    session: DBSession,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Missing bearer token.")
    payload = decode_token(authorization.split(" ", 1)[1].strip())
    user = await UserRepository(session).get(str(payload.get("sub", "")))
    if user is None or not user.is_active:
        raise AuthenticationError("User no longer exists or is inactive.")
    # Tokens issued before a password change or a "sign out everywhere" carry an older
    # version and stop validating here.
    if int(payload.get("ver", 0)) != int(user.token_version or 0):
        raise AuthenticationError("This session has been signed out.")
    return CurrentUser(user=user, organization_id=user.organization_id)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def get_user_project(
    project_id: str,
    session: DBSession,
    current_user: CurrentUserDep,
) -> Project:
    """A project, verified to belong to the caller's organization."""
    project = await ProjectRepository(session).get_for_organization(
        project_id, current_user.organization_id
    )
    if project is None:
        raise NotFoundError("Project not found.")
    project_id_var.set(project.id)
    return project


UserProject = Annotated[Project, Depends(get_user_project)]


# -------------------------------------------------------- project (API key)


async def get_api_project(
    request: Request,
    session: DBSession,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_agent_name: Annotated[str | None, Header(alias="X-Agent-Name")] = None,
) -> Project:
    """Resolve the project behind an API key. This is the tenancy boundary.

    Keys live in ``api_keys`` (named, scoped, revocable). A project's original key is
    still accepted and behaves as a full-scope key, so nothing breaks on upgrade.

    A key bound to an agent profile (§26 3.1) also fixes what the request may read: the
    profile's memory types are set as the request's access here, before any route runs,
    and every reader-bound repository honours them.
    """
    api_key = x_api_key
    if not api_key and authorization and authorization.lower().startswith("bearer "):
        candidate = authorization.split(" ", 1)[1].strip()
        if candidate.startswith("mk_"):
            api_key = candidate
    if not api_key:
        raise AuthenticationError("Missing API key. Send it in the X-API-Key header.")

    digest = hash_api_key(api_key)
    keys = ApiKeyRepository(session)
    record = await keys.get_by_hash(digest)

    if record is not None:
        if not record.is_active:
            raise AuthenticationError(
                "This API key has been revoked."
                if record.revoked_at
                else "This API key has expired."
            )
        project = await ProjectRepository(session).get(record.project_id)
        if project is None or not project.is_active:
            raise AuthenticationError("Invalid API key.")
        await keys.touch(record.id, ip_address=client_ip(request))
        request.state.api_key = record
        profile = (
            await AgentProfileRepository(session).get_any(record.agent_profile_id)
            if record.agent_profile_id
            else None
        )
        if profile is not None and profile.project_id != project.id:
            profile = None  # cannot happen through the API; never trust it anyway
    else:
        project = await ProjectRepository(session).get_by_api_key_hash(digest)
        if project is None:
            raise AuthenticationError("Invalid API key.")
        request.state.api_key = None  # legacy project key: full scope
        profile = None

    request.state.agent_profile = profile
    key_id = record.id if record is not None else None
    if profile is not None:
        access = access_for_types(
            profile.readable_types,
            profile_id=profile.id,
            profile=profile.name,
            api_key_id=key_id,
            agent=profile.name,
        )
    else:
        # An unbound key may label its runs; a bound one is labelled by its profile.
        label = " ".join((x_agent_name or "").split())[:120] or None
        access = MemoryAccess(api_key_id=key_id, agent=label)
    set_access(access)
    project_id_var.set(project.id)
    request.state.project_id = project.id
    return project


def require_scope(*scopes: ApiKeyScope):
    """Dependency factory enforcing least privilege on an API-key route."""

    async def dependency(request: Request, _project: ApiProject) -> None:
        key: ApiKey | None = getattr(request.state, "api_key", None)
        if key is None:
            return  # legacy project key, or a key issued before scopes existed
        if not any(key.has_scope(scope) for scope in scopes):
            raise AuthorizationError(
                "This API key is missing the required scope: "
                + " or ".join(scope.value for scope in scopes)
            )

    return dependency


ApiProject = Annotated[Project, Depends(get_api_project)]


async def get_api_customer(
    customer_id: str,
    session: DBSession,
    project: ApiProject,
) -> Customer:
    customer = await CustomerRepository(session).resolve(customer_id, project.id)
    if customer is None:
        raise NotFoundError(f"Customer '{customer_id}' not found.")
    return customer


ApiCustomer = Annotated[Customer, Depends(get_api_customer)]


async def enforce_rate_limit(project: ApiProject, response: Response) -> None:
    """Applied to every API-key route; sets the standard rate-limit headers."""
    state = await check_rate_limit(project)
    if state.enforced:
        response.headers.update(state.headers())


RateLimited = Annotated[None, Depends(enforce_rate_limit)]


def get_audit_repository(session: DBSession) -> AuditRepository:
    return AuditRepository(session)


Audit = Annotated[AuditRepository, Depends(get_audit_repository)]


def get_usage_repository(session: DBSession) -> UsageRepository:
    return UsageRepository(session)


Usage = Annotated[UsageRepository, Depends(get_usage_repository)]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
