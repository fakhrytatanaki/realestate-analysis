"""Accounts and sessions, against in-memory repositories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from realestate.application.services.auth_service import (
    AuthService,
    AuthSettings,
    hash_token,
)
from realestate.domain.exceptions import (
    AuthorizationError,
    ConflictError,
    ForbiddenError,
    InvalidQueryError,
)
from tests.conftest import FakePasswordHasher, InMemorySessionRepository, InMemoryUserRepository

PASSWORD = "correct horse battery"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def users() -> InMemoryUserRepository:
    return InMemoryUserRepository()


@pytest.fixture
def sessions() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def hasher() -> FakePasswordHasher:
    return FakePasswordHasher()


@pytest.fixture
def auth(
    users: InMemoryUserRepository,
    sessions: InMemorySessionRepository,
    hasher: FakePasswordHasher,
    clock: Clock,
) -> AuthService:
    return AuthService(
        users=users,
        sessions=sessions,
        hasher=hasher,
        settings=AuthSettings(session_ttl=timedelta(days=1)),
        clock=clock,
    )


async def test_register_creates_user_and_session(
    auth: AuthService, sessions: InMemorySessionRepository
) -> None:
    issued = await auth.register(email="  Ada@Example.COM ", password=PASSWORD)

    assert issued.user.email == "ada@example.com"
    assert issued.user.display_name == "ada"
    assert issued.user.password_hash != PASSWORD
    assert issued.session.expires_at == datetime(2026, 1, 2, tzinfo=UTC)
    # Only the hash is stored, never the raw token.
    stored = next(iter(sessions.sessions.values()))
    assert stored.token_hash == hash_token(issued.token)
    assert issued.token not in stored.token_hash


async def test_register_rejects_duplicate_email_case_insensitively(auth: AuthService) -> None:
    await auth.register(email="ada@example.com", password=PASSWORD)

    with pytest.raises(ConflictError):
        await auth.register(email="ADA@example.com", password=PASSWORD)


async def test_register_enforces_password_length(auth: AuthService) -> None:
    with pytest.raises(InvalidQueryError):
        await auth.register(email="ada@example.com", password="short")


async def test_register_can_be_disabled(
    users: InMemoryUserRepository, sessions: InMemorySessionRepository
) -> None:
    auth = AuthService(
        users=users,
        sessions=sessions,
        hasher=FakePasswordHasher(),
        settings=AuthSettings(allow_signup=False),
    )

    with pytest.raises(ForbiddenError):
        await auth.register(email="ada@example.com", password=PASSWORD)


async def test_login_and_authenticate(auth: AuthService) -> None:
    await auth.register(email="ada@example.com", password=PASSWORD, display_name="Ada")

    issued = await auth.login(email="ADA@example.com", password=PASSWORD)
    user = await auth.authenticate(issued.token)

    assert user.display_name == "Ada"


@pytest.mark.parametrize(
    ("email", "password"),
    [("ada@example.com", "wrong password!"), ("nobody@example.com", PASSWORD)],
)
async def test_login_failures_are_indistinguishable(
    auth: AuthService, email: str, password: str
) -> None:
    await auth.register(email="ada@example.com", password=PASSWORD)

    with pytest.raises(AuthorizationError, match="invalid email or password"):
        await auth.login(email=email, password=password)


async def test_login_rehashes_stale_hashes(
    auth: AuthService, users: InMemoryUserRepository, hasher: FakePasswordHasher
) -> None:
    issued = await auth.register(email="ada@example.com", password=PASSWORD)
    hasher.version = 2

    await auth.login(email="ada@example.com", password=PASSWORD)

    assert users.users[issued.user.id].password_hash.startswith("fake$2$")


async def test_expired_session_is_rejected_and_removed(
    auth: AuthService, sessions: InMemorySessionRepository, clock: Clock
) -> None:
    issued = await auth.register(email="ada@example.com", password=PASSWORD)
    clock.now += timedelta(days=1)

    with pytest.raises(AuthorizationError):
        await auth.authenticate(issued.token)
    assert sessions.sessions == {}


async def test_authenticate_touches_at_most_once_per_interval(
    auth: AuthService, sessions: InMemorySessionRepository, clock: Clock
) -> None:
    issued = await auth.register(email="ada@example.com", password=PASSWORD)
    start = clock.now

    clock.now = start + timedelta(seconds=30)
    await auth.authenticate(issued.token)
    assert sessions.sessions[issued.session.id].last_used_at == start

    clock.now = start + timedelta(minutes=2)
    await auth.authenticate(issued.token)
    assert sessions.sessions[issued.session.id].last_used_at == clock.now


async def test_unknown_token_is_rejected(auth: AuthService) -> None:
    with pytest.raises(AuthorizationError):
        await auth.authenticate("not-a-token")


async def test_logout_ends_only_that_session(auth: AuthService) -> None:
    await auth.register(email="ada@example.com", password=PASSWORD)
    first = await auth.login(email="ada@example.com", password=PASSWORD)
    second = await auth.login(email="ada@example.com", password=PASSWORD)

    await auth.logout(first.token)
    await auth.logout("unknown-token")  # ignored

    with pytest.raises(AuthorizationError):
        await auth.authenticate(first.token)
    assert (await auth.authenticate(second.token)).email == "ada@example.com"
