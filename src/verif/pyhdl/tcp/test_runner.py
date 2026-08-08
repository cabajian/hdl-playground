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
    rx_count = [0, 0]
    smoke_expected: bytes = b""
    smoke_matched: int = 0


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
        seg = bytes(data)
        _State.rx_count[side] += 1
        logger.info(f"rx_segment side={side} len={len(seg)}")
        if _State.smoke_expected:
            if side == SIDE_B and seg == _State.smoke_expected:
                _State.smoke_matched += 1
            else:
                _err(
                    f"Smoke segment mismatch (side={side}).\n"
                    f"Expected: {_State.smoke_expected.hex()}\n"
                    f"Received: {seg.hex()}"
                )

    @hif.exp
    def report(self) -> int:
        if _State.smoke_expected:
            total = _State.smoke_matched
            logger.info(f"Matched {total}/1 segments")
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
# T0 smoke sequence: time-service check, then one canned segment through the
# full proxy path. The far-side monitor hands the bytes back to rx_segment,
# which compares against the tcp_model codec's build() image.
# ---------------------------------------------------------------------------
def _smoke_segment() -> bytes:
    seg = TcpSegment(
        src_port=0x1234,
        dst_port=0x4321,
        seq=0x01020304,
        ack=0x05060708,
        flags=Flags.SYN | Flags.ACK,
        window=0xBEEF,
        mss=1460,
        payload=bytes(range(16)),
    )
    return seg.build()


class SmokeSeq(uvm_sequence_impl):

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

            _State.smoke_expected = _smoke_segment()

            # Field transport: read-modify-write through the UVM wrapper.
            req = self.proxy.create_req()

            # Pin queue element widths before any pack/unpack, so pyhdl-if
            # never falls back to inferring them from the data.
            applied = uvm_mirror.bind(req, tcp_item_mirror.tcp_item)
            logger.info(f"Mirror bound: queue element widths {applied}")

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
            back = req.pack()
            if list(back.payload) != [0] * 16:
                _err(f"queue width not pinned: all-zero payload came back as {back.payload!r}")
            else:
                logger.info("Queue width pinned: 16 zero bytes survived round-trip")

            _apply_segment(v, _State.smoke_expected)
            req.unpack(v)

            # Round-trip: what we wrote must read back identically
            diff = _snapshot_diff(v, req.pack())
            if diff:
                _err(f"field transport round-trip mismatch on: {', '.join(diff)}")
            else:
                logger.info("Field transport round-trip OK (all %d fields)" % len(_SNAPSHOT_FIELDS))

            await self.proxy.start_item(req)
            await self.proxy.finish_item(req)
            logger.info(f"SmokeSeq: sent {len(_State.smoke_expected)}-byte segment")
        except Exception as e:
            _err(f"SmokeSeq raised: {type(e).__name__}: {e}")
