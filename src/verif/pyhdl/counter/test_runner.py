import hdl_if as hif
import ctypes as ct

import sim_logging

# This testbench exposes no time service to Python, so records carry "@ ?ns"
# rather than a simulation timestamp. The handler still matters: it drains the
# simulator's C stdio before each record, without which every Python line lands
# in the log after every SV line. `print(flush=True)` does not do this -- it
# flushes Python's buffer, not Verilator's. See ../best_practices.md 6.1.
logger = sim_logging.configure(name="counter")


@hif.api
class TestAPI(object):
    @hif.imp
    async def run_test(self, starting_count: ct.c_ubyte):
        pass


@hif.api
class TestRunnerAPI(object):

    def __init__(self):
        logger.info("Initialized test runner")

    @hif.exp
    async def start_test(self, api: ct.py_object):
        for i in range(16):
            logger.info(f"Running test {i}")
            await api.run_test(i)
            logger.info(f"Finished test {i}")
