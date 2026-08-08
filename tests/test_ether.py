"""Simulation tests for the ether design."""

import time

import pytest
from conftest import compile_sim, run_sim


def _run_variant(variant: str, waves: bool):
    cfg, comp = compile_sim(variant, waves=waves)
    assert comp.returncode == 0, f"Compilation failed:\n{comp.stderr}"

    t0 = time.perf_counter()
    sim = run_sim(cfg)
    elapsed = time.perf_counter() - t0

    # run_sim folds stderr into stdout, so this is the whole interleaved log.
    combined = sim.stdout
    assert sim.returncode == 0, f"Simulation failed:\n{combined[-4000:]}"
    assert "Simulation finished in SV." in combined, "Expected finish message not found"
    assert "$error" not in combined.lower(), f"Errors found in simulation output:\n{combined}"
    assert "PY_ERROR" not in combined, f"Python-side errors in simulation output:\n{combined}"
    assert "Matched 1000/1000 packets" in combined, \
        f"Expected 1000/1000  packet match:\n{combined}"

    print(f"\n[{variant}] simulation wall-clock: {elapsed:.3f}s")
    return elapsed


def test_basic(waves):
    """Compile and run the pure-SV ether testbench (no Python)."""
    _run_variant("tb_ether_basic", waves)


def test_pyhdl(waves):
    """Compile and run the PyHDL-IF ether testbench."""
    _run_variant("tb_ether_pyhdl", waves)
