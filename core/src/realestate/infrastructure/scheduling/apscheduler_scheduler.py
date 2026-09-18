"""APScheduler-backed job scheduler."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from realestate.config.settings import SchedulerSettings
from realestate.domain.exceptions import NotFoundError
from realestate.domain.ports.job_scheduler import JobCallable, JobScheduler, ScheduledJob


class ApSchedulerJobScheduler(JobScheduler):
    """In-process scheduler running jobs on the application's event loop.

    Configured so a slow scrape cannot pile up: ``max_instances=1`` means a job
    still running when its next tick arrives is skipped rather than started
    twice, and ``coalesce=True`` collapses a backlog of missed ticks into one.
    """

    def __init__(self, settings: SchedulerSettings) -> None:
        self._settings = settings
        self._scheduler = AsyncIOScheduler(
            timezone=settings.timezone,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": settings.misfire_grace_seconds,
            },
        )

    def add_interval_job(
        self,
        func: JobCallable,
        *,
        job_id: str,
        minutes: float,
        name: str | None = None,
        start_immediately: bool = False,
    ) -> ScheduledJob:
        job = self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(minutes=minutes, timezone=self._settings.timezone),
            id=job_id,
            name=name or job_id,
            replace_existing=True,
            # `next_run_time=None` would leave the job paused, so only set it
            # when the caller explicitly wants an immediate first run.
            **({"next_run_time": _now(self._settings.timezone)} if start_immediately else {}),
        )
        return _to_scheduled_job(job)

    def add_cron_job(
        self,
        func: JobCallable,
        *,
        job_id: str,
        crontab: str,
        name: str | None = None,
    ) -> ScheduledJob:
        job = self._scheduler.add_job(
            func,
            trigger=CronTrigger.from_crontab(crontab, timezone=self._settings.timezone),
            id=job_id,
            name=name or job_id,
            replace_existing=True,
        )
        return _to_scheduled_job(job)

    async def trigger_now(self, job_id: str) -> None:
        job = self._scheduler.get_job(job_id)
        if job is None:
            raise NotFoundError("ScheduledJob", job_id)
        job.modify(next_run_time=_now(self._settings.timezone))

    def list_jobs(self) -> list[ScheduledJob]:
        return [_to_scheduled_job(job) for job in self._scheduler.get_jobs()]

    async def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    async def shutdown(self, *, wait: bool = True) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)


def _now(timezone: str):  # type: ignore[no-untyped-def]
    from datetime import datetime

    from apscheduler.util import astimezone

    return datetime.now(tz=astimezone(timezone))


def _to_scheduled_job(job: object) -> ScheduledJob:
    return ScheduledJob(
        id=getattr(job, "id", ""),
        name=getattr(job, "name", ""),
        trigger=str(getattr(job, "trigger", "")),
        next_run_at=getattr(job, "next_run_time", None),
    )
