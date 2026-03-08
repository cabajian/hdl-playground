"""Simulation tests for the counter design."""

import pytest
from conftest import compile_sim, run_sim


# ---------------------------------------------------------------------------
# Basic test
# ---------------------------------------------------------------------------

def test_basic(waves):
    """Compile and run the basic (non-UVM, non-PyHDL) testbench."""
    cfg, comp = compile_sim("basic", waves=waves)
    assert comp.returncode == 0, f"Compilation failed:\n{comp.stderr}"

    sim = run_sim(cfg)
    assert sim.returncode == 0, f"Simulation failed:\n{sim.stderr}"
    assert "Simulation finished." in sim.stdout, "Expected finish message not found in sim output"


# ---------------------------------------------------------------------------
# UVM test
# ---------------------------------------------------------------------------

def test_uvm(waves):
    """Compile and run the UVM testbench."""
    cfg, comp = compile_sim("uvm", waves=waves)
    assert comp.returncode == 0, f"Compilation failed:\n{comp.stderr}"

    sim = run_sim(cfg)
    assert sim.returncode == 0, f"Simulation failed:\n{sim.stderr}"
    assert "UVM_ERROR :    0" in sim.stdout, f"UVM errors detected:\n{sim.stdout}"
    assert "UVM_FATAL :    0" in sim.stdout, f"UVM fatals detected:\n{sim.stdout}"


# ---------------------------------------------------------------------------
# PyHDL-IF test
# ---------------------------------------------------------------------------

def test_pyhdl(waves):
    """Compile and run the PyHDL-IF testbench."""
    cfg, comp = compile_sim("pyhdl_counter", waves=waves)
    assert comp.returncode == 0, f"Compilation failed:\n{comp.stderr}"

    sim = run_sim(cfg)
    assert sim.returncode == 0, f"Simulation failed:\n{sim.stderr}"
    assert "Simulation finished in SV." in sim.stdout, "Expected finish message not found"
    assert "$error" not in sim.stdout.lower(), f"Errors found in simulation output:\n{sim.stdout}"
