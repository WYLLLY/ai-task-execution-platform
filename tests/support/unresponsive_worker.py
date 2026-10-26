"""Test-only trusted-client failures, injected inside the supervised child."""

import asyncio
import os
import time
from unittest.mock import patch

from task_platform.cli import worker_run
from task_platform.execution import Lease, Permit
from task_platform.supervisor import supervise
from task_platform.transport import Outcome, SimulationTransport
from task_platform.worker import Worker


def stall_event_loop() -> None:
    # Deliberate test-only synchronous adapter failure; watchdog must be independent.
    time.sleep(20)


async def run_unresponsive() -> None:
    original = SimulationTransport.send
    fault_ready = asyncio.Event()

    async def send(self: SimulationTransport, permit: Permit) -> Outcome:
        await asyncio.sleep(float(os.environ.get("TEST_SETUP_DELAY", "0")))
        await original(self, permit)
        print("fault-ready", flush=True)
        fault_ready.set()
        if os.environ["TEST_STALL"] == "loop":
            stall_event_loop()
        else:
            while True:
                try:
                    await asyncio.sleep(20)
                except asyncio.CancelledError:
                    continue
        return Outcome()

    async def lose_ownership(self: Worker, lease: Lease) -> bool:
        await fault_ready.wait()
        await asyncio.sleep(0.2)
        return False

    with (
        patch.object(SimulationTransport, "send", send),
        patch.object(Worker, "_heartbeat", lose_ownership),
    ):
        await worker_run(True)


if __name__ == "__main__":
    raise SystemExit(supervise(run_unresponsive))
