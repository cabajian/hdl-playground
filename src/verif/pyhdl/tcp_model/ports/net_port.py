"""Net-port helpers for standalone testing.

The engine emits via a ``tx(seg_bytes)`` callable and is fed received bytes via
``engine.on_segment(seg_bytes)``. These helpers provide the two test topologies:

  * ``CaptureNet`` -- records every emitted segment (parsed) for assertions, and
    lets a test hand-craft and inject segments into the engine.
  * ``Wire``       -- connects two engines back-to-back through a controllable
    channel that can delay, drop, or reorder segments. Combined with the
    ManualScheduler this drives full end-to-end scenarios deterministically.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..core.segment import TcpSegment


class CaptureNet:
    """Records segments the engine emits; supports injecting segments back in."""

    def __init__(self):
        self.sent: list[TcpSegment] = []
        self.raw: list[bytes] = []
        self._engine = None

    def bind(self, engine) -> None:
        self._engine = engine

    def tx(self, data: bytes) -> None:
        self.raw.append(data)
        self.sent.append(TcpSegment.parse(data))

    # convenience for assertions
    @property
    def last(self) -> Optional[TcpSegment]:
        return self.sent[-1] if self.sent else None

    def clear(self) -> None:
        self.sent.clear()
        self.raw.clear()

    def pop_all(self) -> list[TcpSegment]:
        segs = list(self.sent)
        self.clear()
        return segs

    def inject(self, seg: TcpSegment) -> None:
        if self._engine is None:
            raise RuntimeError("CaptureNet.inject requires bind(engine)")
        self._engine.on_segment(seg.build())


class Wire:
    """A controllable channel connecting engine A <-> engine B.

    Delivery is scheduled on the shared scheduler so ManualScheduler.advance drives
    it. A ``filter`` callable may drop/transform segments (e.g. to model loss).
    """

    def __init__(
        self,
        scheduler,
        delay: float = 0.0,
        a_to_b_filter: Optional[Callable[[TcpSegment, int], bool]] = None,
        b_to_a_filter: Optional[Callable[[TcpSegment, int], bool]] = None,
    ):
        self.sched = scheduler
        self.delay = delay
        self.a = None
        self.b = None
        self._a_to_b = a_to_b_filter
        self._b_to_a = b_to_a_filter
        self._count_ab = 0
        self._count_ba = 0
        self.delivered_ab = 0
        self.delivered_ba = 0
        self.dropped = 0

    def connect(self, a, b) -> None:
        self.a = a
        self.b = b

    def tx_from_a(self, data: bytes) -> None:
        self._enqueue(data, to="b")

    def tx_from_b(self, data: bytes) -> None:
        self._enqueue(data, to="a")

    def _enqueue(self, data: bytes, to: str) -> None:
        seg = TcpSegment.parse(data)
        if to == "b":
            n = self._count_ab
            self._count_ab += 1
            if self._a_to_b is not None and not self._a_to_b(seg, n):
                self.dropped += 1
                return
            target = self.b
            counter_attr = "delivered_ab"
        else:
            n = self._count_ba
            self._count_ba += 1
            if self._b_to_a is not None and not self._b_to_a(seg, n):
                self.dropped += 1
                return
            target = self.a
            counter_attr = "delivered_ba"

        def deliver():
            setattr(self, counter_attr, getattr(self, counter_attr) + 1)
            target.on_segment(data)

        if self.delay > 0:
            self.sched.call_later(self.delay, deliver)
        else:
            deliver()
