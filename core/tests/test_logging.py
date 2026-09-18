"""Log provider behaviour."""

from __future__ import annotations

import io
import json
from pathlib import Path

from realestate.domain.enums import LogLevel
from realestate.infrastructure.logging.composite import CompositeLogProvider
from realestate.infrastructure.logging.file import FileLogProvider
from realestate.infrastructure.logging.std_stream import StdStreamLogProvider


async def test_warnings_go_to_stderr_and_info_to_stdout() -> None:
    out, err = io.StringIO(), io.StringIO()
    log = StdStreamLogProvider(stdout=out, stderr=err)

    await log.info("all good")
    await log.warning("careful")

    assert "all good" in out.getvalue()
    assert "all good" not in err.getvalue()
    assert "careful" in err.getvalue()


async def test_bind_stamps_context_on_every_record() -> None:
    out = io.StringIO()
    log = StdStreamLogProvider(stdout=out, as_json=True).bind(source_key="fixture")

    await log.info("started", run_id=7)

    record = json.loads(out.getvalue().strip())
    assert record["source_key"] == "fixture"
    assert record["run_id"] == 7
    assert record["message"] == "started"


async def test_level_below_threshold_is_dropped() -> None:
    out = io.StringIO()
    log = StdStreamLogProvider(min_level=LogLevel.WARNING, stdout=out)

    await log.info("noise")

    assert out.getvalue() == ""


async def test_file_provider_writes_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "log" / "app.log"
    log = FileLogProvider(path)

    await log.info("first")
    await log.error("second")
    await log.aclose()

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["message"] for line in lines] == ["first", "second"]
    assert lines[1]["level"] == "ERROR"


async def test_bound_file_provider_shares_one_writer(tmp_path: Path) -> None:
    """Children must append to the same file rather than opening rival writers."""
    path = tmp_path / "app.log"
    parent = FileLogProvider(path)
    child = parent.bind(source_key="fixture")

    await parent.info("from parent")
    await child.info("from child")
    await parent.aclose()

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[1]["source_key"] == "fixture"


async def test_composite_isolates_a_failing_sink() -> None:
    """A broken sink must not stop the healthy one from receiving the record."""

    class Exploding(StdStreamLogProvider):
        async def log(self, level, message, **fields):  # type: ignore[no-untyped-def]
            raise RuntimeError("sink is down")

    out = io.StringIO()
    healthy = StdStreamLogProvider(stdout=out)
    composite = CompositeLogProvider([Exploding(), healthy])

    await composite.info("still delivered")

    assert "still delivered" in out.getvalue()


async def test_exception_helper_records_the_traceback() -> None:
    out = io.StringIO()
    log = StdStreamLogProvider(stdout=out, stderr=out, as_json=True)

    try:
        raise ValueError("boom")
    except ValueError as exc:
        await log.exception("it failed", exc)

    record = json.loads(out.getvalue().strip())
    assert record["error_type"] == "ValueError"
    assert "ValueError: boom" in record["traceback"]
