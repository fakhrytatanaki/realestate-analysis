"""Standalone scheduler process.

Runs the same container as the API but without HTTP, so scraping can be scaled
or restarted independently of the query surface. Start it with:

    python -m realestate.worker
"""

from __future__ import annotations

import asyncio
import signal

from realestate.bootstrap import Container


async def run_worker(container: Container | None = None) -> None:
    """Start the scheduler and block until interrupted."""
    container = container or Container()
    await container.init_db()

    jobs = container.register_scheduled_jobs()
    await container.scheduler.start()
    await container.log.info("worker started", jobs=jobs)
    if not jobs:
        await container.log.warning(
            "no scheduled jobs registered; enable a source and give it "
            "interval_minutes or crontab in etc/settings.toml"
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Signal handlers on the loop, so shutdown runs the same teardown path
        # as a normal exit instead of unwinding through KeyboardInterrupt.
        loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        await container.log.info("worker stopping")
        await container.aclose()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
