"""Account and session DTOs."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from realestate.application.services.auth_service import IssuedSession
from realestate.domain.models import User

#: Deliberately loose: real validation is the confirmation mail we do not send
#: yet. This only rejects input that cannot be an address at all.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _Credentials(BaseModel):
    email: str = Field(max_length=254, examples=["ada@example.com"])
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        value = value.strip()
        if not _EMAIL.match(value):
            raise ValueError("not a valid email address")
        return value


class LoginRequest(_Credentials):
    """Credentials for ``POST /auth/login``."""


class RegisterRequest(_Credentials):
    """A new account for ``POST /auth/register``."""

    display_name: str = Field(default="", max_length=128)


class UserRead(BaseModel):
    """The signed-in account. Never carries the password hash."""

    id: UUID
    email: str
    display_name: str
    created_at: datetime

    @classmethod
    def from_domain(cls, user: User) -> UserRead:
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            created_at=user.created_at,
        )


class SessionRead(BaseModel):
    """A freshly issued session. ``token`` goes in ``Authorization: Bearer``."""

    token: str
    expires_at: datetime
    user: UserRead

    @classmethod
    def from_issued(cls, issued: IssuedSession) -> SessionRead:
        return cls(
            token=issued.token,
            expires_at=issued.session.expires_at,
            user=UserRead.from_domain(issued.user),
        )
