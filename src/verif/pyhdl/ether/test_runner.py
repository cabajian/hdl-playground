import hdl_if as hif
import random
from scapy.all import Ether

import typing
import logging

from sim_clock import SimClock, SimClockAPI  # noqa: F401 (registers SimClockAPI)

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


@hif.api
class TestAPI(object):
    @hif.imp
    async def drive(self, packet: typing.List):
        pass


@hif.api
class TestRunnerAPI(object):

    def __init__(self):
        self._last_pkt_bytes: bytes = b""
        self._num_matches = 0
        self._clock = SimClock.inst()
        logger.info(f"[{self._clock.now_ns()}ns] Initialized ether test runner")

    def _t(self) -> str:
        return f"[{self._clock.now_ns()}ns]"

    @hif.exp
    def check_packet(self, py_list: typing.List):
        logger.info(f"{self._t()} Received packet from SV. Verifying...")
        received = bytes(py_list)
        if received != self._last_pkt_bytes:
            logger.error(
                f"{self._t()} Packets did not match.\n"
                f"{'Expected: '.ljust(10)}{self._last_pkt_bytes.hex()}\n"
                f"{'Received: '.ljust(10)}{received.hex()}"
            )
        else:
            self._num_matches += 1

    @hif.exp
    async def start_test(self, api: TestAPI):
        num_packets = 1000
        for i in range(num_packets):
            mac_src = ":".join([f"{random.randint(0, 255):02x}" for _ in range(6)])
            mac_dst = ":".join([f"{random.randint(0, 255):02x}" for _ in range(6)])
            payload_len = random.randint(0, 1500)
            payload = bytes([random.randint(0, 255) for _ in range(payload_len)])
            pkt = Ether(src=mac_src, dst=mac_dst) / payload
            raw_pkt = list(bytes(pkt))
            self._last_pkt_bytes = bytes(pkt)

            logger.info(f"{self._t()} Sending test packet {i}: {pkt} (length:{len(raw_pkt)})...")
            await api.drive(raw_pkt)
            logger.info(f"{self._t()} Finished sending test packet {i}")

        if self._num_matches != num_packets:
            logger.error(f"{self._t()} Error: matched {self._num_matches}/{num_packets} packets!")
        else:
            logger.info(f"{self._t()} Matched {self._num_matches}/{num_packets} packets")
