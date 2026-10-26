"""Independent process watchdog; a stalled asyncio loop cannot renew its own watchdog."""

import asyncio
import multiprocessing
import signal
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from multiprocessing.connection import Connection
from types import FrameType

Runner = Callable[[], Awaitable[None]]


def _child(runner: Runner, channel: Connection) -> None:
    async def execute() -> None:
        async def heartbeat() -> None:
            while True:
                channel.send("tick")
                await asyncio.sleep(0.5)

        ticker = asyncio.create_task(heartbeat())
        try:
            await runner()
        except BaseException:
            channel.send("failed")
            raise
        finally:
            ticker.cancel()
            with suppress(asyncio.CancelledError):
                await ticker

    try:
        asyncio.run(execute())
    except BaseException:
        # No exception text or provider payload crosses this process boundary.
        raise SystemExit(1) from None
    finally:
        channel.close()


def supervise(runner: Runner) -> int:
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(runner, writer))
    process.start()
    writer.close()
    last_tick = time.monotonic()
    stop_by: float | None = None

    def shutdown(signum: int, frame: FrameType | None) -> None:
        nonlocal stop_by
        process.terminate()
        stop_by = time.monotonic() + 5

    previous = {
        signum: signal.signal(signum, shutdown) for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        while process.is_alive():
            if reader.poll(0.1):
                try:
                    message = reader.recv()
                except EOFError:
                    message = None
                if message == "tick":
                    last_tick = time.monotonic()
                elif message == "failed" and stop_by is None:
                    # The runner already allowed two seconds for cooperative cleanup.
                    stop_by = time.monotonic() + 3
            now = time.monotonic()
            if (stop_by is not None and now >= stop_by) or now - last_tick >= 5:
                process.kill()
                process.join(timeout=1)
                return 70
        process.join(timeout=1)
        return 0 if process.exitcode == 0 else 1
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
        reader.close()
        process.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
