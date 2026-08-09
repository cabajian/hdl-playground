# HDL Tooling Playground

This repository is a vibe-coded playground for HDL-related tooling usage and development. It currently includes setups for:

- **Verilator**: For simulation and linting.
- **Verible**: For SystemVerilog formatting and linting.
- **UVM**: Universal Verification Methodology testbenches.
- **PyHDL-IF**: Python ↔ SystemVerilog integration via DPI.
- **Pytest**: Test orchestration for all simulation variants.

## Designs

- **counter** — a 4-bit counter with register read/write, verified through three
  independent environments (direct SV, UVM, PyHDL-IF) sharing one test pattern.
- **ether** — an Ethernet frame extractor: hunts preamble/SFD in an arbitrary
  byte stream, parses the header (including 802.1Q VLAN), streams the payload
  out, and validates the FCS. Verified by a pure-SV testbench and a
  scapy-driven PyHDL-IF one.
- **TCP co-simulation** — no RTL: two Python TCP model engines exchange data
  through a UVM SystemVerilog transport via PyHDL-IF proxy sequences.

## Getting Started

### Prerequisites

Install the following tools on your system:

- [Verilator](https://verilator.org/guide/latest/install.html) **≥ 5.4x** — 5.020
  cannot preprocess UVM 1.2's `UVM_VERSION_STRING` macro. The `verilator` PyPI
  wheel is a convenient source; note its bundled `verilated.mk` ships with the
  compiler-config variables blank, so `CFG_CXXFLAGS_STD`, `CFG_CXXFLAGS_COROUTINES`
  and `CFG_CXXFLAGS_PCH_I` need filling in.
- [Verible](https://github.com/chipsalliance/verible) (for formatting/linting)
- Python 3.12+
- [UVM 1.2](https://www.accellera.org/downloads/standards/uvm) (via $UVM_HOME, otherwise the default path is: `$HOME/tools/uvm-1.2`)

### Setup

#### Option 1: Automated (using direnv) - Recommended

If you have [direnv](https://direnv.net/) installed:

```bash
# 1. Clone the repo
git clone https://github.com/cabajian/hdl-playground.git
cd hdl-playground

# 2. Allow direnv to setup the environment
# This will automatically create a venv, install dependencies,
# and configure the local git hooks.
direnv allow
```

#### Option 2: Setup script

```bash
git clone https://github.com/cabajian/hdl-playground.git
cd hdl-playground
./scripts/setup_env.sh
```

This creates the venv, installs the pinned toolchain, patches the Verilator
wheel (see below), fetches UVM 1.2, and configures the git hooks. It is
idempotent — re-run it any time, and **always re-run it after `pip install`**,
which silently reverts the Verilator patch.

```bash
./scripts/setup_env.sh --check   # verify an existing setup, change nothing
```

Then export the tool paths it prints (or use direnv, which does it for you).

> **Why the Verilator patch?** The `verilator` PyPI wheel ships `verilated.mk`
> with its compiler-configuration variables empty — they are normally filled in
> by Verilator's `./configure`. Without them the C++ stage fails with confusing
> errors about `std::coroutine_handle` or a missing `__pch.h.fast`. The script
> fills them in idempotently.

### Running Tests

```bash
# Run everything (counter x3, ether x2, tcp x7)
pytest

# One design, or one variant
pytest tests/test_ether.py
pytest -k basic

# TCP co-simulation only (shares one compile across its tests)
pytest tests/test_tcp.py

# VCD waveform output
pytest --waves
```

The TCP tests select a UVM test by name and take stimulus knobs as plusargs;
see [`src/verif/pyhdl/tcp/README.md`](src/verif/pyhdl/tcp/README.md).

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
│   ├── counter.sv               # 4-bit counter DUT
│   └── ether.sv                 # Ethernet frame extractor (MAC RX)
└── verif/
    ├── basic/                   # Direct-test SV testbenches
    │   ├── tb_counter.sv
    │   └── tb_ether_basic.sv
    ├── uvm/                     # UVM env/agent/sequences/scoreboard
    │   └── tb_counter_uvm.sv
    └── pyhdl/
        ├── best_practices.md    # PyHDL-IF practices, pitfalls, debugging
        ├── sim_clock.py         # simpy-backed clock shared by TBs
        ├── sim_logging.py       # sim-time logging that interleaves with SV output
        ├── uvm_mirror.py        # declare queue element widths via a mirror class
        ├── pyhdl_raw.sv         # raw-bytes item transport (seq_item_serializable)
        ├── raw_mirror.py        # Python side of the raw-bytes path
        ├── counter/             # PyHDL-IF counter TB
        ├── ether/               # PyHDL-IF ether TB (scapy stimulus)
        ├── tcp/                 # TCP co-simulation TB (UVM + PyHDL-IF)
        └── tcp_model/           # vendored Python TCP model
tests/
├── conftest.py                  # Pytest fixtures (compile, sim, plusargs, --waves)
├── test_counter.py
├── test_ether.py
└── test_tcp.py
```

## Further reading

- [`src/verif/pyhdl/best_practices.md`](src/verif/pyhdl/best_practices.md) —
  building and debugging PyHDL-IF testbenches: the concurrency rule, Verilator
  and UVM integration gotchas, and a symptom index. **Start here** if you are
  writing a new PyHDL-IF testbench or debugging one.
- [`src/verif/pyhdl/tcp/README.md`](src/verif/pyhdl/tcp/README.md) — the TCP
  co-simulation testbench.
- [`src/verif/pyhdl/tcp/TEST_PLAN.md`](src/verif/pyhdl/tcp/TEST_PLAN.md) — its
  test plan, results and risk log.
