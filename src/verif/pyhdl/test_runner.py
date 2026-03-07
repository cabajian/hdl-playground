import hdl_if as hif
import ctypes as ct


@hif.api
class TestAPI(object):
    @hif.imp
    async def run_test(self, starting_count: ct.c_ubyte):
        pass


@hif.api
class TestRunnerAPI(object):

    def __init__(self):
        print("[Python] Initialized test runner", flush=True)

    @hif.exp
    async def start_test(self, api: ct.py_object):
        for i in range(16):
            print(f"[Python] Running test {i}", flush=True)
            await api.run_test(i)
            print(f"[Python] Finished test {i}", flush=True)
