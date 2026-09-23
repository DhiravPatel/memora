"""Organizations and dashboard users."""

from __future__ import annotations

from sqlalchemy import select

from common.enums import UserRole
from common.ids import new_id
from common.time import utcnow
from database.models import Organization, User
from database.repositories.base import BaseRepository


class OrganizationRepository(BaseRepository):
    async def create(self, *, name: str, slug: str) -> Organization:
        organization = Organization(id=new_id("org"), name=name, slug=slug)
        self.session.add(organization)
        await self.session.flush()
        return organization

    async def get(self, organization_id: str) -> Organization | None:
        return await self.session.get(Organization, organization_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self.session.execute(
            select(Organization).where(Organization.slug == slug)
        )
        return result.scalar_one_or_none()


class UserRepository(BaseRepository):
    async def create(
        self,
        *,
        organization_id: str,
        email: str,
        password_hash: str,
        name: str | None = None,
        role: UserRole = UserRole.OWNER,
    ) -> User:
        user = User(
            id=new_id("usr"),
            organization_id=organization_id,
            email=email.lower(),
            password_hash=password_hash,
            name=name,
            role=role,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def get(self, user_id: str) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def list_for_organization(self, organization_id: str) -> list[User]:
        result = await self.session.execute(
            select(User).where(User.organization_id == organization_id).order_by(User.created_at)
        )
        return list(result.scalars())

    async def touch_login(self, user: User) -> None:
        user.last_login_at = utcnow()
        await self.session.flush()
