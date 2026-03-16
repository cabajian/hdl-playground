import hdl_if as hif
import ctypes as ct
import random
from scapy.all import Packet, Ether

import typing
import logging

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
        self._last_pkt: Packet
        self._num_matches = 0
        self._sim_time_ns = 0
        logger.info(f"[{self._sim_time_ns}ns] Initialized ether test runner")

    @hif.exp
    def set_sim_time(self, t: int):
        self._sim_time_ns = t

    @hif.exp
    def check_packet(self, py_list: typing.List):
        logger.info(f"[{self._sim_time_ns}ns] Received packet from SV. Verifying...")
        # Reconstruct packet
        byte_data = bytes(py_list)
        eth = Ether(byte_data)
        # Compare packets
        if (eth != self._last_pkt):
            logger.error(f"[{self._sim_time_ns}ns] Packets did not match.\n{'Expected: '.ljust(10)}{bytes(eth)}\n{'Actual: '.ljust(10)}{bytes(self._last_pkt)}")
        else:
            self._num_matches += 1

    @hif.exp
    async def start_test(self, api: TestAPI):
        num_packets = 100
        for i in range(num_packets):
            # Generate random MAC addresses
            mac_src = ":".join([f"{random.randint(0, 255):02x}" for _ in range(6)])
            mac_dst = ":".join([f"{random.randint(0, 255):02x}" for _ in range(6)])
            # Generate random payload
            payload_len = random.randint(0, 1500)
            payload = bytes([random.randint(0, 255) for _ in range(payload_len)])
            # Construct packet
            pkt = Ether(src=mac_src, dst=mac_dst) / payload
            raw_pkt = list(bytes(pkt))
            self._last_pkt = pkt
            # Send packet
            logger.info(f"[{self._sim_time_ns}ns] Sending test packet {i}: {pkt} (length:{len(raw_pkt)})...")
            await api.drive(raw_pkt)
            logger.info(f"[{self._sim_time_ns}ns] Finished sending test packet {i}")

        if (self._num_matches != num_packets):
            logger.error(f"[{self._sim_time_ns}ns] Error: matched {self._num_matches}/{num_packets} packets!")
        else:
            logger.info(f"[{self._sim_time_ns}ns] Matched {self._num_matches}/{num_packets} packets")
