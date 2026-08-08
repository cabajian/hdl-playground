"""Simulation tests for the TCP model co-simulation testbench.

Two Python tcp_model engines talk to each other through a UVM SystemVerilog
transport, driven by pyhdl-if proxy sequences. All tests share one compiled
binary and select a UVM test by name.
"""

import re
import time

import pytest
from conftest import compile_sim, run_sim

CORE = "tb_tcp_uvm_pyhdl"

# Messages per direction for the data tests
NUM_MSGS = 1000


def _counts(text: str) -> dict:
    """Pull the UVM report-summary severity counts out of a run log."""
    out = {}
    for sev in ("UVM_ERROR", "UVM_FATAL", "UVM_WARNING"):
        m = re.search(rf"^{sev}\s*:\s*(\d+)\s*$", text, re.MULTILINE)
        if m:
            out[sev] = int(m.group(1))
    return out


@pytest.fixture(scope="module")
def tcp_cfg(pytestconfig):
    """Compile the testbench once; every test selects a UVM test by name."""
    cfg, comp = compile_sim(CORE, waves=pytestconfig.getoption("--waves"))
    assert comp.returncode == 0, f"Compilation failed:\n{comp.stderr}"
    return cfg


def _run(uvm_test: str, cfg, plusargs=None):
    args = [f"+UVM_TESTNAME={uvm_test}"] + list(plusargs or [])
    t0 = time.perf_counter()
    sim = run_sim(cfg, plusargs=args)
    elapsed = time.perf_counter() - t0

    combined = sim.stdout + sim.stderr
    assert sim.returncode == 0, f"Simulation failed:\n{combined[-4000:]}"

    counts = _counts(combined)
    assert counts, f"No UVM report summary found:\n{combined[-4000:]}"
    assert counts.get("UVM_FATAL", 1) == 0, f"UVM_FATAL in {uvm_test}:\n{combined[-4000:]}"
    assert counts.get("UVM_ERROR", 1) == 0, f"UVM_ERROR in {uvm_test}:\n{combined[-4000:]}"
    # The Python side logs failures at ERROR level rather than raising
    assert "[ERROR]" not in combined, f"Python-side errors in {uvm_test}:\n{combined[-4000:]}"

    print(f"\n[{uvm_test}] simulation wall-clock: {elapsed:.3f}s")
    return combined


def test_smoke(tcp_cfg):
    """T0: time service, mirror-pinned queue widths, one segment A->B."""
    out = _run("tcp_smoke_test", tcp_cfg)
    assert "Time service OK" in out
    assert "Queue width pinned" in out
    assert "Matched 1/1 segments A->B" in out


def test_transport(tcp_cfg):
    """P2: canned segments both directions, scoreboard checks each stream."""
    out = _run("tcp_transport_test", tcp_cfg)
    assert "Sequential wait_ns OK" in out
    assert "Matched 4/4 segments A->B" in out
    assert "Matched 4/4 segments B->A" in out


def test_handshake(tcp_cfg):
    """T1: three-way handshake, every segment across the SV wire."""
    out = _run("tcp_handshake_test", tcp_cfg)
    assert "Handshake OK" in out
    assert "Matched 2/2 segments A->B" in out  # SYN, ACK
    assert "Matched 1/1 segments B->A" in out  # SYN-ACK


def test_data(tcp_cfg):
    """T2: NUM_MSGS application messages A->B over the connection."""
    out = _run("tcp_data_test", tcp_cfg, [f"+num_msgs={NUM_MSGS}"])
    assert f"Matched {NUM_MSGS}/{NUM_MSGS} messages A->B" in out


def test_bidir(tcp_cfg):
    """T3: NUM_MSGS messages in each direction, interleaved."""
    out = _run("tcp_bidir_test", tcp_cfg, [f"+num_msgs={NUM_MSGS}"])
    assert f"Matched {NUM_MSGS}/{NUM_MSGS} messages A->B" in out
    assert f"Matched {NUM_MSGS}/{NUM_MSGS} messages B->A" in out


def test_teardown(tcp_cfg):
    """T4: graceful close; both sides reach CLOSED (one via TIME-WAIT)."""
    out = _run("tcp_teardown_test", tcp_cfg, ["+num_msgs=5"])
    assert "Teardown OK: both CLOSED" in out
    assert "Matched 5/5 messages A->B before teardown" in out


def test_loss(tcp_cfg):
    """T5: dropped data segments are recovered by retransmission."""
    out = _run("tcp_loss_test", tcp_cfg, ["+num_msgs=5"])
    assert "Dropped A->B segments" in out
    assert re.search(r"Matched 5/5 messages A->B after \d+ dropped segment", out), out[-2000:]
