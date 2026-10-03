"""Tortoise-backed account and session repositories."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from tortoise.exceptions import IntegrityError

from realestate.domain.exceptions import ConflictError
from realestate.domain.models import Session, User
from realestate.domain.ports.repositories import SessionRepository, UserRepository
from realestate.infrastructure.db.mappers import to_session, to_user
from realestate.infrastructure.db.models import SessionModel, UserModel


class TortoiseUserRepository(UserRepository):
    """Accounts."""

    async def add(self, user: User) -> User:
        try:
            row = await UserModel.create(
                id=user.id,
                email=user.email,
                display_name=user.display_name,
                password_hash=user.password_hash,
                is_active=user.is_active,
            )
        except IntegrityError as exc:
            # The service checks first; this catches the race between two sign-ups.
            raise ConflictError("an account with this email already exists") from exc
        return to_user(row)

    async def get(self, user_id: UUID) -> User | None:
        row = await UserModel.get_or_none(id=user_id)
        return to_user(row) if row else None

    async def get_by_email(self, email: str) -> User | None:
        row = await UserModel.get_or_none(email=email)
        return to_user(row) if row else None

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        await UserModel.filter(id=user_id).update(password_hash=password_hash)


class TortoiseSessionRepository(SessionRepository):
    """Sessions, looked up by token hash."""

    async def add(self, session: Session) -> Session:
        row = await SessionModel.create(
            id=session.id,
            user_id=session.user_id,
            token_hash=session.token_hash,
            created_at=session.created_at,
            expires_at=session.expires_at,
            last_used_at=session.last_used_at,
            user_agent=session.user_agent,
        )
        return to_session(row)

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        row = await SessionModel.get_or_none(token_hash=token_hash)
        return to_session(row) if row else None

    async def touch(self, session_id: UUID, *, at: datetime) -> None:
        await SessionModel.filter(id=session_id).update(last_used_at=at)

    async def delete(self, session_id: UUID) -> None:
        await SessionModel.filter(id=session_id).delete()

    async def delete_for_user(self, user_id: UUID) -> int:
        return await SessionModel.filter(user_id=user_id).delete()
