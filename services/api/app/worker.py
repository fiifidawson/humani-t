"""The worker process: `uv run python -m app.worker`.

A composition root like `cli.py`, which runs queued runs until told to stop. Workers
share the queue through Postgres and never take the same run. On SIGTERM it hands its
run back to the queue at once. `--check-health` passes while the heartbeat file is
younger than three poll intervals.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import structlog

from app.api.dependencies import Providers, build_providers
from app.application.run_worker import RunWorker
from app.observability import configure_logging
from app.settings import get_settings

POLL_INTERVAL = timedelta(seconds=5)
# Three intervals: one slow turn (a busy database) is tolerated, a stalled loop is not.
HEALTHY_WITHIN = 3 * POLL_INTERVAL
# The container is read-only with a tmpfs at /tmp; gettempdir() is /tmp there.
DEFAULT_HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "humani-t-worker-heartbeat"

_log = structlog.get_logger(__name__)


class Heartbeat:
    """A file whose modification time says when the worker was last alive."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def beat(self) -> None:
        self.path.touch()


def is_healthy(path: Path, within: timedelta = HEALTHY_WITHIN) -> bool:
    """Whether the worker beat within `within`. A missing file means it never did."""
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age < within.total_seconds()


def worker_id() -> str:
    """Unique per process, and readable in logs: which machine, which process."""
    return f"{socket.gethostname()[:64]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _stop_on_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows' event loop has no signal handlers; a plain handler that
            # schedules the stop on the loop works there.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def _serve(providers: Providers, heartbeat: Heartbeat) -> None:
    worker = RunWorker(
        storage=providers.storage,
        encryption=providers.encryption,
        signals=providers.signals,
        stages=providers.stages,
        worker_id=worker_id(),
        poll_interval=POLL_INTERVAL,
    )
    stop = asyncio.Event()
    _stop_on_signals(stop)
    _log.info("worker_started", worker=worker.worker_id, stages=sorted(providers.stages))
    try:
        # Follow-up questions are answered, and uploaded documents read, beside runs.
        await asyncio.gather(
            worker.serve(stop, on_alive=heartbeat.beat),
            providers.answers.serve(stop),
            providers.document_processor.serve(stop),
        )
    finally:
        await providers.close()
        _log.info("worker_stopped", worker=worker.worker_id)


@asynccontextmanager
async def running_in_process(providers: Providers) -> AsyncIterator[None]:
    """Work queued runs in this process while the context is open; hand back any on exit."""
    worker = RunWorker(
        storage=providers.storage,
        encryption=providers.encryption,
        signals=providers.signals,
        stages=providers.stages,
        worker_id=f"in-process:{worker_id()}",
        poll_interval=POLL_INTERVAL,
    )
    stop = asyncio.Event()
    serving = asyncio.create_task(worker.serve(stop))
    reading = asyncio.create_task(providers.document_processor.serve(stop))
    _log.info("worker_started_in_process", worker=worker.worker_id)
    try:
        yield
    finally:
        stop.set()
        await serving
        await reading
        _log.info("worker_stopped", worker=worker.worker_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.worker", description=__doc__, allow_abbrev=False
    )
    parser.add_argument(
        "--check-health",
        action="store_true",
        help="Exit 0 if the running worker beat recently, 1 if not. Starts nothing.",
    )
    parser.add_argument(
        "--heartbeat-file",
        type=Path,
        default=DEFAULT_HEARTBEAT_FILE,
        help=f"Where the worker records that it is alive (default {DEFAULT_HEARTBEAT_FILE}).",
    )
    args = parser.parse_args(argv)
    if args.check_health:
        return 0 if is_healthy(args.heartbeat_file) else 1

    settings = get_settings()
    configure_logging(settings.log_level, as_json=settings.is_production)
    asyncio.run(_serve(build_providers(settings), Heartbeat(args.heartbeat_file)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
