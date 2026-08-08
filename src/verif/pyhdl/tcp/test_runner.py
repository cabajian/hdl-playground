"""Python side of the TCP co-simulation testbench.

Defines the Call-API surface (time service, runner) and the session sequences
that run as the body of pyhdl-if's shipped UVM sequence proxy. The TCP model
(tcp_model package) provides the engines; SystemVerilog provides transport.
"""

import logging
import typing

import hdl_if as hif
from hdl_if.uvm import uvm_sequence_impl

from tcp_model import Flags, TcpSegment

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Must match tcp_verif_pkg's TCP_TB_MSS / TCP_OPT_MAX
TB_MSS = 256
OPT_MAX = 40

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
# Item fill: SV populates the sequence's current request from wire bytes.
#
# The UVM wrapper's own field transport (req.pack()/req.unpack()) does not
# round-trip against UVM 2020.3.1 -- layout discovery from sprint() is correct,
# but the pack_ints() bitstream slicing is misaligned, so a freshly created,
# all-zero item reads back with non-zero fields. Handing the segment over as a
# byte list (the crossing the ether testbench already relies on) sidesteps it.
# The session sequence still calls create_req/start_item/finish_item itself.
# ---------------------------------------------------------------------------
@hif.api
class TcpItemAPI(object):
    @hif.imp
    def fill(self, data: typing.List): ...


# ---------------------------------------------------------------------------
# Shared state between the runner API and the session sequences
# ---------------------------------------------------------------------------
class _State:
    ts: typing.Optional[TimeServiceAPI] = None
    item_api: typing.Dict[int, TcpItemAPI] = {}
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
    def init_item_api(self, side: int, api: TcpItemAPI):
        _State.item_api[side] = api
        logger.info(f"Item API registered for side {side}")

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

            fill = _State.item_api.get(SIDE_A)
            if fill is None:
                _err("SmokeSeq: item API not registered for side A")
                return

            req = self.proxy.create_req()
            fill.fill(list(_State.smoke_expected))
            await self.proxy.start_item(req)
            await self.proxy.finish_item(req)
            logger.info(f"SmokeSeq: sent {len(_State.smoke_expected)}-byte segment")
        except Exception as e:
            _err(f"SmokeSeq raised: {type(e).__name__}: {e}")
