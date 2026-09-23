"""Authentication and account schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from common.enums import UserRole


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=255)
    organization_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    id: str
    email: str
    name: str | None = None
    role: UserRole
    organization_id: str
    created_at: datetime


class OrganizationOut(BaseModel):
    id: str
    name: str
    slug: str
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserOut
    organization: OrganizationOut
    tokens: TokenPair
