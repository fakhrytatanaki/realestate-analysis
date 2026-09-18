"""Port for running background work on a schedule."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

#: A scheduled job is any zero-argument coroutine function; binding arguments is
#: the caller's job (``functools.partial`` or a closure over the container).
JobCallable = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """Introspection view of a registered job."""

    id: str
    name: str
    trigger: str
    next_run_at: datetime | None


class JobScheduler(ABC):
    """Runs coroutines on intervals or cron expressions.

    The in-process APScheduler implementation satisfies this today; a
    distributed queue would satisfy it tomorrow without touching callers.
    """

    @abstractmethod
    def add_interval_job(
        self,
        func: JobCallable,
        *,
        job_id: str,
        minutes: float,
        name: str | None = None,
        start_immediately: bool = False,
    ) -> ScheduledJob:
        """Register a job to run every ``minutes``."""

    @abstractmethod
    def add_cron_job(
        self,
        func: JobCallable,
        *,
        job_id: str,
        crontab: str,
        name: str | None = None,
    ) -> ScheduledJob:
        """Register a job on a five-field crontab expression."""

    @abstractmethod
    async def trigger_now(self, job_id: str) -> None:
        """Run a registered job as soon as the loop is free."""

    @abstractmethod
    def list_jobs(self) -> list[ScheduledJob]:
        """Everything currently registered."""

    @abstractmethod
    async def start(self) -> None:
        """Begin firing jobs. Idempotent."""

    @abstractmethod
    async def shutdown(self, *, wait: bool = True) -> None:
        """Stop firing jobs, optionally draining in-flight ones. Idempotent."""
