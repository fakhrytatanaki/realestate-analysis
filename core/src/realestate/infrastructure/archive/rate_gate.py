"""Request spacing for one archive host, shared by every worker that uses it.

``WaybackClient`` keeps its own delay between requests, which only holds within
one client. Two crawl processes (or a crawl plus an ``archive enumerate``)
would each keep their delay and together exceed it. A rate gate hands out
send slots from one shared clock: each request reserves the next slot, at
least ``interval`` after the previous reservation, and waits for it. A 429
pushes the whole gate back, so one worker's rate limit pauses all of them.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta

from tortoise.transactions import in_transaction

from realestate.infrastructure.db.models import ArchiveRateGateModel


class RateGate(ABC):
    @abstractmethod
    async def reserve(self, name: str, *, interval: float) -> float:
        """Reserve the next send slot; returns seconds to wait before sending."""

    @abstractmethod
    async def hold(self, name: str, *, seconds: float) -> None:
        """Nobody sends to ``name`` for ``seconds`` (a rate-limit cooldown)."""


class LocalRateGate(RateGate):
    """One process's gate (tests, single worker): the same arithmetic, in memory."""

    def __init__(self) -> None:
        self._next: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, name: str, *, interval: float) -> float:
        async with self._lock:
            now = datetime.now(UTC)
            slot = max(now, self._next.get(name, now))
            self._next[name] = slot + timedelta(seconds=interval)
            return max(0.0, (slot - now).total_seconds())

    async def hold(self, name: str, *, seconds: float) -> None:
        async with self._lock:
            until = datetime.now(UTC) + timedelta(seconds=seconds)
            self._next[name] = max(self._next.get(name, until), until)


class PostgresRateGate(RateGate):
    """Slots kept in ``archive_rate_gate``; the row lock serialises reservations."""

    async def reserve(self, name: str, *, interval: float) -> float:
        async with in_transaction() as connection:
            now = datetime.now(UTC)
            await ArchiveRateGateModel.get_or_create(
                name=name, defaults={"next_at": now}, using_db=connection
            )
            row = (
                await ArchiveRateGateModel.filter(name=name)
                .using_db(connection)
                .select_for_update()
                .get()
            )
            slot = max(now, row.next_at)
            row.next_at = slot + timedelta(seconds=interval)
            await row.save(using_db=connection, update_fields=["next_at"])
        return max(0.0, (slot - now).total_seconds())

    async def hold(self, name: str, *, seconds: float) -> None:
        async with in_transaction() as connection:
            until = datetime.now(UTC) + timedelta(seconds=seconds)
            row, _ = await ArchiveRateGateModel.get_or_create(
                name=name, defaults={"next_at": until}, using_db=connection
            )
            row = (
                await ArchiveRateGateModel.filter(name=name)
                .using_db(connection)
                .select_for_update()
                .get()
            )
            if row.next_at < until:
                row.next_at = until
                await row.save(using_db=connection, update_fields=["next_at"])
