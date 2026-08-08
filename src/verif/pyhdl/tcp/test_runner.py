"""Python side of the TCP co-simulation testbench.

Defines the Call-API surface (time service, runner) and the session sequences
that run as the body of pyhdl-if's shipped UVM sequence proxy. The TCP model
(tcp_model package) provides the engines; SystemVerilog provides transport.
"""

import asyncio
import collections
import heapq
import itertools
import logging
import typing

import hdl_if as hif
from hdl_if.uvm import uvm_sequence_impl

import tcp_item_mirror  # noqa: F401 (registers the tcp_item mirror)
import uvm_mirror
from tcp_model import Flags, TcpEngine, TcpSegment, TimerConfig
from tcp_model.ports.sim_clock import SimScheduler

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SIDE_A = 0
SIDE_B = 1


# ---------------------------------------------------------------------------
# Time service: SV implements these; Python (SimScheduler via TimeMux) calls.
# ---------------------------------------------------------------------------
@hif.api
class TimeServiceAPI(object):
    @hif.imp
    def now_ns(self) -> int: ...

    @hif.imp
    async def wait_ns(self, delay_ns: int): ...


# ---------------------------------------------------------------------------
# Shared state between the runner API and the session sequences
# ---------------------------------------------------------------------------
class _State:
    ts: typing.Optional[TimeServiceAPI] = None
    errors: int = 0
    # Segments each side put on the wire / took off it, by side index
    sent: typing.Dict[int, typing.List[bytes]] = {SIDE_A: [], SIDE_B: []}
    rcvd: typing.Dict[int, typing.List[bytes]] = {SIDE_A: [], SIDE_B: []}
    # Optional per-side consumer (P3 hands received segments to a TcpEngine)
    rx_sink: typing.Dict[int, typing.Callable[[bytes], None]] = {}

    # --- P3: engines, their pending tx segments, and app-level receive data
    mux: typing.Any = None
    engine: typing.Dict[int, typing.Any] = {}
    txq: typing.Dict[int, typing.Any] = {}
    tx_event: typing.Dict[int, typing.Any] = {}
    app_rx: typing.Dict[int, bytearray] = {}
    app_tx: typing.Dict[int, bytearray] = {}
    done: typing.Any = None

    # T5 loss injection bookkeeping
    seg_idx: typing.Dict[int, int] = {}
    dropped: typing.List[int] = []

    # Stimulus configuration, set from SV plusargs via TcpRunnerAPI.configure
    num_msgs: int = 8
    seed: int = 1
    max_msg: int = 512

    @staticmethod
    def init_engine_state():
        _State.txq = {SIDE_A: collections.deque(), SIDE_B: collections.deque()}
        _State.tx_event = {SIDE_A: asyncio.Event(), SIDE_B: asyncio.Event()}
        _State.app_rx = {SIDE_A: bytearray(), SIDE_B: bytearray()}
        _State.app_tx = {SIDE_A: bytearray(), SIDE_B: bytearray()}
        _State.done = asyncio.Event()


def _err(msg: str):
    _State.errors += 1
    logger.error(msg)


# ---------------------------------------------------------------------------
# Only one pyhdl-if imp task may be in flight at a time.
#
# Several concurrent activations wedge Verilator's inactive region
# (DIDNOTCONVERGE). That covers wait_ns against itself, and also a sequence's
# start_item/finish_item overlapping another side's wait_ns -- which is exactly
# what happens once engines are running, since side B wakes to drive an ACK
# while side A is asleep pumping time. Every SV-blocking call therefore takes
# this lock. Side A pumps in short quanta so side B is never starved.
# ---------------------------------------------------------------------------
_sv_lock: typing.Optional[typing.Any] = None


def sv_lock():
    global _sv_lock
    if _sv_lock is None:
        _sv_lock = asyncio.Lock()
    return _sv_lock


# ---------------------------------------------------------------------------
# Runner API: exp = implemented here, called from SV
# ---------------------------------------------------------------------------
@hif.api
class TcpRunnerAPI(object):
    @hif.exp
    def init_ts(self, ts: TimeServiceAPI):
        _State.ts = ts
        logger.info("Runner initialized with time service")

    @hif.exp
    def configure(self, num_msgs: int, seed: int, max_msg: int):
        _State.num_msgs = int(num_msgs)
        _State.seed = int(seed)
        _State.max_msg = int(max_msg)
        logger.info(f"config: num_msgs={_State.num_msgs} seed={_State.seed} "
                    f"max_msg={_State.max_msg}")

    @hif.exp
    def rx_segment(self, side: int, data: typing.List):
        """A monitor observed a complete segment arriving at `side`."""
        seg = bytes(data)
        _State.rcvd[side].append(seg)
        sink = _State.rx_sink.get(side)
        if sink is not None:
            sink(seg)

    @hif.exp
    def report(self) -> int:
        """Cross-check both directions; returns the error count for SV."""
        for src, dst in ((SIDE_A, SIDE_B), (SIDE_B, SIDE_A)):
            sent, got = _State.sent[src], _State.rcvd[dst]
            tag = f"{'AB'[src]}->{'AB'[dst]}"
            if sent != got:
                _err(f"{tag}: {len(sent)} sent vs {len(got)} received")
                for i, (a, b) in enumerate(zip(sent, got)):
                    if a != b:
                        _err(f"{tag}: first differing segment {i}\n"
                             f"  sent: {a.hex()}\n  got:  {b.hex()}")
                        break
            else:
                logger.info(f"Matched {len(got)}/{len(sent)} segments {tag}")
        if _State.errors:
            logger.error(f"report: {_State.errors} error(s)")
        return _State.errors


# ---------------------------------------------------------------------------
# Snapshot helpers for the UVM wrapper's pack()/unpack() field transport.
#
# req.pack() returns a detached snapshot of the item's registered fields; the
# idiom is read-modify-write (pack, set fields, unpack). Never unpack a freshly
# constructed snapshot -- it would zero every field you did not set.
#
# options/payload are byte queues; the element width is pinned to 8 bits via
# uvm_mirror.bind() + the tcp_item mirror, rather than inferred from the data.
# ---------------------------------------------------------------------------
def _zero_item(v):
    v.src_port = 0
    v.dst_port = 0
    v.seq_num = 0
    v.ack_num = 0
    v.flags = 0
    v.window = 0
    v.checksum = 0
    v.urgent_ptr = 0
    v.options = []
    v.payload = []


def _apply_segment(v, seg_bytes: bytes):
    """Fill a tcp_item snapshot from a wire-image segment."""
    hdr_len = (seg_bytes[12] >> 4) * 4

    _zero_item(v)
    v.src_port = int.from_bytes(seg_bytes[0:2], "big")
    v.dst_port = int.from_bytes(seg_bytes[2:4], "big")
    v.seq_num = int.from_bytes(seg_bytes[4:8], "big")
    v.ack_num = int.from_bytes(seg_bytes[8:12], "big")
    v.flags = seg_bytes[13]
    v.window = int.from_bytes(seg_bytes[14:16], "big")
    v.checksum = int.from_bytes(seg_bytes[16:18], "big")
    v.urgent_ptr = int.from_bytes(seg_bytes[18:20], "big")
    v.options = list(seg_bytes[20:hdr_len])
    v.payload = list(seg_bytes[hdr_len:])


_SNAPSHOT_FIELDS = ("src_port", "dst_port", "seq_num", "ack_num", "flags", "window",
                    "checksum", "urgent_ptr", "options", "payload")


def _snapshot_diff(a, b) -> typing.List[str]:
    return [f for f in _SNAPSHOT_FIELDS if getattr(a, f) != getattr(b, f)]


# ---------------------------------------------------------------------------
# Session-sequence plumbing
#
# Each side runs its own Python sequence as the body of a tcp_py_seq proxy.
# The base does the bookkeeping every sequence needs: bind the mirror once,
# then push a wire-image segment through create_req / pack / unpack /
# start_item / finish_item.
# ---------------------------------------------------------------------------
class _SessionBase(uvm_sequence_impl):

    SIDE: int = SIDE_A

    def _new_req(self):
        req = self.proxy.create_req()
        if not uvm_mirror.is_bound(req):
            applied = uvm_mirror.bind(req, tcp_item_mirror.tcp_item)
            logger.info(f"Mirror bound: queue element widths {applied}")
        return req

    async def send_segment(self, seg_bytes: bytes):
        """Drive one wire-image segment out of this side's sequencer."""
        async with sv_lock():
            req = self._new_req()
            v = req.pack()
            _apply_segment(v, seg_bytes)
            req.unpack(v)
            _State.sent[self.SIDE].append(seg_bytes)
            await self.proxy.start_item(req)
            await self.proxy.finish_item(req)


# ---------------------------------------------------------------------------
# T0 smoke sequence: time-service check, mirror/field-transport checks, then
# one canned segment through the full proxy path.
# ---------------------------------------------------------------------------
def _seg(src_port, dst_port, seq, flags, payload=b"", **kw) -> bytes:
    return TcpSegment(src_port=src_port, dst_port=dst_port, seq=seq,
                      flags=flags, payload=payload, **kw).build()


def _smoke_segment() -> bytes:
    return _seg(0x1234, 0x4321, 0x01020304, Flags.SYN | Flags.ACK,
                payload=bytes(range(16)), ack=0x05060708, window=0xBEEF, mss=1460)


class SmokeSeq(_SessionBase):

    SIDE = SIDE_A

    async def body(self):
        try:
            ts = _State.ts
            if ts is None:
                _err("SmokeSeq: time service not initialized")
                return

            t0 = ts.now_ns()
            await ts.wait_ns(100)
            t1 = ts.now_ns()
            if t1 - t0 != 100:
                _err(f"wait_ns(100) advanced {t1 - t0} ns (t0={t0}, t1={t1})")
            else:
                logger.info(f"Time service OK: {t0} -> {t1} ns")

            req = self._new_req()
            v = req.pack()

            fresh = {k: hex(x) for k, x in vars(v).items() if isinstance(x, int) and x}
            if fresh:
                _err(f"field transport broken: fresh item packs non-zero: {fresh}")

            # Pinning proof: an all-zero payload is the case pyhdl-if's
            # inference gets wrong -- max(abs(x)).bit_length() would make the
            # elements 1 bit wide. With the mirror-declared width it survives.
            probe = req.pack()
            _zero_item(probe)
            probe.payload = [0] * 16
            req.unpack(probe)
            if list(req.pack().payload) != [0] * 16:
                _err("queue width not pinned: all-zero payload did not survive")
            else:
                logger.info("Queue width pinned: 16 zero bytes survived round-trip")

            expected = _smoke_segment()
            _apply_segment(v, expected)
            req.unpack(v)
            diff = _snapshot_diff(v, req.pack())
            if diff:
                _err('field transport round-trip mismatch on: ' + ', '.join(diff))
            else:
                logger.info(f"Field transport round-trip OK (all {len(_SNAPSHOT_FIELDS)} fields)")

            _State.sent[self.SIDE].append(expected)
            await self.proxy.start_item(req)
            await self.proxy.finish_item(req)
            logger.info(f"SmokeSeq: sent {len(expected)}-byte segment")
        except Exception as e:
            _err(f"SmokeSeq raised: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# P2 transport test: both directions driven with canned segments, so the
# scoreboard exercises A->B and B->A and the far-side monitors are compared
# against what each side actually put on the wire.
# ---------------------------------------------------------------------------
XPORT_SEGMENTS = 4


class _XportSeq(_SessionBase):

    async def body(self):
        try:
            base_port = 0x1000 + 0x100 * self.SIDE
            for i in range(XPORT_SEGMENTS):
                # vary payload length (including 0) and option presence
                payload = bytes((self.SIDE * 16 + i + n) & 0xFF for n in range(i * 7))
                seg = _seg(base_port + i, 0x2000 + i, 0x1000 * (i + 1),
                           Flags.PSH | Flags.ACK if payload else Flags.ACK,
                           payload=payload, ack=i, window=0x2000 + i,
                           mss=(1460 if i % 2 else None))
                await self.send_segment(seg)
                logger.info(f"side {self.SIDE}: sent segment {i} ({len(seg)} B)")
        except Exception as e:
            _err(f"XportSeq side {self.SIDE} raised: {type(e).__name__}: {e}")


class XportSeqA(_XportSeq):
    SIDE = SIDE_A


class XportSeqB(_XportSeq):
    SIDE = SIDE_B


# ---------------------------------------------------------------------------
# Time-service probe (risk R3).
#
# Concurrent wait_ns activations are NOT usable: issuing three at once via
# asyncio.gather wedges Verilator's inactive region (DIDNOTCONVERGE at the
# converge limit) before any of them returns. The engines' SimScheduler spawns
# one task per armed timer, so it cannot drive the time service directly --
# hence TimeMux, which keeps exactly one wait_ns in flight.
#
# What this probe checks is the property TimeMux actually relies on: that
# *sequential* waits accumulate simulation time exactly.
# ---------------------------------------------------------------------------
class TimeServiceProbe(_SessionBase):

    SIDE = SIDE_A

    async def body(self):
        ts = _State.ts
        if ts is None:
            _err("probe: no time service")
            return
        try:
            t0 = ts.now_ns()
            elapsed = 0
            for d in (300, 100, 200):
                await ts.wait_ns(d)
                elapsed += d
                got = ts.now_ns() - t0
                if got != elapsed:
                    _err(f"sequential wait_ns({d}): expected +{elapsed} ns, got +{got}")
            logger.info(f"Sequential wait_ns OK: {elapsed} ns accumulated exactly")
        except Exception as e:
            _err(f"time-service probe raised: {type(e).__name__}: {e}")


# ===========================================================================
# P3: two TcpEngine instances talking to each other through the SV wire
# ===========================================================================

# Timers scaled so a handshake/teardown completes in milliseconds of sim time
# rather than the RFC's seconds (same values the model's own harness uses).
TB_TIMERS = dict(rto_initial=0.001, rto_min=0.001, rto_max=0.008, msl=0.0005)
TB_MSS = 256

PORT_A = 50000
PORT_B = 80


class TimeMux:
    """``ExternalTimeService`` for ``SimScheduler`` with one wait_ns in flight.

    ``SimScheduler`` spawns a task per armed timer, and each would call
    ``wait_ns``. Several concurrent activations wedge the simulator (see the
    TimeServiceProbe note), so waits are funnelled through a deadline heap
    here: callers get a future, and a single pump loop performs the one real
    ``wait_ns`` and resolves whatever has come due.

    Time only advances while :meth:`pump` runs, which is deliberate -- it keeps
    simulation time under the testbench's control the way ``SpoofClock.run_for``
    does in the model's standalone harness.
    """

    def __init__(self, ts):
        self._ts = ts
        self._heap: typing.List[typing.Tuple[int, int, typing.Any]] = []
        self._ctr = itertools.count()

    # -- ExternalTimeService ------------------------------------------------
    def now_ns(self) -> int:
        return self._ts.now_ns()

    async def wait_ns(self, delay_ns: int) -> None:
        deadline = self.now_ns() + max(0, int(delay_ns))
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._heap, (deadline, next(self._ctr), fut))
        await fut

    # -- driving ------------------------------------------------------------
    @property
    def pending(self) -> int:
        return sum(1 for _, _, f in self._heap if not f.cancelled())

    def _fire_due(self) -> int:
        """Resolve every waiter whose deadline has passed. Returns how many."""
        fired = 0
        now = self.now_ns()
        while self._heap and self._heap[0][0] <= now:
            _, _, fut = heapq.heappop(self._heap)
            if not fut.cancelled():
                fut.set_result(None)
                fired += 1
        return fired

    async def _settle(self) -> None:
        # Let engine cascades and freshly armed timers reach their first await
        for _ in range(8):
            await asyncio.sleep(0)

    async def step(self, max_ns: int) -> None:
        """Advance simulation time by at most ``max_ns``, servicing timers."""
        await self._settle()
        self._fire_due()
        await self._settle()

        target = self.now_ns() + max_ns
        live = [d for d, _, f in self._heap if not f.cancelled()]
        if live:
            target = min(target, min(live))

        delta = target - self.now_ns()
        if delta > 0:
            async with sv_lock():
                await self._ts.wait_ns(delta)

        self._fire_due()
        await self._settle()


def _tx_of(side: int):
    """Return a tx() callback that queues a segment for `side` to drive."""
    def tx(seg: bytes) -> None:
        _State.txq[side].append(seg)
        _State.tx_event[side].set()
    return tx


def build_engines() -> typing.Tuple[typing.Any, typing.Any]:
    """Create the two engines, wired to the SV transport through the runner."""
    _State.init_engine_state()
    mux = TimeMux(_State.ts)
    sched = SimScheduler(mux, asyncio.get_running_loop())
    cfg = TimerConfig(**TB_TIMERS)

    a = TcpEngine(local_port=PORT_A, remote_port=PORT_B, scheduler=sched,
                  tx=_tx_of(SIDE_A), snd_mss=TB_MSS, iss=1000, timers=cfg,
                  on_data=lambda d: _State.app_rx[SIDE_A].extend(d), name="A")
    b = TcpEngine(local_port=PORT_B, remote_port=PORT_A, scheduler=sched,
                  tx=_tx_of(SIDE_B), snd_mss=TB_MSS, iss=5000, timers=cfg,
                  on_data=lambda d: _State.app_rx[SIDE_B].extend(d), name="B")

    # A monitor on side X observes what the far side put on the wire, so its
    # segments are what engine X receives.
    _State.rx_sink[SIDE_A] = a.on_segment
    _State.rx_sink[SIDE_B] = b.on_segment

    _State.mux = mux
    _State.engine[SIDE_A] = a
    _State.engine[SIDE_B] = b
    return a, b


class _EngineSeq(_SessionBase):
    """Common body: drain this side's tx queue onto the wire, forever."""

    async def drain(self) -> None:
        q = _State.txq[self.SIDE]
        while q:
            await self.send_segment(q.popleft())


class EngineSeqB(_EngineSeq):
    """Side B: purely reactive -- wake when engine B emits, then drive."""

    SIDE = SIDE_B

    async def body(self):
        try:
            ev = _State.tx_event[self.SIDE]
            while not _State.done.is_set():
                await ev.wait()
                ev.clear()
                await self.drain()
            await self.drain()
        except Exception as e:
            _err(f"EngineSeqB raised: {type(e).__name__}: {e}")


class HandshakeSeqA(_EngineSeq):
    """Side A: opens the connection, drives its own segments, and pumps time."""

    SIDE = SIDE_A

    async def body(self):
        try:
            a, b = build_engines()

            b.passive_open()
            a.active_open()
            logger.info(f"open: A={a.state.name} B={b.state.name}")

            deadline_ns = _State.ts.now_ns() + 20_000_000  # 20 ms of sim time
            while _State.ts.now_ns() < deadline_ns:
                await self.drain()
                if a.state.name == "ESTABLISHED" and b.state.name == "ESTABLISHED":
                    break
                await _State.mux.step(200_000)  # <= 200 us per step

            logger.info(f"after handshake: A={a.state.name} B={b.state.name} "
                        f"at {_State.ts.now_ns()} ns")

            if a.state.name != "ESTABLISHED" or b.state.name != "ESTABLISHED":
                _err(f"handshake incomplete: A={a.state.name} B={b.state.name}")
            else:
                ta, tb = a.get_tcb_snapshot(), b.get_tcb_snapshot()
                if ta["snd_nxt"] != tb["rcv_nxt"] or tb["snd_nxt"] != ta["rcv_nxt"]:
                    _err(f"sequence spaces disagree: A={ta} B={tb}")
                else:
                    logger.info("Handshake OK: both ESTABLISHED, sequence spaces agree")

            _State.done.set()
            _State.tx_event[SIDE_B].set()  # release side B
        except Exception as e:
            _err(f"HandshakeSeqA raised: {type(e).__name__}: {e}")
            _State.done.set()
            _State.tx_event[SIDE_B].set()


# ---------------------------------------------------------------------------
# T2 / T3: application data over the established connection.
#
# Both engines live in this interpreter, so side A's sequence drives the *app*
# calls for both peers (a.send(...) / b.send(...)). Only the resulting segments
# are protocol traffic, and every one of those still crosses the SV wire: B's
# engine queues them and EngineSeqB drives them from side B's sequencer.
# ---------------------------------------------------------------------------
def _rand_msg(rng, n_max: int) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(rng.randint(1, n_max)))


class _DataSeqA(_EngineSeq):

    SIDE = SIDE_A
    BIDIR = False

    async def run_until(self, cond, timeout_ns: int, quantum_ns: int = 20_000) -> bool:
        """Drain and advance time until `cond()` holds or the budget runs out."""
        deadline = _State.ts.now_ns() + timeout_ns
        while _State.ts.now_ns() < deadline:
            await self.drain()
            if cond():
                return True
            await _State.mux.step(quantum_ns)
        await self.drain()
        return cond()

    async def body(self):
        import random
        try:
            a, b = build_engines()
            rng = random.Random(_State.seed)

            b.passive_open()
            a.active_open()
            ok = await self.run_until(
                lambda: a.state.name == "ESTABLISHED" and b.state.name == "ESTABLISHED",
                20_000_000)
            if not ok:
                _err(f"handshake failed: A={a.state.name} B={b.state.name}")
                return
            logger.info(f"connected at {_State.ts.now_ns()} ns; "
                        f"sending {_State.num_msgs} message(s) per direction"
                        if self.BIDIR else
                        f"connected at {_State.ts.now_ns()} ns; "
                        f"sending {_State.num_msgs} message(s) A->B")

            for i in range(_State.num_msgs):
                msg = _rand_msg(rng, _State.max_msg)
                _State.app_tx[SIDE_A] += msg
                a.send(msg)
                if self.BIDIR:
                    msg_b = _rand_msg(rng, _State.max_msg)
                    _State.app_tx[SIDE_B] += msg_b
                    b.send(msg_b)

                # Let this message reach the peer and be acknowledged before
                # queueing the next, so the run stays bounded and in step.
                want_b = len(_State.app_tx[SIDE_A])
                want_a = len(_State.app_tx[SIDE_B])
                done = await self.run_until(
                    lambda: (len(_State.app_rx[SIDE_B]) >= want_b
                             and len(_State.app_rx[SIDE_A]) >= want_a
                             and a.get_tcb_snapshot()["retx_depth"] == 0
                             and b.get_tcb_snapshot()["retx_depth"] == 0),
                    5_000_000)
                if not done:
                    _err(f"message {i} did not complete: "
                         f"A->B {len(_State.app_rx[SIDE_B])}/{want_b} "
                         f"B->A {len(_State.app_rx[SIDE_A])}/{want_a}")
                    break
                if _State.num_msgs <= 20 or (i + 1) % 100 == 0:
                    logger.info(f"  {i + 1}/{_State.num_msgs} messages complete "
                                f"at {_State.ts.now_ns()} ns")

            self._check_streams(a, b)
        except Exception as e:
            _err(f"{type(self).__name__} raised: {type(e).__name__}: {e}")
        finally:
            _State.done.set()
            _State.tx_event[SIDE_B].set()

    def _check_streams(self, a, b):
        for src, dst in ((SIDE_A, SIDE_B),) + (((SIDE_B, SIDE_A),) if self.BIDIR else ()):
            tag = f"{'AB'[src]}->{'AB'[dst]}"
            sent, got = bytes(_State.app_tx[src]), bytes(_State.app_rx[dst])
            if sent != got:
                _err(f"{tag} app data mismatch: {len(sent)} sent vs {len(got)} delivered")
                for i, (x, y) in enumerate(zip(sent, got)):
                    if x != y:
                        _err(f"{tag} first differing byte at offset {i}: {x:#04x} vs {y:#04x}")
                        break
            else:
                logger.info(f"Matched {_State.num_msgs}/{_State.num_msgs} messages {tag} "
                            f"({len(sent)} bytes)")
        for eng, side in ((a, SIDE_A), (b, SIDE_B)):
            if eng.state.name != "ESTABLISHED":
                _err(f"side {side} left ESTABLISHED: {eng.state.name}")
            depth = eng.get_tcb_snapshot()["retx_depth"]
            if depth:
                _err(f"side {side} has {depth} unacknowledged segment(s) at end")


class DataSeqA(_DataSeqA):
    """T2: unidirectional, A -> B."""
    BIDIR = False


class BidirSeqA(_DataSeqA):
    """T3: both directions, interleaved."""
    BIDIR = True


# ---------------------------------------------------------------------------
# T4: graceful teardown. Both sides close; the connection must reach CLOSED,
# one side via TIME-WAIT expiry (2*MSL, scaled to 0.5 ms here).
# ---------------------------------------------------------------------------
class TeardownSeqA(_DataSeqA):

    BIDIR = True

    async def body(self):
        import random
        try:
            a, b = build_engines()
            rng = random.Random(_State.seed)

            b.passive_open()
            a.active_open()
            if not await self.run_until(
                    lambda: a.state.name == "ESTABLISHED" and b.state.name == "ESTABLISHED",
                    20_000_000):
                _err(f"handshake failed: A={a.state.name} B={b.state.name}")
                return

            # A little traffic first, so the teardown follows real data
            n = min(_State.num_msgs, 5)
            for _ in range(n):
                msg = _rand_msg(rng, _State.max_msg)
                _State.app_tx[SIDE_A] += msg
                a.send(msg)
                want = len(_State.app_tx[SIDE_A])
                await self.run_until(lambda: len(_State.app_rx[SIDE_B]) >= want, 5_000_000)
            logger.info(f"{n} message(s) delivered; closing")

            a.close()
            await self.run_until(lambda: b.state.name == "CLOSE_WAIT", 5_000_000)
            b.close()

            ok = await self.run_until(
                lambda: a.state.name == "CLOSED" and b.state.name == "CLOSED", 20_000_000)
            logger.info(f"after close: A={a.state.name} B={b.state.name} "
                        f"at {_State.ts.now_ns()} ns")
            if not ok:
                _err(f"teardown incomplete: A={a.state.name} B={b.state.name}")
            else:
                logger.info("Teardown OK: both CLOSED")

            if bytes(_State.app_tx[SIDE_A]) != bytes(_State.app_rx[SIDE_B]):
                _err("data delivered before close does not match")
            else:
                logger.info(f"Matched {n}/{n} messages A->B before teardown")
        except Exception as e:
            _err(f"TeardownSeqA raised: {type(e).__name__}: {e}")
        finally:
            _State.done.set()
            _State.tx_event[SIDE_B].set()


# ---------------------------------------------------------------------------
# T5: segment loss. The driver drops whole A->B segments at fixed indices, so
# the run stays deterministic; retransmission must still deliver every byte in
# order. Drops are counted here rather than in the scoreboard, which is why the
# SV test relaxes its A->B stream compare.
# ---------------------------------------------------------------------------
DROP_INDICES = frozenset({2, 5})


class LossSeqA(_DataSeqA):

    BIDIR = False

    async def send_segment(self, seg_bytes: bytes):
        idx = _State.seg_idx[self.SIDE]
        _State.seg_idx[self.SIDE] += 1
        if idx in DROP_INDICES and len(seg_bytes) > 20:
            # Drop a data-bearing segment: account for it, put nothing on the wire
            _State.dropped.append(idx)
            logger.info(f"  dropping A->B segment {idx} ({len(seg_bytes)} B)")
            return
        await super().send_segment(seg_bytes)

    async def body(self):
        import random
        try:
            a, b = build_engines()
            _State.seg_idx = {SIDE_A: 0, SIDE_B: 0}
            _State.dropped = []
            rng = random.Random(_State.seed)

            b.passive_open()
            a.active_open()
            if not await self.run_until(
                    lambda: a.state.name == "ESTABLISHED" and b.state.name == "ESTABLISHED",
                    20_000_000):
                _err(f"handshake failed: A={a.state.name} B={b.state.name}")
                return

            n = min(_State.num_msgs, 8)
            for i in range(n):
                msg = _rand_msg(rng, _State.max_msg)
                _State.app_tx[SIDE_A] += msg
                a.send(msg)
                want = len(_State.app_tx[SIDE_A])
                if not await self.run_until(
                        lambda: (len(_State.app_rx[SIDE_B]) >= want
                                 and a.get_tcb_snapshot()["retx_depth"] == 0),
                        30_000_000):
                    _err(f"message {i} never recovered "
                         f"({len(_State.app_rx[SIDE_B])}/{want} bytes)")
                    break

            if not _State.dropped:
                _err("loss test dropped nothing -- not exercising retransmission")
            else:
                logger.info(f"Dropped A->B segments {_State.dropped}")

            sent, got = bytes(_State.app_tx[SIDE_A]), bytes(_State.app_rx[SIDE_B])
            if sent != got:
                _err(f"loss recovery failed: {len(sent)} sent vs {len(got)} delivered")
            else:
                logger.info(f"Matched {n}/{n} messages A->B after "
                            f"{len(_State.dropped)} dropped segment(s) ({len(sent)} bytes)")
        except Exception as e:
            _err(f"LossSeqA raised: {type(e).__name__}: {e}")
        finally:
            _State.done.set()
            _State.tx_event[SIDE_B].set()
