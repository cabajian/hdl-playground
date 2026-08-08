"""Ether testbench driver.

Builds Ethernet frames with scapy, wraps each one in the framing a real MAC
would see on the wire (preamble, SFD, trailing FCS, plus some non-frame filler
in between), and streams the raw bytes at the DUT. The SV monitor hands every
frame the DUT extracts back here, where it is compared against the scapy
original byte-for-byte.
"""

import hdl_if as hif
import random
import struct
import zlib
from scapy.all import Ether, Dot1Q, Raw

import typing

import sim_logging
from sim_clock import SimClock, SimClockAPI  # noqa: F401 (registers SimClockAPI)

# Simulation time is stamped onto every record, so it interleaves with the SV
# output rather than trailing it in a block. See sim_logging for why.
logger = sim_logging.configure(lambda: SimClock.inst().now_ns(), name="ether")

# 7 bytes of preamble followed by the start-of-frame delimiter
PREAMBLE = bytes([0x55] * 7) + bytes([0xD5])

NUM_PACKETS = 1000
MAX_PAYLOAD = 1500
MIN_FRAME_BYTES = 64  # DST..FCS, for 802.3 padding

FLAVOUR_ETH2 = 0
FLAVOUR_DOT3 = 1
FLAVOUR_VLAN = 2


def _rand_mac() -> str:
    return ":".join(f"{random.randint(0, 255):02x}" for _ in range(6))


def _rand_bytes(n: int) -> bytes:
    return bytes(random.getrandbits(8) for _ in range(n))


def _inter_frame_filler() -> bytes:
    """Non-frame bytes the DUT must skip over.

    0x55 is excluded so the filler can never be mistaken for a preamble.
    """
    return bytes(b for b in _rand_bytes(random.randint(0, 8)) if b != 0x55)


def build_stream(flavour: int) -> typing.Tuple[bytes, bytes]:
    """Return (expected_frame, wire_stream) for one randomly generated frame.

    expected_frame is the frame body as scapy serializes it -- exactly what the
    DUT is expected to reconstruct. wire_stream is what gets clocked in.
    """
    src, dst = _rand_mac(), _rand_mac()
    payload_len = random.randint(0, MAX_PAYLOAD)
    payload = _rand_bytes(payload_len)
    pad = b""

    if flavour == FLAVOUR_ETH2:
        etype = random.choice([0x0800, 0x86DD, 0x0806, 0x9000])
        pkt = Ether(src=src, dst=dst, type=etype) / Raw(payload)
    elif flavour == FLAVOUR_DOT3:
        # The type field carries the payload length, so the frame may be padded
        # out to the 64-byte minimum. The pad sits outside the length field and
        # must not come back as payload.
        pkt = Ether(src=src, dst=dst, type=payload_len) / Raw(payload)
        pad = _rand_bytes(max(0, MIN_FRAME_BYTES - 4 - (14 + payload_len)))
    else:
        pkt = (Ether(src=src, dst=dst)
               / Dot1Q(prio=random.randrange(8), id=random.randrange(2),
                       vlan=random.randrange(1, 4095), type=0x0800)
               / Raw(payload))

    frame = bytes(pkt)
    body = frame + pad  # everything the FCS covers
    fcs = struct.pack("<I", zlib.crc32(body))

    return frame, _inter_frame_filler() + PREAMBLE + body + fcs


@hif.api
class TestAPI(object):
    @hif.imp
    async def drive(self, packet: typing.List):
        pass


@hif.api
class TestRunnerAPI(object):

    def __init__(self):
        self._expected: bytes = b""
        self._num_matches = 0
        self._num_checked = 0
        self._clock = SimClock.inst()
        logger.info("Initialized ether test runner")

    @hif.exp
    def check_packet(self, py_list: typing.List):
        received = bytes(py_list)
        self._num_checked += 1
        if received != self._expected:
            logger.error(
                "Frames did not match.\n"
                f"{'Expected: '.ljust(10)}{self._expected.hex()}\n"
                f"{'Received: '.ljust(10)}{received.hex()}"
            )
        else:
            self._num_matches += 1

    @hif.exp
    async def start_test(self, api: TestAPI):
        for i in range(NUM_PACKETS):
            flavour = i % 3
            frame, stream = build_stream(flavour)
            self._expected = frame

            logger.info(
                f"Sending frame {i} "
                f"(flavour={flavour}, frame={len(frame)}B, stream={len(stream)}B)..."
            )
            await api.drive(list(stream))

        if self._num_checked != NUM_PACKETS:
            logger.error(
                f"Error: DUT extracted {self._num_checked} frames, "
                f"expected {NUM_PACKETS}!"
            )
        if self._num_matches != NUM_PACKETS:
            logger.error(f"Error: matched {self._num_matches}/{NUM_PACKETS} packets!")
        else:
            logger.info(f"Matched {self._num_matches}/{NUM_PACKETS} packets")
