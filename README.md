# HDL Tooling Playground

This repository is a vibe-coded playground for HDL-related tooling usage and development. It currently includes setups for:

- **Verilator**: For simulation and linting.
- **Verible**: For SystemVerilog formatting and linting.
- **UVM**: Universal Verification Methodology testbenches.
- **PyHDL-IF**: Python ↔ SystemVerilog integration via DPI.
- **Pytest**: Test orchestration for all simulation variants.

## Design

A simple 4-bit counter with register read/write capability, verified through three independent test environments that share the same test pattern.

## Getting Started

### Prerequisites
- Verilator (≥ 5.x with `--timing` support)
- Verible (for formatting/linting)
- Python 3.12+ virtual environment (at project root)
- UVM 1.2 (installed at `~/tools/uvm-1.2`)
- [pyhdl-if](https://github.com/fvutils/pyhdl-if) (installed in the venv)

### Running Tests

```bash
# Run all tests (basic, uvm, pyhdl)
./bin/pytest

# Run a specific test
./bin/pytest -k basic

# Run with VCD waveform output
./bin/pytest --waves
```

### Utilities

```bash
# Format SystemVerilog files
make format

# Lint SystemVerilog files
make lint

# Clean build artifacts
make clean
```

## Project Structure

```
src/
├── rtl/
│   └── counter.sv              # 4-bit counter DUT
└── verif/
    ├── basic/
    │   └── tb_counter.sv        # Direct-test testbench
    ├── uvm/
    │   ├── tb_counter_uvm.sv    # UVM top module
    │   ├── counter_test.sv      # UVM test
    │   └── ...                  # UVM env, agent, sequences, scoreboard
    └── pyhdl/
        ├── tb_counter_pyhdl.sv  # PyHDL-IF testbench
        ├── counter_test_pkg.sv  # SV test class
        ├── simple_print.py      # Python test driver
        └── best_practices.md    # PyHDL-IF lessons learned
tests/
├── conftest.py                  # Pytest fixtures (compile, sim, --waves)
└── test_counter.py              # Test functions for all 3 variants
```
