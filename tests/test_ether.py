"""Simulation tests for the ether design."""

import pytest
from conftest import compile_sim, run_sim

def test_pyhdl(waves):
    """Compile and run the PyHDL-IF ether testbench."""
    cfg, comp = compile_sim("pyhdl_ether", waves=waves)
    assert comp.returncode == 0, f"Compilation failed:\n{comp.stderr}"

    sim = run_sim(cfg)
    assert sim.returncode == 0, f"Simulation failed:\n{sim.stderr}"
    assert "Simulation finished in SV." in sim.stdout, "Expected finish message not found"
    assert "$error" not in sim.stdout.lower(), f"Errors found in simulation output:\n{sim.stdout}"
