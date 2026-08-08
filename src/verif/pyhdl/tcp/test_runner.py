"""Python side of the TCP co-simulation testbench.

Defines the Call-API surface (time service, runner) and the session sequences
that run as the body of pyhdl-if's shipped UVM sequence proxy. The TCP model
(tcp_model package) provides the engines; SystemVerilog provides transport.
"""

import logging
import typing

import hdl_if as hif
from hdl_if.uvm import uvm_sequence_impl

import tcp_item_mirror  # noqa: F401 (registers the tcp_item mirror)
import uvm_mirror
from tcp_model import Flags, TcpSegment

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


def _err(msg: str):
    _State.errors += 1
    logger.error(msg)


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
