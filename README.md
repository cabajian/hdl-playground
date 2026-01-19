# HDL Tooling Playground

This repository is a playground for HDL-related tooling usage and development, developed using **Antigravity**. It currently includes setups for:

- **Verilator**: For simulation and linting.
- **Verible**: For SystemVerilog formatting and linting.
- **Makefiles**: To automate the build, simulation, formatting, and linting workflows.

## Getting Started

### Prerequisites
- Verilator
- Verible (installed in `~/tools/verible`)

### Usage
- `make compile`: Compile the design with Verilator.
- `make sim`: Run the simulation and pipe output to `build/sim.log`.
- `make format`: Format SystemVerilog files using Verible.
- `make lint`: Lint SystemVerilog files using Verible.
- `make clean`: Remove build artifacts.
