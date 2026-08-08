"""External-clock scheduling: the pyhdl-if seam, rehearsed in pure Python.

In simulation, time belongs to the SystemVerilog scheduler. pyhdl-if exposes it
to Python as two imported tasks on a time-service object:

    now_ns() -> int             # current simulation time
    async wait_ns(delay_ns)     # blocking imp task: returns after #delay_ns

``SimScheduler`` implements the engine's ``Scheduler`` protocol on top of any
object with that shape, running on the (single) asyncio loop. ``SpoofClock`` is
a Python-generated stand-in for the SV side: a discrete-event virtual clock
that services ``wait_ns`` calls in deadline order, advancing simulated time
only when the Python side is quiescent — the same rendezvous discipline a
co-simulation gives you. Swapping ``SpoofClock`` for the real pyhdl-if imp
object is the only change needed to run under a simulator; the engine and
``SimScheduler`` are untouched.

Everything (engine calls, timer callbacks, deliveries) executes on one asyncio
loop, so the engine's atomic-entry contract holds exactly as it does standalone.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
from typing import Callable, Protocol, runtime_checkable

from .scheduler import TimerHandle

NS = 1_000_000_000


@runtime_checkable
class ExternalTimeService(Protocol):
    """The time-service boundary as pyhdl-if will present it."""

    def now_ns(self) -> int: ...
    async def wait_ns(self, delay_ns: int) -> None: ...


class _SimHandle(TimerHandle):
    __slots__ = ("task",)

    def __init__(self, id_: int, deadline: float, cb: Callable[[], None]):
        super().__init__(id_, deadline, cb)
        self.task: asyncio.Task | None = None


class SimScheduler:
    """``Scheduler`` implementation over an :class:`ExternalTimeService`.

    ``call_later`` spawns a task that awaits ``wait_ns`` on the service and
    then invokes the callback; ``now`` reads the service clock. Callbacks
    therefore fire at simulation timestamps, on the loop, one at a time.
    """

    def __init__(self, svc: ExternalTimeService, loop: asyncio.AbstractEventLoop | None = None):
        self._svc = svc
        self._loop = loop or asyncio.get_event_loop()
        self._counter = itertools.count()

    def now(self) -> float:
        return self._svc.now_ns() / NS

    def call_later(self, delay_s: float, cb: Callable[[], None]) -> TimerHandle:
        delay_ns = max(0, int(round(delay_s * NS)))
        h = _SimHandle(next(self._counter), self.now() + delay_s, cb)
        h.task = self._loop.create_task(self._run(h, delay_ns))
        return h

    async def _run(self, h: _SimHandle, delay_ns: int) -> None:
        try:
            await self._svc.wait_ns(delay_ns)
        except asyncio.CancelledError:
            return
        if not h.cancelled:
            h.cb()

    def cancel(self, h: TimerHandle | None) -> None:
        if h is None:
            return
        h.cancelled = True
        task = getattr(h, "task", None)
        if task is not None and not task.done():
            task.cancel()


class SpoofClock:
    """A fake simulator time service (the Python-generated external clock).

    Discrete-event semantics: ``wait_ns`` registers a waiter; the drive
    coroutines (:meth:`run_for` / :meth:`run_until_idle`) let the asyncio loop
    quiesce, then jump virtual time to the earliest waiter's deadline and wake
    it — one waiter per step, in (deadline, registration) order, exactly like
    an HDL scheduler draining its time wheel.
    """

    def __init__(self, settle_rounds: int = 8):
        self._now_ns = 0
        self._heap: list[tuple[int, int, asyncio.Future]] = []
        self._counter = itertools.count()
        self._settle_rounds = settle_rounds

    # -- ExternalTimeService interface ------------------------------------
    def now_ns(self) -> int:
        return self._now_ns

    async def wait_ns(self, delay_ns: int) -> None:
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._heap, (self._now_ns + max(0, int(delay_ns)), next(self._counter), fut))
        await fut

    # -- drive ------------------------------------------------------------
    async def _settle(self) -> None:
        # Let engine cascades and freshly spawned wait_ns tasks reach their
        # first await before time moves.
        for _ in range(self._settle_rounds):
            await asyncio.sleep(0)

    def _pop_live(self) -> tuple[int, asyncio.Future] | None:
        while self._heap:
            deadline, _, fut = heapq.heappop(self._heap)
            if not fut.cancelled():
                return deadline, fut
        return None

    async def run_for(self, sim_ns: int) -> int:
        """Advance virtual time by ``sim_ns``, waking due waiters in order."""
        target = self._now_ns + int(sim_ns)
        fired = 0
        await self._settle()
        while self._heap and self._heap[0][0] <= target:
            nxt = self._pop_live()
            if nxt is None:
                break
            deadline, fut = nxt
            if deadline > target:            # popped a live one beyond target
                heapq.heappush(self._heap, (deadline, next(self._counter), fut))
                break
            self._now_ns = max(self._now_ns, deadline)
            fut.set_result(None)
            fired += 1
            await self._settle()
        self._now_ns = target
        await self._settle()
        return fired

    async def run_until_idle(self, max_events: int = 100000) -> int:
        """Advance virtual time until no waiters remain (timer-storm guarded)."""
        fired = 0
        await self._settle()
        while True:
            nxt = self._pop_live()
            if nxt is None:
                return fired
            deadline, fut = nxt
            self._now_ns = max(self._now_ns, deadline)
            fut.set_result(None)
            fired += 1
            if fired > max_events:
                raise RuntimeError("run_until_idle exceeded max_events (timer storm?)")
            await self._settle()

    @property
    def pending(self) -> int:
        return sum(1 for _, _, f in self._heap if not f.cancelled())
