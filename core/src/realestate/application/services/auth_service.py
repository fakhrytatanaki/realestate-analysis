"""Accounts and sessions.

Sessions are opaque random tokens rather than signed JWTs: the server can end
one on logout, and the only thing stored is the token's sha256, so a database
leak yields nothing a client could present.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from realestate.domain.exceptions import (
    AuthorizationError,
    ConflictError,
    ForbiddenError,
    InvalidQueryError,
)
from realestate.domain.models import Session, User
from realestate.domain.ports.repositories import SessionRepository, UserRepository
from realestate.domain.ports.security import PasswordHasher

#: Deliberately vague: the caller must not learn whether the email exists.
INVALID_CREDENTIALS = "invalid email or password"


@dataclass(frozen=True, slots=True)
class AuthSettings:
    session_ttl: timedelta = timedelta(days=30)
    allow_signup: bool = True
    min_password_length: int = 10
    #: Writing ``last_used_at`` on every request would turn reads into writes.
    touch_interval: timedelta = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A new session plus its raw token, which is never stored server-side."""

    user: User
    session: Session
    token: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    """Registration, login, token authentication and logout."""

    def __init__(
        self,
        *,
        users: UserRepository,
        sessions: SessionRepository,
        hasher: PasswordHasher,
        settings: AuthSettings | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._hasher = hasher
        self._settings = settings or AuthSettings()
        self._clock = clock
        # Verified against for unknown emails, so both paths cost one hash check.
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(16))

    async def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "",
        user_agent: str | None = None,
    ) -> IssuedSession:
        """Create an account and sign it in.

        Raises:
            ForbiddenError: if sign-up is switched off.
            InvalidQueryError: if the password is too short.
            ConflictError: if the email is already registered.
        """
        if not self._settings.allow_signup:
            raise ForbiddenError("sign-up is disabled")
        if len(password) < self._settings.min_password_length:
            raise InvalidQueryError(
                f"password must be at least {self._settings.min_password_length} characters"
            )
        email = normalise_email(email)
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("an account with this email already exists")
        user = await self._users.add(
            User(
                id=uuid4(),
                email=email,
                display_name=display_name.strip() or email.split("@", 1)[0],
                password_hash=self._hasher.hash(password),
                is_active=True,
                created_at=self._clock(),
            )
        )
        return await self._issue(user, user_agent)

    async def login(
        self, *, email: str, password: str, user_agent: str | None = None
    ) -> IssuedSession:
        """Exchange credentials for a session.

        Raises:
            AuthorizationError: for a wrong password, unknown email or inactive
                account, all with the same message.
        """
        user = await self._users.get_by_email(normalise_email(email))
        if user is None:
            self._hasher.verify(self._dummy_hash, password)
            raise AuthorizationError(INVALID_CREDENTIALS)
        if not self._hasher.verify(user.password_hash, password) or not user.is_active:
            raise AuthorizationError(INVALID_CREDENTIALS)
        if self._hasher.needs_rehash(user.password_hash):
            await self._users.update_password_hash(user.id, self._hasher.hash(password))
        return await self._issue(user, user_agent)

    async def authenticate(self, token: str) -> User:
        """The user a bearer token belongs to.

        Raises:
            AuthorizationError: if the token is unknown, expired, or its user is
                gone or deactivated.
        """
        session = await self._sessions.get_by_token_hash(hash_token(token))
        if session is None:
            raise AuthorizationError("invalid or expired session")
        now = self._clock()
        if session.expires_at <= now:
            await self._sessions.delete(session.id)
            raise AuthorizationError("invalid or expired session")
        user = await self._users.get(session.user_id)
        if user is None or not user.is_active:
            raise AuthorizationError("invalid or expired session")
        if now - session.last_used_at >= self._settings.touch_interval:
            await self._sessions.touch(session.id, at=now)
        return user

    async def logout(self, token: str) -> None:
        """End the session behind ``token``; unknown tokens are ignored."""
        session = await self._sessions.get_by_token_hash(hash_token(token))
        if session is not None:
            await self._sessions.delete(session.id)

    async def _issue(self, user: User, user_agent: str | None) -> IssuedSession:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        session = await self._sessions.add(
            Session(
                id=uuid4(),
                user_id=user.id,
                token_hash=hash_token(token),
                created_at=now,
                expires_at=now + self._settings.session_ttl,
                last_used_at=now,
                user_agent=user_agent[:512] if user_agent else None,
            )
        )
        return IssuedSession(user=user, session=session, token=token)
