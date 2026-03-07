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

Install the following tools on your system:

- [Verilator](https://verilator.org/guide/latest/install.html) ≥ 5.x (with `--timing` support)
- [Verible](https://github.com/chipsalliance/verible) (for formatting/linting)
- Python 3.12+
- [UVM 1.2](https://www.accellera.org/downloads/standards/uvm) (default path: `~/tools/uvm-1.2`)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/cabajian/hdl-playground.git
cd hdl-playground

# 2. Create and activate a Python virtual environment
python3 -m venv .
source bin/activate

# 3. Install Python dependencies
pip install pyhdl-if pytest pytest-timeout

# 4. Enable the pre-commit formatting hook
git config core.hooksPath .githooks
```

### Running Tests

```bash
# Run all tests (basic, uvm, pyhdl)
pytest

# Run a specific test
pytest -k basic

# Run with VCD waveform output
pytest --waves
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
