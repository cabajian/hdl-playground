"""Generic simpy-backed clock for pyhdl-if testbenches.

Modes:
  - "sim":        env.now is driven externally by SV via SimClockAPI.advance_to.
  - "standalone": env.now tracks wallclock; a background asyncio task advances it.

User code (async def) uses `SimClock.inst().sleep(ns)` / `wait_until(t)` / `now_ns()`.
Internally each simpy event is bridged to an asyncio.Future so it can be awaited.
"""

from __future__ import annotations

import asyncio
import time
import logging
from typing import Optional

import simpy
import hdl_if as hif


logger = logging.getLogger(__name__)


def _get_loop() -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.get_event_loop()


class SimClock:
    _inst: Optional["SimClock"] = None

    def __init__(self, mode: str = "sim"):
        if mode not in ("sim", "standalone"):
            raise ValueError(f"Unknown SimClock mode: {mode}")
        self._mode = mode
        self.env = simpy.Environment()
        self._bg_task: Optional[asyncio.Task] = None

    @classmethod
    def inst(cls) -> "SimClock":
        if cls._inst is None:
            cls._inst = cls(mode="sim")
        return cls._inst

    @classmethod
    def set_mode(cls, mode: str) -> "SimClock":
        cls._inst = cls(mode=mode)
        return cls._inst

    @property
    def mode(self) -> str:
        return self._mode

    def now_ns(self) -> int:
        return int(self.env.now)

    def advance_to(self, t_ns: int) -> None:
        if t_ns > self.env.now:
            try:
                self.env.run(until=t_ns)
            except simpy.core.EmptySchedule:
                # No pending events; just bump the clock.
                self.env._now = t_ns

    def _as_future(self, event: simpy.events.Event) -> asyncio.Future:
        loop = _get_loop()
        fut: asyncio.Future = loop.create_future()

        def _cb(evt):
            if not fut.done():
                fut.set_result(getattr(evt, "value", None))

        event.callbacks.append(_cb)
        return fut

    async def sleep(self, ns: int) -> None:
        if ns <= 0:
            return
        await self._as_future(self.env.timeout(ns))

    async def wait_until(self, t_ns: int) -> None:
        delta = t_ns - int(self.env.now)
        if delta > 0:
            await self.sleep(delta)

    async def _wallclock_pump(self, poll_s: float = 0.001) -> None:
        start = time.monotonic()
        while True:
            await asyncio.sleep(poll_s)
            self.advance_to(int((time.monotonic() - start) * 1e9))

    def start_standalone(self, poll_s: float = 0.001) -> None:
        """Start the wallclock pump. Call from within an asyncio context."""
        if self._mode != "standalone":
            raise RuntimeError("start_standalone requires mode='standalone'")
        if self._bg_task is None:
            self._bg_task = _get_loop().create_task(self._wallclock_pump(poll_s))


@hif.api
class SimClockAPI(object):
    """SV-facing API. SV calls advance_to($time) to sync the simpy clock."""

    def __init__(self):
        self._clock = SimClock.inst()

    @hif.exp
    def advance_to(self, t: int):
        self._clock.advance_to(t)
