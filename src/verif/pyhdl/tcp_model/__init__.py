"""Standalone, RFC 9293-oriented TCP engine (synchronous, runtime-agnostic).

The engine core has no dependency on any simulator, pyhdl-if, asyncio, or SimPy.
It talks to the world only through three injected ports:

  * App port      -- user calls: active_open/passive_open/send/recv/close/abort
  * Net port      -- a ``tx(seg_bytes)`` callable it emits segments on, and
                     ``on_segment(seg_bytes)`` fed to it for received segments
  * Scheduler     -- timers via call_later/cancel (see ports.scheduler)

This top-level package re-exports the most commonly used names.
"""

from .core.engine import TcpEngine, ReceiveResult
from .core.tcb import ConnState, TcbSeed, seed_established, TCB
from .core.timers import TimerConfig
from .core.segment import TcpSegment, Flags
from .ports.scheduler import Scheduler, ManualScheduler, WallClockScheduler
from .ports.net_port import CaptureNet, Wire
from .ports.sim_clock import ExternalTimeService, SimScheduler, SpoofClock

__all__ = [
    "TcpEngine",
    "ReceiveResult",
    "ConnState",
    "TcbSeed",
    "seed_established",
    "TCB",
    "TimerConfig",
    "TcpSegment",
    "Flags",
    "Scheduler",
    "ManualScheduler",
    "WallClockScheduler",
    "ExternalTimeService",
    "SimScheduler",
    "SpoofClock",
    "CaptureNet",
    "Wire",
]
