"""The clock/scheduler port -- the one piece swapped between run-modes.

The engine never reads wall-clock time; it only calls ``call_later`` / ``cancel``
and reads ``now()``. In simulation this is backed by SystemVerilog time (via
pyhdl-if ``wait_ns``); for pure unit tests we use the ``ManualScheduler`` below,
which advances time explicitly so RTO/persist/TIME-WAIT tests are instant and
deterministic.
"""

from __future__ import annotations

import heapq
import itertools
import time
from typing import Callable, Protocol, runtime_checkable


class TimerHandle:
    __slots__ = ("id", "deadline", "cb", "cancelled")

    def __init__(self, id_: int, deadline: float, cb: Callable[[], None]):
        self.id = id_
        self.deadline = deadline
        self.cb = cb
        self.cancelled = False


@runtime_checkable
class Scheduler(Protocol):
    def now(self) -> float: ...
    def call_later(self, delay_s: float, cb: Callable[[], None]) -> TimerHandle: ...
    def cancel(self, h: TimerHandle) -> None: ...


class ManualScheduler:
    """Virtual-time scheduler. Time only moves when ``advance`` is called."""

    def __init__(self, start: float = 0.0):
        self._now = float(start)
        self._heap: list[tuple[float, int, TimerHandle]] = []
        self._counter = itertools.count()

    def now(self) -> float:
        return self._now

    def call_later(self, delay_s: float, cb: Callable[[], None]) -> TimerHandle:
        if delay_s < 0:
            delay_s = 0.0
        h = TimerHandle(next(self._counter), self._now + delay_s, cb)
        heapq.heappush(self._heap, (h.deadline, h.id, h))
        return h

    def cancel(self, h: TimerHandle | None) -> None:
        if h is not None:
            h.cancelled = True

    # --- test driver ------------------------------------------------------
    def advance(self, dt: float) -> int:
        """Advance virtual time by ``dt`` seconds, firing due callbacks in order.

        Returns the number of callbacks fired. Callbacks scheduled during firing
        are honored if they fall within the advanced window.
        """
        target = self._now + dt
        fired = 0
        while self._heap and self._heap[0][0] <= target:
            deadline, _id, h = heapq.heappop(self._heap)
            self._now = max(self._now, deadline)
            if h.cancelled:
                continue
            h.cb()
            fired += 1
        self._now = target
        return fired

    def run_until_idle(self, max_steps: int = 100000) -> int:
        """Fire all pending timers in time order until none remain."""
        fired = 0
        steps = 0
        while self._heap:
            steps += 1
            if steps > max_steps:
                raise RuntimeError("run_until_idle exceeded max_steps (timer storm?)")
            deadline, _id, h = heapq.heappop(self._heap)
            self._now = max(self._now, deadline)
            if h.cancelled:
                continue
            h.cb()
            fired += 1
        return fired

    @property
    def pending(self) -> int:
        return sum(1 for _, _, h in self._heap if not h.cancelled)


class WallClockScheduler:
    """Real-time scheduler: ``now()`` tracks the monotonic wall clock.

    Single-threaded by design: callbacks fire on the caller's thread inside
    ``run_for`` / ``run_until_idle``, which sleep until each deadline. This
    preserves the engine's atomic-entry contract (no locks, no callback
    thread racing user calls) while timer behavior plays out in real elapsed
    time. Use ms-scale ``TimerConfig`` periods to keep test wall time short.
    """

    def __init__(self):
        self._t0 = time.monotonic()
        self._heap: list[tuple[float, int, TimerHandle]] = []
        self._counter = itertools.count()

    def now(self) -> float:
        return time.monotonic() - self._t0

    def call_later(self, delay_s: float, cb: Callable[[], None]) -> TimerHandle:
        if delay_s < 0:
            delay_s = 0.0
        h = TimerHandle(next(self._counter), self.now() + delay_s, cb)
        heapq.heappush(self._heap, (h.deadline, h.id, h))
        return h

    def cancel(self, h: TimerHandle | None) -> None:
        if h is not None:
            h.cancelled = True

    # --- drivers (caller thread) ------------------------------------------
    def _fire_due(self) -> int:
        fired = 0
        while self._heap and self._heap[0][0] <= self.now():
            _, _, h = heapq.heappop(self._heap)
            if h.cancelled:
                continue
            h.cb()
            fired += 1
        return fired

    def run_for(self, dt: float) -> int:
        """Run in real time for ``dt`` seconds, firing due callbacks in order."""
        target = self.now() + dt
        fired = 0
        while True:
            fired += self._fire_due()
            remaining = target - self.now()
            if remaining <= 0:
                return fired
            next_dl = self._heap[0][0] if self._heap else None
            sleep_for = remaining if next_dl is None else min(remaining, next_dl - self.now())
            if sleep_for > 0:
                time.sleep(sleep_for)

    def run_until_idle(self, timeout: float = 10.0) -> int:
        """Fire pending timers (in real time) until none remain or ``timeout``."""
        deadline = self.now() + timeout
        fired = 0
        while self._heap:
            if self.now() > deadline:
                raise RuntimeError("run_until_idle wall-clock timeout")
            fired += self._fire_due()
            if not self._heap:
                break
            wait = max(0.0, min(self._heap[0][0] - self.now(), deadline - self.now()))
            if wait > 0:
                time.sleep(wait)
        return fired

    @property
    def pending(self) -> int:
        return sum(1 for _, _, h in self._heap if not h.cancelled)
